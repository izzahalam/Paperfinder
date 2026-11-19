"""Network layer.

Two clients with different trust levels:

* ``ProviderClient`` – talks only to the fixed, reviewed provider API hosts.
  Adds timeouts, bounded retries with backoff + jitter, Retry-After handling,
  a per-provider concurrency cap / minimum interval and a simple circuit
  breaker (SRS FR-82..85, R-16, R-17).

* ``SafeFetcher`` – fetches *arbitrary* URLs (publisher/repository PDFs,
  user-supplied landing pages). It enforces SSRF protections (SRS NFR-13,
  §11, R-06): http/https only, standard ports, every resolved address must be
  public, redirects followed manually and re-validated per hop, size and time
  caps, and (when no proxy is configured) the connection is pinned to the
  vetted IP to defeat DNS rebinding.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import socket
import time
import urllib.request
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from . import __version__

log = logging.getLogger("paperfinder.net")


def user_agent(contact_email: str = "") -> str:
    ua = f"PaperFinderAI/{__version__} (local research utility)"
    if contact_email:
        ua += f" mailto:{contact_email}"
    return ua


# ======================================================================
# Errors
# ======================================================================


class FetchError(Exception):
    """A fetch failed in a way that should be reported to the user."""

    def __init__(self, message: str, category: str = "network"):
        super().__init__(message)
        self.category = category


class UnsafeURLError(FetchError):
    def __init__(self, message: str):
        super().__init__(message, category="unsafe_url")


class RateLimitedError(FetchError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message, category="rate_limited")
        self.retry_after = retry_after


class TooLargeError(FetchError):
    def __init__(self, message: str):
        super().__init__(message, category="too_large")


# ======================================================================
# URL safety
# ======================================================================

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}
BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return is_public_ip(ip.ipv4_mapped)
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour:
        return is_public_ip(ip.sixtofour)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def check_url_syntax(url: str) -> tuple[str, str, int | None]:
    """Validate scheme/host/port. Returns (scheme, host, port)."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise UnsafeURLError(f"Malformed URL: {e}") from None
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Blocked URL scheme '{scheme or '(none)'}' – only http/https are allowed.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL has no host.")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError(f"Blocked non-standard port {port}.")
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURLError(f"Blocked local host name '{host}'.")
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None and not is_public_ip(ip):
        raise UnsafeURLError(f"Blocked private/reserved address {ip}.")
    if ip is None and "." not in host:
        raise UnsafeURLError(f"Blocked single-label host name '{host}'.")
    return scheme, host, port


Resolver = Callable[[str, int], "asyncio.Future[list[str]]"]


async def default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def resolve_and_check(host: str, port: int, resolver=default_resolver) -> list[str]:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        addrs = [str(ip)]
    except ValueError:
        try:
            addrs = await asyncio.wait_for(resolver(host, port), timeout=10)
        except (OSError, asyncio.TimeoutError) as e:
            raise FetchError(f"Could not resolve host '{host}': {e}", "dns") from None
    if not addrs:
        raise FetchError(f"Host '{host}' did not resolve.", "dns")
    for a in addrs:
        if not is_public_ip(ipaddress.ip_address(a)):
            raise UnsafeURLError(f"Host '{host}' resolves to a private/reserved address ({a}).")
    return addrs


def proxy_configured() -> bool:
    return bool(urllib.request.getproxies())


@dataclass
class FetchResult:
    final_url: str
    status_code: int
    headers: httpx.Headers
    redirects: list[str] = field(default_factory=list)


