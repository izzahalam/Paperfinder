"""Shared test fixtures. Tests never touch the network (NFR-26)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIX = Path(__file__).parent / "fixtures"


def fixture_json(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def make_pdf(lines: list[str]) -> bytes:
    """Build a minimal single-page PDF with extractable text."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 11 Tf 72 740 Td 14 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


FILLER = [
    "This paper studies representation learning with multiple processing layers in detail.",
    "We report experiments on several benchmark datasets and analyse the results carefully.",
    "The method improves accuracy while reducing computational cost considerably overall.",
]


@pytest.fixture
def pdf_factory() -> Callable[..., bytes]:
    return make_pdf


class Router:
    """httpx MockTransport handler that dispatches on host and records calls."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Callable[[httpx.Request], httpx.Response]]] = []
        self.calls: list[httpx.Request] = []

    def add(self, host: str, path_prefix: str, handler) -> "Router":
        if not callable(handler):
            resp = handler
            handler = lambda req, resp=resp: resp  # noqa: E731
        self.routes.append((host, path_prefix, handler))
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        host = request.headers.get("host") or request.url.host
        for h, prefix, handler in self.routes:
            if (h == request.url.host or h == host) and request.url.path.startswith(prefix):
                r = handler(request)
                return r() if callable(r) else r
        return httpx.Response(404, json={"error": "no mock route"})

    def count(self, host: str) -> int:
        return sum(1 for c in self.calls if c.url.host == host)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def standard_router() -> Router:
    """Provider mocks built from the real responses saved in fixtures/."""
    r = Router()
    cr_work = fixture_json("crossref_work.json")
    oa_work = fixture_json("openalex_work.json")
    up = fixture_json("unpaywall.json")
    s2 = fixture_json("s2_paper.json")
    r.add("api.crossref.org", "/works/10.1038/nature14539", httpx.Response(200, json=cr_work))
    r.add("api.crossref.org", "/works", httpx.Response(200, json=fixture_json("crossref_search.json")))
    r.add("api.openalex.org", "/works/doi:10.1038/nature14539", httpx.Response(200, json=oa_work))
    r.add("api.openalex.org", "/works", httpx.Response(200, json={"results": [oa_work]}))
    r.add("api.unpaywall.org", "/v2/10.1038/nature14539", httpx.Response(200, json=up))
    r.add("api.semanticscholar.org", "/graph/v1/paper/search", httpx.Response(200, json={"data": [s2]}))
    r.add("api.semanticscholar.org", "/graph/v1/paper/", httpx.Response(404, json={"error": "not found"}))
    r.add("export.arxiv.org", "/api/query", httpx.Response(200, text=fixture_text("arxiv_entry.xml")))
    return r


@pytest.fixture
def router() -> Router:
    return standard_router()


@pytest.fixture
def app_client(tmp_path, router):
    """A TestClient for the app with a temp data dir and mocked providers."""
    from fastapi.testclient import TestClient

    from app.config import Preferences, Settings
    from app.main import create_app

    settings = Settings(data_dir=tmp_path / "data", frontend_dist=tmp_path / "nodist")
    app = create_app(settings)
    with TestClient(app) as client:
        client.app.state.search.transport = router.transport()
        client.app.state.downloads.use_process_pool = False
        client.app.state.db.put_preferences(Preferences(contact_email="tester@example.org",
                                                        search_budget_seconds=10, max_retries=1))
        client.router_mock = router
        client.settings = settings
        yield client


def wait_search(client, search_id: str, timeout: float = 10.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        data = client.get(f"/api/search/{search_id}").json()
        if data["status"] != "running":
            return data
        time.sleep(0.05)
    raise AssertionError("search did not finish")


def wait_download(client, download_id: str, timeout: float = 10.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        data = client.get(f"/api/downloads/{download_id}").json()
        if data["status"] not in ("queued", "downloading", "validating"):
            return data
        time.sleep(0.05)
    raise AssertionError("download did not finish")
