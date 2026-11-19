"""End-to-end API tests with mocked providers (T-01, T-06..08, T-27..29, T-31, T-33..35)."""

import asyncio

import httpx

from conftest import FILLER, make_pdf, wait_download, wait_search

from app.net import SafeFetcher


def search(client, **body):
    r = client.post("/api/search", json=body)
    assert r.status_code == 202, r.text
    return wait_search(client, r.json()["id"])


def test_health(app_client):
    r = app_client.get("/api/health").json()
    assert r["status"] == "ok" and r["database"] is True


def test_doi_search_end_to_end(app_client):  # T-01, T-35
    job = search(app_client, mode="doi_url", query="https://doi.org/10.1038/nature14539")
    assert job["status"] == "completed"
    assert len(job["results"]) == 1
    p = job["results"][0]
    assert p["match"]["label"] == "Exact" and p["doi"] == "10.1038/nature14539"
    by = {o["provider"]: o for o in job["sources"]}
    assert by["crossref"]["status"] == "success" and by["unpaywall"]["status"] == "success"
    assert by["semanticscholar"]["status"] == "no_results"
    assert by["arxiv"]["status"] == "skipped" and by["arxiv"]["notes"]
    assert any("only from the sources listed" in n for n in job["notes"])  # no exhaustive-coverage claim


def test_invalid_doi_rejected(app_client):  # T-02
    r = app_client.post("/api/search", json={"mode": "doi_url", "query": "10.abc"})
    assert r.status_code == 400 and "DOI" in r.json()["detail"]
    r = app_client.post("/api/search", json={"mode": "advanced"})
    assert r.status_code == 422


def test_provider_outage_partial_results(app_client):  # T-06, UC-09
    app_client.router_mock.routes.insert(0, ("api.openalex.org", "/", lambda r: httpx.Response(500)))
    job = search(app_client, mode="doi_url", query="10.1038/nature14539")
    assert job["status"] == "partial"
    by = {o["provider"]: o for o in job["sources"]}
    assert by["openalex"]["status"] == "failed" and by["openalex"]["error"]
    assert by["openalex"]["attempts"] == 2  # bounded retries (max_retries=1)
    assert job["results"] and job["results"][0]["match"]["label"] == "Exact"


def test_rate_limit_no_retry_storm(app_client):  # T-07, R-17
    app_client.router_mock.routes.insert(
        0, ("api.crossref.org", "/", lambda r: httpx.Response(429, headers={"retry-after": "120"})))
    job = search(app_client, mode="doi_url", query="10.1038/nature14539")
    by = {o["provider"]: o for o in job["sources"]}
    assert by["crossref"]["status"] == "rate_limited"
    assert app_client.router_mock.count("api.crossref.org") == 1  # long Retry-After is not waited on/retried


def test_no_results_state(app_client):  # T-08
    for host in ("api.crossref.org", "api.openalex.org", "api.unpaywall.org"):
        app_client.router_mock.routes.insert(0, (host, "/", lambda r: httpx.Response(404)))
    job = search(app_client, mode="doi_url", query="10.9999/does-not-exist")
    assert job["status"] == "completed" and job["results"] == []


def test_title_search_not_exact_without_corroboration(app_client):  # T-03/T-04 via API
    job = search(app_client, mode="title", query="Attention is all you need")
    labels = {p["title"]: p["match"]["label"] for p in job["results"]}
    assert "Exact" not in labels.values()


def test_advanced_search_and_unsupported_criteria_disclosed(app_client):  # UC-03
    job = search(app_client, mode="advanced", title="Deep learning", authors=["LeCun"], year_from=2015,
                 year_to=2015, venue="Nature")
    by = {o["provider"]: o for o in job["sources"]}
    assert any("venue" in n.lower() for n in by["arxiv"]["notes"] + by["openalex"]["notes"])
    deep = [p for p in job["results"] if p["doi"] == "10.1038/nature14539"]
    assert deep and deep[0]["match"]["label"] == "Exact"


def test_history_persists_and_deletes(app_client, tmp_path):  # T-27, T-28
    job = search(app_client, mode="doi_url", query="10.1038/nature14539")
    hist = app_client.get("/api/history").json()
    assert hist[0]["id"] == job["id"] and hist[0]["result_count"] == 1
    # Simulate restart: a fresh app on the same data dir sees the history.
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(app_client.settings)) as c2:
        restored = c2.get(f"/api/search/{job['id']}").json()
        assert restored["cached"] is True and restored["results"][0]["doi"] == "10.1038/nature14539"
        assert c2.delete(f"/api/history/{job['id']}").status_code == 200
        assert c2.get("/api/history").json() == []
        assert c2.get(f"/api/papers/{job['results'][0]['id']}").status_code == 404  # orphan purged