class SafeFetcher:
    """Fetch untrusted URLs with SSRF protections and hard limits."""

    def __init__(self, contact_email: str = "", timeout: float = 30.0, max_redirects: int = 5,
                 resolver=default_resolver, transport: httpx.AsyncBaseTransport | None = None,
                 pin_ip: bool | None = None):
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.resolver = resolver
        self.contact_email = contact_email
        self._transport = transport
        # With an outbound proxy the proxy does DNS; pinning to an IP is not
        # possible, but we still validate every hop before connecting.
        self.pin_ip = (not proxy_configured()) if pin_ip is None else pin_ip

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict = dict(
            timeout=httpx.Timeout(self.timeout, connect=min(10.0, self.timeout)),
            follow_redirects=False,
            headers={"User-Agent": user_agent(self.contact_email)},
        )
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _prepare(self, url: str) -> tuple[str, dict, dict]:
        """Validate a hop. Returns (request_url, headers, extensions)."""
        scheme, host, port = check_url_syntax(url)
        eff_port = port or (443 if scheme == "https" else 80)
        if not self.pin_ip:
            # Behind an outbound proxy the proxy resolves names. Still refuse
            # any name that resolves locally to a private/reserved address.
            try:
                await resolve_and_check(host, eff_port, self.resolver)
            except UnsafeURLError:
                raise
            except FetchError:
                pass  # local DNS unavailable; the proxy resolves the name
            return url, {}, {}
        addrs = await resolve_and_check(host, eff_port, self.resolver)
        # Pin the connection to the vetted address; keep Host header and TLS SNI
        # (and therefore certificate verification) bound to the original name.
        parts = urlsplit(url)
        ip = ipaddress.ip_address(addrs[0])
        ip_host = f"[{ip}]" if ip.version == 6 else str(ip)
        netloc = ip_host + (f":{port}" if port else "")
        pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
        headers = {"Host": parts.netloc.rsplit("@", 1)[-1]}
        ext = {"sni_hostname": host} if scheme == "https" else {}
        return pinned, headers, ext

    async def stream(self, url: str, accept: str = "*/*", max_bytes: int = 50 * 1024 * 1024,
                     ) -> AsyncIterator[tuple[FetchResult | None, bytes]]:
        """Yield (result, b"") once headers arrive, then (None, chunk) for body chunks."""
        redirects: list[str] = []
        current = url
        async with self._client() as client:
            for _hop in range(self.max_redirects + 1):
                req_url, headers, ext = await self._prepare(current)
                headers["Accept"] = accept
                req = client.build_request("GET", req_url, headers=headers, extensions=ext)
                try:
                    resp = await client.send(req, stream=True)
                except httpx.TimeoutException:
                    raise FetchError("The server took too long to respond.", "timeout") from None
                except httpx.HTTPError as e:
                    raise FetchError(f"Connection failed: {type(e).__name__}", "network") from None
                try:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            raise FetchError("Redirect without a Location header.", "http")
                        nxt = urljoin(current, loc)
                        redirects.append(nxt)
                        current = nxt
                        continue
                    if resp.status_code == 429:
                        raise RateLimitedError("The server is rate-limiting requests (HTTP 429).")
                    if resp.status_code >= 400:
                        raise FetchError(f"The server returned HTTP {resp.status_code}.", "http")
                    clen = resp.headers.get("content-length")
                    if clen and clen.isdigit() and int(clen) > max_bytes:
                        raise TooLargeError(
                            f"File is {int(clen) / 1e6:.1f} MB, above the {max_bytes / 1e6:.0f} MB limit."
                        )
                    yield FetchResult(current, resp.status_code, resp.headers, redirects), b""
                    received = 0
                    try:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            received += len(chunk)
                            if received > max_bytes:
                                raise TooLargeError(
                                    f"Download exceeded the {max_bytes / 1e6:.0f} MB limit."
                                )
                            yield None, chunk
                    except httpx.TimeoutException:
                        raise FetchError("The download stalled and timed out.", "timeout") from None
                    except httpx.HTTPError as e:
                        raise FetchError(f"Download interrupted: {type(e).__name__}", "network") from None
                    return
                finally:
                    await resp.aclose()
            raise UnsafeURLError(f"Too many redirects (limit {self.max_redirects}).")

    async def fetch_text(self, url: str, max_bytes: int = 2 * 1024 * 1024) -> tuple[FetchResult, str]:
        """Fetch a small text/HTML document (e.g. a landing page) safely."""
        result: FetchResult | None = None
        buf = bytearray()
        async for res, chunk in self.stream(url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                                            max_bytes=max_bytes):
            if res is not None:
                result = res
            else:
                buf.extend(chunk)
        assert result is not None
        charset = result.headers.get_encoding() if hasattr(result.headers, "get_encoding") else None
        return result, bytes(buf).decode(charset or "utf-8", errors="replace")


# ======================================================================
# Provider client
# ======================================================================


