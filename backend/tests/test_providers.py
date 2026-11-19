"""Adapter parsers against real (saved) provider responses."""

from conftest import fixture_json, fixture_text

from app.models import VersionLabel
from app.providers.arxiv import parse_feed
from app.providers.crossref import parse_work as cr_parse
from app.providers.openalex import parse_work as oa_parse
from app.providers.semanticscholar import parse_paper
from app.providers.unpaywall import parse_record


def test_crossref_work():
    r = cr_parse(fixture_json("crossref_work.json")["message"])
    assert r.doi == "10.1038/nature14539"
    assert r.title == "Deep learning"
    assert [a.family for a in r.authors] == ["LeCun", "Bengio", "Hinton"]
    assert r.year == 2015 and r.venue == "Nature" and r.volume == "521" and r.pages == "436-444"
    assert r.candidates == []  # no CC licence => no publisher full-text offered


def test_crossref_search_results():
    items = fixture_json("crossref_search.json")["message"]["items"]
    recs = [cr_parse(i) for i in items]
    assert recs and all(r.source == "crossref" for r in recs)


def test_openalex_work():
    r = oa_parse(fixture_json("openalex_work.json"))
    assert r.doi == "10.1038/nature14539"
    assert r.authors[0].orcid == "0000-0002-1992-2684"
    assert r.pages == "436-444"
    # Only OA locations become candidates, with version labels from the source.
    assert r.candidates and all(c.is_oa for c in r.candidates)
    assert {c.version for c in r.candidates} == {VersionLabel.preprint}


def test_semantic_scholar():
    r = parse_paper(fixture_json("s2_paper.json"))
    assert r.arxiv_id == "1706.03762"
    assert r.title == "Attention is All you Need"
    assert r.candidates[0].version == VersionLabel.unknown  # S2 does not state the version


def test_arxiv_feed():
    recs = parse_feed(fixture_text("arxiv_entry.xml"))
    assert len(recs) == 1
    r = recs[0]
    assert r.arxiv_id == "1706.03762" and r.year == 2017 and r.type == "preprint"
    assert r.candidates[0].version == VersionLabel.preprint
    assert r.candidates[0].url.startswith("https://arxiv.org/pdf/")


def test_arxiv_error_feed_is_empty():
    xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#x</id>'
           '<title>Error</title></entry></feed>')
    assert parse_feed(xml) == []


def test_unpaywall():
    r = parse_record(fixture_json("unpaywall.json"))
    assert r.doi == "10.1038/nature14539"
    assert r.candidates and r.candidates[0].version == VersionLabel.preprint
    assert any("Repository" in e for e in r.candidates[0].evidence)