def test_saved_papers(app_client):
    job = search(app_client, mode="doi_url", query="10.1038/nature14539")
    pid = job["results"][0]["id"]
    assert app_client.post("/api/saved", json={"paper_id": pid, "note": "read later"}).json()["saved"]
    assert app_client.get("/api/saved").json()[0]["note"] == "read later"
    app_client.delete("/api/history")
    assert app_client.get(f"/api/papers/{pid}").status_code == 200  # saved papers survive history deletion
    assert app_client.delete(f"/api/saved/{pid}").status_code == 200


def test_citation_endpoint(app_client):
    pid = search(app_client, mode="doi_url", query="10.1038/nature14539")["results"][0]["id"]
    r = app_client.post("/api/citations", json={"paper_id": pid, "style": "ieee"}).json()
    assert "Y. LeCun" in r["text"] and "doi: 10.1038/nature14539" in r["text"]


def test_versions_and_download_flow(app_client):  # UC-05, UC-06, T-29
    pid = search(app_client, mode="doi_url", query="10.1038/nature14539")["results"][0]["id"]
    v = app_client.post(f"/api/papers/{pid}/versions").json()
    assert v["access"]["status"] == "Alternative Version Found"
    assert v["candidates"] and all(c["is_oa"] for c in v["candidates"])
    pdf = make_pdf(["Deep learning", "Yann LeCun Yoshua Bengio Geoffrey Hinton", *FILLER])

    async def public(host, port):
        return ["93.184.216.34"]

    def handler(req):
        if req.url.path.endswith(".pdf"):
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        return httpx.Response(200, text='<html><meta name="citation_pdf_url" content="/files/deep.pdf"></html>')

    app_client.app.state.downloads.fetcher_factory = lambda prefs: SafeFetcher(
        resolver=public, transport=httpx.MockTransport(handler), pin_ip=False)
    cand = v["candidates"][0]
    d = app_client.post("/api/downloads", json={"paper_id": pid, "candidate_id": cand["id"]}).json()
    d = wait_download(app_client, d["id"])
    assert d["status"] == "ready" and d["verification"] == "Verified" and d["file_available"]
    f = app_client.get(f"/api/downloads/{d['id']}/file")
    assert f.status_code == 200 and f.content == pdf
    assert "attachment" in f.headers["content-disposition"]
    # Deleting the file and the record are separate, explicit actions (R-28).
    assert app_client.delete(f"/api/downloads/{d['id']}/file").json()["file_deleted"] is True
    assert app_client.get(f"/api/downloads/{d['id']}/file").status_code == 410
    assert app_client.delete(f"/api/downloads/{d['id']}").json() == {"record_deleted": True, "file_deleted": False}


def test_cancel_search(app_client):  # T-33
    async def slow(req):
        await asyncio.sleep(0.5)
        return httpx.Response(200, json={"message": {"items": []}})
    app_client.router_mock.routes.insert(0, ("api.crossref.org", "/", slow))
    r = app_client.post("/api/search", json={"mode": "title", "query": "slow search please"}).json()
    c = app_client.post(f"/api/search/{r['id']}/cancel").json()
    assert c["status"] == "cancelled"
    assert any(o["status"] == "cancelled" for o in c["sources"])


def test_concurrent_search_limit(app_client):  # T-31
    async def slow(req):
        await asyncio.sleep(0.4)
        return httpx.Response(200, json={"message": {"items": []}})
    app_client.router_mock.routes.insert(0, ("api.crossref.org", "/", slow))
    codes = [app_client.post("/api/search", json={"mode": "title", "query": f"query {i}"}).status_code
             for i in range(5)]
    assert codes.count(202) == 3 and 429 in codes
    assert app_client.get("/api/health").status_code == 200  # still responsive


def test_loopback_host_only(app_client):  # T-34, R-20
    r = app_client.get("/api/health", headers={"host": "evil.example.com"})
    assert r.status_code == 421


def test_cross_origin_writes_blocked(app_client):
    r = app_client.post("/api/search", json={"mode": "title", "query": "abc"},
                        headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403
    r = app_client.post("/api/search", content="mode=title", headers={"content-type": "text/plain"})
    assert r.status_code == 415


def test_settings_roundtrip_and_no_secrets(app_client):
    s = app_client.get("/api/settings").json()
    assert set(s["credentials"].values()) <= {True, False}  # only booleans, never values
    prefs = s["preferences"] | {"enabled_providers": ["crossref"], "contact_email": ""}
    assert app_client.put("/api/settings", json=prefs).status_code == 200
    provs = {p["name"]: p for p in app_client.get("/api/providers").json()}
    assert provs["crossref"]["enabled"] and not provs["openalex"]["enabled"]
    assert provs["unpaywall"]["unavailable_reason"]
    bad = app_client.put("/api/settings", json=prefs | {"enabled_providers": ["nope"]})
    assert bad.status_code == 422


def test_assistant_unconfigured(app_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    r = app_client.post("/api/assistant/interpret", json={"text": "papers on transformers for protein folding"})
    assert r.status_code == 503


def test_storage_info(app_client):
    info = app_client.get("/api/storage").json()
    assert {"download_bytes", "database_bytes", "searches"} <= info.keys()
