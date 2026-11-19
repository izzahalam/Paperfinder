"""Deduplication, provenance/conflicts, identity classification, access labels."""

from conftest import fixture_json, fixture_text

from app.aggregate import aggregate
from app.matching import MatchContext, classify, rank, title_similarity
from app.models import AccessStatus, Author, MatchLabel, ProviderRecord, VersionCandidate, VersionLabel
from app.providers.arxiv import parse_feed
from app.providers.crossref import parse_work as cr_parse
from app.providers.openalex import parse_work as oa_parse
from app.providers.unpaywall import parse_record


def rec(source, title, doi=None, year=2020, authors=("Ada Lovelace",), venue="Journal A", **kw):
    from app.normalize import make_author

    return ProviderRecord(source=source, title=title, doi=doi, year=year, venue=venue,
                          authors=[make_author(name=a) for a in authors], **kw)


def real_records():
    return [cr_parse(fixture_json("crossref_work.json")["message"]),
            oa_parse(fixture_json("openalex_work.json")),
            parse_record(fixture_json("unpaywall.json"))]


def test_duplicate_doi_consolidated():  # T-09
    papers = aggregate(real_records(), [])
    assert len(papers) == 1
    p = papers[0]
    assert set(p.sources) == {"crossref", "openalex", "unpaywall"}
    assert p.provenance["title"][0].source == "crossref"  # most authoritative first
    assert len(p.provenance["doi"]) == 3


def test_valid_doi_is_exact_with_evidence():  # T-01
    p = aggregate(real_records(), [])[0]
    m = classify(p, MatchContext(doi="10.1038/nature14539"))
    assert m.label == MatchLabel.exact
    assert any("matches the query" in e for e in m.evidence)


def test_conflicting_metadata_exposed():  # T-10, R-13
    papers = aggregate([
        rec("crossref", "Quantum widgets", doi="10.1/x", year=2019, venue="Physical Review A"),
        rec("openalex", "Quantum widgets", doi="10.1/x", year=2021, venue="Nature Physics"),
    ], [])
    p = papers[0]
    fields = {c.field for c in p.conflicts}
    assert {"year", "venue"} <= fields
    assert p.year == 2019  # higher-priority source wins, but nothing is silently dropped
    assert {v.value for v in p.provenance["year"]} == {2019, 2021}
    m = classify(p, MatchContext(doi="10.1/x"))
    assert m.label == MatchLabel.exact and m.warnings


def test_title_alone_never_exact():  # T-04, NFR-09
    p = aggregate([rec("crossref", "Deep learning", doi="10.1/a"), rec("openalex", "Deep learning", doi="10.1/a")], [])[0]
    m = classify(p, MatchContext(title="Deep learning"))
    assert m.label == MatchLabel.probable
    assert m.label != MatchLabel.exact


def test_title_author_year_exact():  # T-05, T-03
    p = aggregate([rec("crossref", "Deep learning", doi="10.1/a", year=2015, authors=("Yann LeCun",))], [])[0]
    m = classify(p, MatchContext(title="Deep Learning", authors=["LeCun"], year_from=2015, year_to=2015))
    assert m.label == MatchLabel.exact
    wrong = classify(p, MatchContext(title="Deep Learning", authors=["Smith"]))
    assert wrong.label == MatchLabel.similar and wrong.warnings


def test_reordered_title_is_not_near_identical():
    assert title_similarity("Attention is all you need", "Is attention all you need?") < 90


def test_near_duplicate_ranked_below_exact():  # T-03/T-04 ranking
    papers = aggregate([
        rec("crossref", "Attention is all you need", doi="10.1/a", year=2017, authors=("Ashish Vaswani",)),
        rec("crossref", "Is attention all you need?", doi="10.1/b", year=2025, authors=("Someone Else",)),
    ], [])
    ctx = MatchContext(title="Attention is all you need", authors=["Vaswani"])
    for p in papers:
        p.match = classify(p, ctx)
    ranked = rank(papers)
    assert ranked[0].doi == "10.1/a" and ranked[0].match.label == MatchLabel.exact
    assert ranked[1].match.label in (MatchLabel.similar, MatchLabel.unverified)


def test_keyword_search_is_unverified():
    p = aggregate([rec("crossref", "Something", doi="10.1/a")], [])[0]
    assert classify(p, MatchContext(keywords="graph neural networks")).label == MatchLabel.unverified


def test_preprint_linked_as_separate_version():  # T-12, R-10
    ax = parse_feed(fixture_text("arxiv_entry.xml"))[0]
    pub = rec("crossref", "Attention Is All You Need", doi="10.5555/3295222.3295349", year=2017,
              authors=("Ashish Vaswani",), venue="NeurIPS")
    papers = aggregate([pub, ax], [])
    assert len(papers) == 1
    p = papers[0]
    assert p.doi == "10.5555/3295222.3295349" and p.venue == "NeurIPS"
    assert any(c.version == VersionLabel.preprint and c.source == "arxiv" for c in p.candidates)
    assert p.access.status == AccessStatus.alternative


def test_preprint_not_linked_across_distant_years():
    ax = parse_feed(fixture_text("arxiv_entry.xml"))[0]  # 2017
    later = rec("crossref", "Attention Is All You Need", doi="10.9999/zzz", year=2025, authors=("Ashish Vaswani",))
    assert len(aggregate([later, ax], [])) == 2


def test_access_classification():
    oa = VersionCandidate(url="https://x.org/a.pdf", is_pdf_url=True, version=VersionLabel.vor, source="openalex", is_oa=True)
    p = aggregate([rec("openalex", "T", doi="10.1/a", candidates=[oa])], [])[0]
    assert p.access.status == AccessStatus.open_access
    closed = aggregate([rec("openalex", "T", doi="10.1/b", is_oa=False)], [])[0]
    assert closed.access.status == AccessStatus.closed  # T-13: truthful no-copy outcome
    unknown = aggregate([rec("crossref", "T", doi="10.1/c")], [])[0]
    assert unknown.access.status == AccessStatus.unknown


def test_abstract_unavailable_vs_failed():  # T-22, FR-53
    p = aggregate([rec("crossref", "T", doi="10.1/a")], [])[0]
    assert p.abstract.state == "unavailable" and p.abstract.text is None
    p2 = aggregate([rec("crossref", "T", doi="10.1/a")], ["semanticscholar"])[0]
    assert p2.abstract.state == "retrieval_failed"


def test_abstract_only_from_identity_matched_records():  # T-23, R-15
    a = rec("crossref", "Paper one", doi="10.1/one")
    b = rec("semanticscholar", "Paper two", doi="10.1/two", abstract="An abstract about something else entirely " * 3)
    papers = {p.doi: p for p in aggregate([a, b], [])}
    assert papers["10.1/one"].abstract.text is None
    assert papers["10.1/two"].abstract.source == "semanticscholar"


def test_never_merge_different_dois():
    papers = aggregate([rec("crossref", "Same title", doi="10.1/a"), rec("openalex", "Same title", doi="10.1/b")], [])
    assert len(papers) == 2


def test_author_model_roundtrip():
    assert Author(name="X").family is None