@dataclass
class _ProviderState:
    sem: asyncio.Semaphore
    min_interval: float
    last_call: float = 0.0
    consecutive_failures: int = 0
    open_until: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ProviderClient:
    """Shared HTTP client for provider APIs with polite rate limiting."""

    # Conservative defaults per provider (requests in flight, seconds between calls)
    LIMITS = {
        "crossref": (3, 0.1),
        "openalex": (3, 0.1),
        "semanticscholar": (1, 1.1),  # unauthenticated: ~1 req/s shared pool
        "arxiv": (1, 3.0),  # arXiv asks for one request every 3 seconds
        "unpaywall": (2, 0.1),
    }
    BREAKER_THRESHOLD = 3
    BREAKER_COOLDOWN = 60.0

    def __init__(self, contact_email: str = "", timeout: float = 15.0, max_retries: int = 2,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.timeout = timeout
        self.max_retries = max_retries
        kwargs: dict = dict(
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            headers={"User-Agent": user_agent(contact_email)},
            follow_redirects=True,
            max_redirects=3,
        )
        if transport is not None:
            kwargs["transport"] = transport
        self.http = httpx.AsyncClient(**kwargs)
        self._state: dict[str, _ProviderState] = {}

    def _st(self, provider: str) -> _ProviderState:
        if provider not in self._state:
            conc, interval = self.LIMITS.get(provider, (2, 0.2))
            self._state[provider] = _ProviderState(asyncio.Semaphore(conc), interval)
        return self._state[provider]

    def breaker_open(self, provider: str) -> bool:
        return self._st(provider).open_until > time.monotonic()

    async def aclose(self) -> None:
        await self.http.aclose()

    async def get(self, provider: str, url: str, *, params: dict | None = None,
                  headers: dict | None = None, deadline: float | None = None,
                  max_bytes: int = 10 * 1024 * 1024, attempts_cb: Callable[[], None] | None = None,
                  ok_404: bool = False) -> httpx.Response:
        st = self._st(provider)
        if self.breaker_open(provider):
            raise FetchError("Temporarily paused after repeated failures (circuit breaker).", "circuit_open")
        last_err: FetchError | None = None
        for attempt in range(self.max_retries + 1):
            if deadline is not None and time.monotonic() >= deadline:
                raise FetchError("Search time budget exhausted.", "timeout")
            if attempts_cb:
                attempts_cb()
            async with st.sem:
                async with st.lock:
                    wait = st.min_interval - (time.monotonic() - st.last_call)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    st.last_call = time.monotonic()
                timeout = self.timeout
                if deadline is not None:
                    timeout = max(0.5, min(timeout, deadline - time.monotonic()))
                try:
                    resp = await self.http.get(url, params=params, headers=headers, timeout=timeout)
                except httpx.TimeoutException:
                    last_err = FetchError("Request timed out.", "timeout")
                    resp = None
                except httpx.HTTPError as e:
                    last_err = FetchError(f"Connection error ({type(e).__name__}).", "network")
                    resp = None
            if resp is not None:
                if len(resp.content) > max_bytes:
                    raise TooLargeError("Provider response exceeded size limit.")
                if resp.status_code == 429:
                    ra = _retry_after(resp)
                    last_err = RateLimitedError(_rate_limit_message(provider, resp), ra)
                    # Only wait if the provider asks for a short pause that fits the budget.
                    if ra is not None and ra <= 5 and attempt < self.max_retries and (
                        deadline is None or time.monotonic() + ra < deadline
                    ):
                        await asyncio.sleep(ra)
                        continue
                    self._record_failure(st)
                    raise last_err
                if resp.status_code == 404 and ok_404:
                    st.consecutive_failures = 0
                    return resp
                if 500 <= resp.status_code < 600:
                    last_err = FetchError(f"Provider error HTTP {resp.status_code}.", "http")
                elif resp.status_code >= 400:
                    if resp.status_code in (401, 403):
                        self._record_failure(st)
                    raise FetchError(_http_error_message(provider, resp), "http")
                else:
                    st.consecutive_failures = 0
                    return resp
            if attempt < self.max_retries:
                backoff = min(4.0, 0.5 * (2 ** attempt)) * (0.5 + random.random())
                if deadline is not None and time.monotonic() + backoff >= deadline:
                    break
                await asyncio.sleep(backoff)
        self._record_failure(st)
        raise last_err or FetchError("Request failed.", "network")

    def _record_failure(self, st: _ProviderState) -> None:
        st.consecutive_failures += 1
        if st.consecutive_failures >= self.BREAKER_THRESHOLD:
            st.open_until = time.monotonic() + self.BREAKER_COOLDOWN
            st.consecutive_failures = 0


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    if v and v.strip().isdigit():
        return float(v.strip())
    return None


def _rate_limit_message(provider: str, resp: httpx.Response) -> str:
    msg = "Rate limit reached (HTTP 429)."
    if provider == "openalex":
        msg += " OpenAlex's shared free budget is exhausted – add a free OpenAlex API key in .env (PAPERFINDER_OPENALEX_API_KEY)."
    elif provider == "semanticscholar":
        msg += " Semantic Scholar's shared pool is busy – try again shortly or add PAPERFINDER_S2_API_KEY."
    return msg


def _http_error_message(provider: str, resp: httpx.Response) -> str:
    if resp.status_code in (401, 403):
        return f"Access denied by provider (HTTP {resp.status_code}). Check the API key/contact email settings."
    if resp.status_code == 422 and provider == "unpaywall":
        return "Unpaywall rejected the request (HTTP 422) – a valid contact email is required."
    return f"Provider returned HTTP {resp.status_code}."
