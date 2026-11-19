"""Citation formatting fixtures (T-24..26)."""

from app.citations import bib_escape, generate
from app.models import Author, Paper

DEEP = Paper(
    id="p", doi="10.1038/nature14539", title="Deep learning", year=2015, venue="Nature", volume="521",
    issue="7553", pages="436-444", publisher="Springer Science and Business Media LLC", type="journal-article",
    authors=[Author(name="Yann LeCun", given="Yann", family="LeCun"),
             Author(name="Yoshua Bengio", given="Yoshua", family="Bengio"),
             Author(name="Geoffrey Hinton", given="Geoffrey", family="Hinton")],
)


def test_apa7():  # T-24
    c = generate(DEEP, "apa7")
    assert c.text == ("LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521(7553), 436–444. "
                      "https://doi.org/10.1038/nature14539")
    assert [s.text for s in c.segments if s.italic] == ["Nature", "521"]
    assert c.warnings == []


def test_ieee():  # T-24
    c = generate(DEEP, "ieee")
    assert c.text == ("Y. LeCun, Y. Bengio, and G. Hinton, “Deep learning,” Nature, vol. 521, no. 7553, "
                      "pp. 436–444, 2015, doi: 10.1038/nature14539.")


def test_ieee_many_authors_et_al():
    p = DEEP.model_copy(update={"authors": [Author(name=f"A{i} Person", given=f"A{i}", family="Person")
                                            for i in range(8)]})
    assert "et al." in generate(p, "ieee").text


def test_apa_hyphenated_initials():
    p = DEEP.model_copy(update={"authors": [Author(name="Jean-Paul Sartre", given="Jean-Paul", family="Sartre")]})
    assert generate(p, "apa7").text.startswith("Sartre, J.-P. (2015)")


def test_bibtex_valid_syntax():  # T-26
    c = generate(DEEP, "bibtex")
    t = c.text
    assert t.startswith("@article{lecun2015deep,")
    assert "author = {LeCun, Yann and Bengio, Yoshua and Hinton, Geoffrey}" in t
    assert "journal = {Nature}" in t and "pages = {436--444}" in t and "doi = {10.1038/nature14539}" in t
    assert t.count("{") == t.count("}")


def test_bibtex_escaping():
    assert bib_escape("R&D 50% $x$ a_b #1") == r"R\&D 50\% \$x\$ a\_b \#1"
    p = DEEP.model_copy(update={"title": "Profit & loss {in} 100% of_cases"})
    t = generate(p, "bibtex").text
    assert r"\&" in t and r"\%" in t and r"\_" in t
    assert t.count("{") - t.count(r"\{") == t.count("}") - t.count(r"\}")


def test_missing_fields_warn():  # T-25
    p = Paper(id="x", title="Untitled thing", type="journal-article", venue="J")
    c = generate(p, "apa7")
    assert any("authors" in w for w in c.warnings)
    assert any("year" in w for w in c.warnings)
    assert "(n.d.)" in c.text


def test_user_overrides():  # FR-62
    c = generate(DEEP, "apa7", {"year": 2016, "authors": ["Ada Lovelace"], "evil_field": "x"})
    assert c.text.startswith("Lovelace, A. (2016).")


def test_preprint_citation():
    p = Paper(id="x", title="Attention is all you need", year=2017, arxiv_id="1706.03762", type="preprint",
              authors=[Author(name="Ashish Vaswani", given="Ashish", family="Vaswani")])
    assert "arXiv" in generate(p, "apa7").text
    t = generate(p, "bibtex").text
    assert t.startswith("@misc{") and "eprint = {1706.03762}" in t
