"""SSRF protections, limits and log redaction (T-17, T-19, T-20, T-30, T-34)."""

import asyncio
import logging

import httpx
import pytest

from app.main import RedactingFilter
from app.net import FetchError, SafeFetcher, TooLargeError, UnsafeURLError, check_url_syntax, resolve_and_check


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://localhost:8000/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://0.0.0.0/", "file:///etc/passwd",
    "gopher://example.org/", "http://user:pw@example.org/", "http://example.org:8080/", "http://intranet/",
    "http://printer.local/", "http://[::ffff:127.0.0.1]/",
])
def test_unsafe_urls_blocked(url):  # T-20
    with pytest.raises(UnsafeURLError):
        check_url_syntax(url)


def test_dns_resolving_to_private_is_blocked():  # T-20 (DNS)
    async def fake(host, port):
        return ["10.1.2.3"]
    with pytest.raises(UnsafeURLError):
        asyncio.run(resolve_and_check("evil.example.com", 443, fake))


async def _public(host, port):
    return ["93.184.216.34"]


def _collect(fetcher, url, **kw):
    async def run():
        chunks = []
        async for res, chunk in fetcher.stream(url, **kw):
            chunks.append(chunk)
        return b"".join(chunks)
    return asyncio.run(run())


def test_redirect_to_private_address_blocked():  # T-19
    def handler(req):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
    f = SafeFetcher(resolver=_public, transport=httpx.MockTransport(handler), pin_ip=False)
    with pytest.raises(UnsafeURLError):
        _collect(f, "https://example.org/paper.pdf")


def test_too_many_redirects():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://example.org/loop"})
    f = SafeFetcher(resolver=_public, transport=httpx.MockTransport(handler), pin_ip=False, max_redirects=3)
    with pytest.raises(UnsafeURLError):
        _collect(f, "https://example.org/start")


def test_size_limit_enforced():  # T-17
    big = b"%PDF-" + b"0" * 5000
    f = SafeFetcher(resolver=_public, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big)),
                    pin_ip=False)
    with pytest.raises(TooLargeError):
        _collect(f, "https://example.org/big.pdf", max_bytes=1000)


def test_ip_pinning_keeps_host_and_sni():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["host"] = req.headers["host"]
        seen["sni"] = req.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"ok")
    f = SafeFetcher(resolver=_public, transport=httpx.MockTransport(handler), pin_ip=True)
    assert _collect(f, "https://example.org/a.pdf") == b"ok"
    assert seen["url"].startswith("https://93.184.216.34/")
    assert seen["host"] == "example.org" and seen["sni"] == "example.org"


def test_http_error_reported():
    f = SafeFetcher(resolver=_public, transport=httpx.MockTransport(lambda r: httpx.Response(403)), pin_ip=False)
    with pytest.raises(FetchError):
        _collect(f, "https://example.org/a.pdf")


def test_secrets_redacted_in_logs():  # T-30
    rec = logging.LogRecord("x", logging.INFO, __file__, 1,
                            "GET https://api.openalex.org/works?api_key=SECRET123&mailto=me@x.org x-api-key: abc "
                            "sk-ant-api03-zzz", (), None)
    RedactingFilter().filter(rec)
    msg = rec.getMessage()
    for secret in ("SECRET123", "me@x.org", "abc", "sk-ant-api03-zzz"):
        assert secret not in msg
