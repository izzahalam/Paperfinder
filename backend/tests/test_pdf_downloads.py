"""PDF validation and download handling (T-11, T-14..18, T-21)."""

import asyncio

import httpx

from conftest import FILLER, make_pdf

from app.config import Preferences, Settings
from app.db import Database
from app.downloads import DownloadService, safe_filename
from app.models import Author, Paper, VersionCandidate, VersionLabel
from app.net import SafeFetcher
from app.pdfcheck import validate_file

PAPER = Paper(id="p_1", doi="10.1038/nature14539", title="Deep learning for cancer histopathology images", year=2015,
              authors=[Author(name="Yann LeCun", given="Yann", family="LeCun"),
                       Author(name="Geoffrey Hinton", given="Geoffrey", family="Hinton")])
EXPECTED = {"title": PAPER.title, "doi": PAPER.doi, "author_families": ["LeCun", "Hinton"], "year": 2015}


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_matching_pdf_verified(tmp_path):  # T-11
    pdf = make_pdf([PAPER.title, "Yann LeCun, Geoffrey Hinton", "doi:10.1038/nature14539", *FILLER])
    r = validate_file(write(tmp_path, "a.pdf", pdf), EXPECTED)
    assert r["state"] == "Verified"
    assert any("DOI" in d for d in r["details"])


def test_html_as_pdf_rejected(tmp_path):  # T-14, R-04
    r = validate_file(write(tmp_path, "b.pdf", b"<!DOCTYPE html><html><body>Sign in</body></html>"), EXPECTED)
    assert r["state"] == "Rejected"


def test_wrong_paper_rejected(tmp_path):  # T-15, R-03
    pdf = make_pdf(["A survey of coral reef ecology in the Pacific", "J. Smith, K. Brown", *FILLER])
    r = validate_file(write(tmp_path, "c.pdf", pdf), EXPECTED)
    assert r["state"] == "Rejected"


def test_corrupt_pdf_fails(tmp_path):  # T-16
    pdf = make_pdf([PAPER.title, *FILLER])
    r = validate_file(write(tmp_path, "d.pdf", pdf[: len(pdf) // 2]), EXPECTED)
    assert r["state"] == "Failed"
    r2 = validate_file(write(tmp_path, "e.pdf", b"%PDF-1.4\ngarbage\n%%EOF"), EXPECTED)
    assert r2["state"] == "Failed"


def test_no_text_is_limited(tmp_path):
    r = validate_file(write(tmp_path, "f.pdf", make_pdf(["x"])), EXPECTED)
    assert r["state"] == "Verification Limited"


def test_safe_filename_prevents_traversal():  # T-21, R-07
    evil = Paper(id="x", title="../../etc/passwd\\..\\win.ini", year=2020,
                 authors=[Author(name="../../root", family="../../root")])
    name = safe_filename(evil)
    assert "/" not in name and "\\" not in name and ".." not in name
    assert name.endswith(".pdf")
    con = safe_filename(Paper(id="y", title="", authors=[Author(name="CON", family="CON")]))
    assert not con.lower().startswith("con.")
    assert safe_filename(PAPER) == "LeCun_2015_Deep_learning_for_cancer_histopathology_images.pdf"


# ------------------------------------------------------------------ download service


async def _public(host, port):
    return ["93.184.216.34"]


def _service(tmp_path, handler):
    s = Settings(data_dir=tmp_path / "data")
    s.ensure_dirs()
    db = Database(s.db_path)
    db.put_preferences(Preferences(max_pdf_mb=1))
    svc = DownloadService(db, s)
    svc.use_process_pool = False
    svc.fetcher_factory = lambda prefs: SafeFetcher(resolver=_public, transport=httpx.MockTransport(handler),
                                                    pin_ip=False)
    return svc, db, s


def _paper(url="https://repo.example.org/a.pdf", is_oa=True):
    p = PAPER.model_copy(deep=True)
    p.candidates = [VersionCandidate(id="v_1", url=url, is_pdf_url=True, version=VersionLabel.accepted,
                                     source="openalex", is_oa=is_oa)]
    return p


def _run(svc, paper):
    async def go():
        rec = svc.start(paper, "v_1")
        return await svc.wait(rec.id)
    return asyncio.run(go())


def test_download_verified_and_finalised(tmp_path):
    pdf = make_pdf([PAPER.title, "Yann LeCun, Geoffrey Hinton", *FILLER])
    svc, db, s = _service(tmp_path, lambda r: httpx.Response(200, content=pdf,
                                                             headers={"content-type": "application/pdf"}))
    paper = _paper()
    db.upsert_papers([paper])
    rec = _run(svc, paper)
    assert rec.status == "ready" and rec.verification.value == "Verified"
    assert (s.downloads_dir / rec.filename).read_bytes() == pdf
    assert rec.sha256 and rec.size_bytes == len(pdf)
    assert list(s.temp_dir.iterdir()) == []  # temp file moved atomically
    assert db.get_paper(paper.id).candidates[0].verification.value == "Verified"
    # identical file again -> deduplicated (R-21)
    rec2 = _run(svc, paper)
    assert rec2.filename == rec.filename and len(list(s.downloads_dir.iterdir())) == 1


def test_html_download_blocked_and_cleaned(tmp_path):  # T-14 end-to-end
    svc, db, s = _service(tmp_path, lambda r: httpx.Response(200, content=b"<html>Please log in</html>"))
    rec = _run(svc, _paper())
    assert rec.status == "blocked" and rec.filename is None
    assert list(s.downloads_dir.iterdir()) == [] and list(s.temp_dir.iterdir()) == []


def test_oversized_download_stopped(tmp_path):  # T-17
    big = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
    svc, db, s = _service(tmp_path, lambda r: httpx.Response(200, content=big))
    rec = _run(svc, _paper())
    assert rec.status == "failed" and "limit" in rec.error
    assert list(s.temp_dir.iterdir()) == []


def test_interrupted_download_cleans_temp(tmp_path):  # T-18
    def handler(r):
        raise httpx.ReadError("connection reset")
    svc, db, s = _service(tmp_path, handler)
    rec = _run(svc, _paper())
    assert rec.status == "failed" and rec.error
    assert list(s.temp_dir.iterdir()) == []


def test_non_oa_candidate_never_downloaded(tmp_path):  # T-13, NFR-31
    calls = []
    svc, db, s = _service(tmp_path, lambda r: calls.append(r) or httpx.Response(200, content=b""))
    rec = svc.start(_paper(is_oa=False), "v_1")
    assert rec.status == "blocked" and not calls


def test_unsafe_candidate_url_blocked(tmp_path):  # T-20 via download path
    svc, db, s = _service(tmp_path, lambda r: httpx.Response(200, content=b"x"))
    rec = _run(svc, _paper(url="http://127.0.0.1:8000/api/health"))
    assert rec.status == "blocked"


def test_validation_in_isolated_process(tmp_path):
    pdf = make_pdf([PAPER.title, "Yann LeCun, Geoffrey Hinton", *FILLER])
    svc, db, s = _service(tmp_path, lambda r: httpx.Response(200, content=pdf))
    svc.use_process_pool = True
    try:
        rec = _run(svc, _paper())
    finally:
        svc._kill_pool()
    assert rec.verification.value == "Verified"
