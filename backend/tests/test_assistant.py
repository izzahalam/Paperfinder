"""AI assistant output is grounded and bounded (NFR-11, R-25)."""

from app.assistant import build_user_message, sanitize
from app.models import SearchMode


def base(**kw):
    d = {"mode": "advanced", "query": None, "title": None, "authors": [], "keywords": None, "year_from": None,
         "year_to": None, "venue": None, "explanation": "ok", "refinements": []}
    return d | kw


def test_fabricated_doi_removed():
    s = sanitize(base(mode="doi_url", query="10.1038/nature14539"), "the famous deep learning review by LeCun")
    assert s.mode == SearchMode.advanced and s.query is None
    assert "removed" in s.explanation
    assert s.keywords  # falls back to the user's own words


def test_user_doi_kept():
    s = sanitize(base(mode="doi_url", query="10.1038/nature14539"), "mujhe ye paper chahiye 10.1038/nature14539")
    assert s.mode == SearchMode.doi_url and s.query == "10.1038/nature14539"


def test_years_and_lists_bounded():
    s = sanitize(base(year_from=2030, year_to=2010, authors=[" a "] * 30, refinements=["x"] * 10,
                      keywords="graph neural networks"), "gnn papers")
    assert s.year_from == 2010 and s.year_to == 2030
    assert len(s.authors) == 10 and len(s.refinements) == 4
    s2 = sanitize(base(year_from=99999, keywords="x"), "x y")
    assert s2.year_from is None


def test_previous_search_context_is_minimal():
    from app.models import JobStatus, SearchJob, SearchRequest

    job = SearchJob(id="s", mode="title", request=SearchRequest(mode="title", query="graph networks"),
                    status=JobStatus.completed, started_at="t", sources=[])
    msg = build_user_message("refine please", job)
    assert "<previous_search>" in msg and "result_count: 0" in msg
