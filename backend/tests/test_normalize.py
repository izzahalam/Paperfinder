import pytest

from app.normalize import (
    clean_title, make_author, normalize_arxiv_id, normalize_doi, parse_identifier, reconstruct_inverted_index,
    split_name, strip_markup,
)


@pytest.mark.parametrize("raw,expected", [
    ("10.1038/nature14539", "10.1038/nature14539"),
    ("https://doi.org/10.1038/NATURE14539", "10.1038/nature14539"),
    ("doi: 10.1145/3065386.", "10.1145/3065386"),
    ("http://dx.doi.org/10.1000/xyz123", "10.1000/xyz123"),
    ("10.1002/(SICI)1097-4571(199806)49:8<693::AID-ASI3>3.0.CO;2-O", None),  # contains <>, rejected
    ("not a doi", None),
    ("10.12/abc", None),  # registrant too short
    ("", None),
])
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


def test_valid_doi_identifier():  # T-01 input side
    p = parse_identifier("10.1038/nature14539")
    assert (p.kind, p.value) == ("doi", "10.1038/nature14539")


@pytest.mark.parametrize("raw", ["10.abc/def", "hello world", "ftp://example.org/paper.pdf", "javascript:alert(1)"])
def test_invalid_identifier(raw):  # T-02
    p = parse_identifier(raw)
    assert p.kind == "invalid"
    assert p.note


@pytest.mark.parametrize("url,kind,value", [
    ("https://www.nature.com/articles/10.1038/nature14539", "doi", "10.1038/nature14539"),
    ("https://onlinelibrary.wiley.com/doi/full/10.1002/anie.201915678", "doi", "10.1002/anie.201915678"),
    ("https://dl.acm.org/doi/pdf/10.1145/3065386", "doi", "10.1145/3065386"),
    ("https://arxiv.org/abs/1706.03762v5", "arxiv", "1706.03762"),
    ("https://arxiv.org/pdf/1706.03762.pdf", "arxiv", "1706.03762"),
    ("https://example.org/some/paper", "url", None),
])
def test_url_parsing(url, kind, value):
    p = parse_identifier(url)
    assert p.kind == kind
    assert p.value == value


def test_arxiv_ids():
    assert normalize_arxiv_id("arXiv:2101.00001v2") == "2101.00001"
    assert normalize_arxiv_id("hep-th/9901001") == "hep-th/9901001"
    assert normalize_arxiv_id("10.48550/arXiv.1706.03762") == "1706.03762"
    assert normalize_arxiv_id("1234") is None
    assert parse_identifier("arXiv:1706.03762").kind == "arxiv"


def test_markup_and_titles():
    assert strip_markup("<jats:p>Abstract <i>Deep</i> learning &amp; more</jats:p>") == "Deep learning & more"
    assert clean_title("A <i>title</i>.") == "A title"


def test_inverted_index():
    assert reconstruct_inverted_index({"world": [1], "hello": [0], "again": [2]}) == "hello world again"
    assert reconstruct_inverted_index(None) is None


def test_names():
    assert split_name("Ludwig van Beethoven") == ("Ludwig", "van Beethoven")
    assert split_name("LeCun, Yann") == ("Yann", "LeCun")
    a = make_author(name="Geoffrey Hinton", orcid="https://orcid.org/0000-0002-1992-2684")
    assert a.family == "Hinton" and a.orcid == "0000-0002-1992-2684"
    assert make_author(name="   ") is None


def test_inline_tags_do_not_split_words():
    assert strip_markup("H<sub>2</sub>O and CO<jats:sub>2</jats:sub>") == "H2O and CO2"
