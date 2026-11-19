"""Citation generation from structured metadata (SRS FR-55..62, R-14, T-24..26).

Citations are built only from structured fields – never from an LLM. Output
is returned as plain text plus formatting segments (italics) so the UI never
has to render provider-supplied HTML (R-30).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import Author, CitationResponse, CitationSegment, Paper

EDITABLE_FIELDS = {"title", "authors", "year", "venue", "publisher", "volume", "issue", "pages",
                   "article_number", "doi", "type", "url"}

JOURNAL_TYPES = {"journal-article", "article", "JournalArticle", "Review"}
CONFERENCE_TYPES = {"proceedings-article", "Conference", "conference-paper"}
BOOK_TYPES = {"book", "monograph", "Book", "edited-book"}
CHAPTER_TYPES = {"book-chapter", "BookSection", "book-section"}
PREPRINT_TYPES = {"posted-content", "preprint"}


class Meta:
    """Citation-ready view of a paper with user overrides applied."""

    def __init__(self, paper: Paper, overrides: dict[str, Any] | None = None):
        o = {k: v for k, v in (overrides or {}).items() if k in EDITABLE_FIELDS}
        self.title = _s(o.get("title", paper.title))
        authors = o.get("authors")
        if isinstance(authors, list):
            from .normalize import make_author

            self.authors = [a for a in (make_author(name=str(x)) for x in authors if str(x).strip()) if a]
        else:
            self.authors = paper.authors
        y = o.get("year", paper.year)
        try:
            self.year = int(y) if y not in (None, "") else None
        except (TypeError, ValueError):
            self.year = None
        self.venue = _s(o.get("venue", paper.venue))
        self.publisher = _s(o.get("publisher", paper.publisher))
        self.volume = _s(o.get("volume", paper.volume))
        self.issue = _s(o.get("issue", paper.issue))
        self.pages = _s(o.get("pages", paper.pages))
        self.article_number = _s(o.get("article_number", paper.article_number))
        self.doi = _s(o.get("doi", paper.doi))
        self.url = _s(o.get("url", paper.url))
        self.type = _s(o.get("type", paper.type))
        self.arxiv_id = paper.arxiv_id

    @property
    def kind(self) -> str:
        t = self.type or ""
        if t in CONFERENCE_TYPES:
            return "conference"
        if t in CHAPTER_TYPES:
            return "chapter"
        if t in BOOK_TYPES:
            return "book"
        if t in PREPRINT_TYPES or (self.arxiv_id and not self.venue):
            return "preprint"
        return "journal" if self.venue else "other"


def _s(v) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _initials(given: str | None, with_space: bool = True) -> str:
    if not given:
        return ""
    parts = re.split(r"[\s]+", given.strip())
    out = []
    for p in parts:
        sub = [x for x in p.split("-") if x]
        out.append("-".join(f"{x[0].upper()}." for x in sub if x[0].isalpha()))
    return (" " if with_space else "").join(x for x in out if x)


def _family(a: Author) -> str:
    return a.family or a.name


def _pages(p: str | None, en_dash: bool = True) -> str | None:
    if not p:
        return None
    return re.sub(r"\s*[-–—]+\s*", "–" if en_dash else "--", p)


def _warn_missing(m: Meta, needed: list[str]) -> list[str]:
    labels = {"authors": "authors", "year": "year", "title": "title", "venue": "journal/venue",
              "volume": "volume", "pages": "pages or article number", "doi": "DOI", "publisher": "publisher"}
    w = []
    for f in needed:
        if f == "pages":
            if not (m.pages or m.article_number):
                w.append(labels[f])
        elif not getattr(m, f):
            w.append(labels[f])
    return [f"Missing {x} – check before using this citation." for x in w]


# ------------------------------------------------------------------ APA 7


def _apa_author(a: Author) -> str:
    if not a.family and not a.given:
        return a.name
    ini = _initials(a.given)
    return f"{_family(a)}, {ini}" if ini else _family(a)


def _apa_authors(authors: list[Author]) -> str:
    names = [_apa_author(a) for a in authors]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) <= 20:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:19]) + ", . . . " + names[-1]


def apa7(m: Meta) -> tuple[list[CitationSegment], list[str]]:
    seg: list[CitationSegment] = []
    kind = m.kind
    warnings = _warn_missing(m, ["authors", "year", "title"] + (["venue", "volume", "pages"] if kind == "journal" else []))
    authors = _apa_authors(m.authors)
    year = f"({m.year})" if m.year else "(n.d.)"
    title = (m.title or "[Untitled]").rstrip(".")
    lead = f"{authors} {year}. " if authors else ""
    if not authors:
        # APA: title moves to author position when there is no author.
        seg.append(CitationSegment(text=f"{title}. {year}. "))
    else:
        seg.append(CitationSegment(text=lead))
    if kind in ("journal", "conference") and m.venue:
        if authors:
            seg.append(CitationSegment(text=f"{title}. "))
        seg.append(CitationSegment(text=m.venue, italic=True))
        if m.volume:
            seg.append(CitationSegment(text=", "))
            seg.append(CitationSegment(text=m.volume, italic=True))
        if m.issue:
            seg.append(CitationSegment(text=f"({m.issue})"))
        if m.pages:
            seg.append(CitationSegment(text=f", {_pages(m.pages)}"))
        elif m.article_number:
            seg.append(CitationSegment(text=f", Article {m.article_number}"))
        seg.append(CitationSegment(text=". "))
    elif kind == "book":
        if authors:
            seg.append(CitationSegment(text=title, italic=True))
            seg.append(CitationSegment(text=". "))
        if m.publisher:
            seg.append(CitationSegment(text=f"{m.publisher}. "))
    elif kind == "preprint":
        if authors:
            seg.append(CitationSegment(text=f"{title}", italic=True))
            seg.append(CitationSegment(text=" [Preprint]. "))
        seg.append(CitationSegment(text=f"{'arXiv' if m.arxiv_id else (m.venue or m.publisher or 'Preprint server')}. "))
    else:
        if authors:
            seg.append(CitationSegment(text=f"{title}. "))
        if m.venue:
            seg.append(CitationSegment(text=m.venue, italic=True))
            seg.append(CitationSegment(text=". "))
        elif m.publisher:
            seg.append(CitationSegment(text=f"{m.publisher}. "))
    if m.doi:
        seg.append(CitationSegment(text=f"https://doi.org/{m.doi}"))
    elif m.url:
        seg.append(CitationSegment(text=m.url))
    else:
        warnings.append("No DOI or URL – APA recommends including one when available.")
    return _tidy(seg), warnings


# ------------------------------------------------------------------ IEEE


def _ieee_author(a: Author) -> str:
    ini = _initials(a.given)
    return f"{ini} {_family(a)}".strip() if (a.family or a.given) else a.name


def _ieee_authors(authors: list[Author]) -> str:
    names = [_ieee_author(a) for a in authors]
    if not names:
        return ""
    if len(names) > 6:
        return f"{names[0]} et al."
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + ", and " + names[-1]


def ieee(m: Meta) -> tuple[list[CitationSegment], list[str]]:
    kind = m.kind
    warnings = _warn_missing(m, ["authors", "title", "year"] + (["venue", "volume", "pages"] if kind == "journal" else []))
    seg: list[CitationSegment] = []
    authors = _ieee_authors(m.authors)
    if authors:
        seg.append(CitationSegment(text=f"{authors}, "))
    title = (m.title or "[Untitled]").rstrip(".")
    if kind == "book":
        seg.append(CitationSegment(text=title, italic=True))
        seg.append(CitationSegment(text=". "))
        if m.publisher:
            seg.append(CitationSegment(text=f"{m.publisher}, "))
        seg.append(CitationSegment(text=f"{m.year or 'n.d.'}."))
    else:
        seg.append(CitationSegment(text=f"“{title},” "))
        if kind == "conference" and m.venue:
            seg.append(CitationSegment(text="in "))
            seg.append(CitationSegment(text=m.venue, italic=True))
            seg.append(CitationSegment(text=", "))
        elif kind == "preprint":
            seg.append(CitationSegment(text=f"arXiv:{m.arxiv_id}, " if m.arxiv_id else "preprint, "))
        elif m.venue:
            seg.append(CitationSegment(text=m.venue, italic=True))
            seg.append(CitationSegment(text=", "))
        if m.volume:
            seg.append(CitationSegment(text=f"vol. {m.volume}, "))
        if m.issue:
            seg.append(CitationSegment(text=f"no. {m.issue}, "))
        if m.pages:
            p = _pages(m.pages)
            seg.append(CitationSegment(text=f"{'pp.' if '–' in p else 'p.'} {p}, "))
        elif m.article_number:
            seg.append(CitationSegment(text=f"Art. no. {m.article_number}, "))
        seg.append(CitationSegment(text=f"{m.year or 'n.d.'}"))
        seg.append(CitationSegment(text=f", doi: {m.doi}." if m.doi else "."))
    return _tidy(seg), warnings


# ------------------------------------------------------------------ BibTeX

_BIB_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def bib_escape(s: str) -> str:
    return "".join(_BIB_SPECIAL.get(c, c) for c in s)


def bib_key(m: Meta) -> str:
    fam = _family(m.authors[0]) if m.authors else "anon"
    fam = unicodedata.normalize("NFKD", fam).encode("ascii", "ignore").decode()
    word = next((w for w in re.findall(r"[A-Za-z]+", m.title or "") if len(w) > 3), "paper")
    return re.sub(r"[^A-Za-z0-9]", "", f"{fam}{m.year or ''}{word}").lower() or "ref"


def bibtex(m: Meta) -> tuple[list[CitationSegment], list[str]]:
    kind = m.kind
    entry = {"journal": "article", "conference": "inproceedings", "book": "book", "chapter": "incollection",
             "preprint": "misc"}.get(kind, "misc")
    warnings = _warn_missing(m, ["authors", "title", "year"] + (["venue"] if entry in ("article", "inproceedings") else []))
    fields: list[tuple[str, str]] = []
    if m.authors:
        fields.append(("author", " and ".join(
            bib_escape(f"{a.family}, {a.given}" if a.family and a.given else (a.family or a.name)) for a in m.authors)))
    if m.title:
        fields.append(("title", "{" + bib_escape(m.title) + "}"))  # protect capitalisation
    if m.venue:
        fields.append(({"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle"}.get(entry, "howpublished"),
                       bib_escape(m.venue)))
    if m.year:
        fields.append(("year", str(m.year)))
    for f, v in (("volume", m.volume), ("number", m.issue), ("pages", _pages(m.pages, en_dash=False)),
                 ("publisher", m.publisher), ("doi", m.doi)):
        if v:
            fields.append((f, bib_escape(v) if f != "doi" else v))
    if m.article_number and not m.pages:
        fields.append(("eid", bib_escape(m.article_number)))
    if m.arxiv_id:
        fields += [("eprint", m.arxiv_id), ("archivePrefix", "arXiv")]
    if not m.doi and m.url:
        fields.append(("url", m.url))
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
    text = f"@{entry}{{{bib_key(m)},\n{body}\n}}"
    return [CitationSegment(text=text)], warnings


def _tidy(seg: list[CitationSegment]) -> list[CitationSegment]:
    out = [s for s in seg if s.text]
    if out:
        out[-1] = CitationSegment(text=out[-1].text.rstrip(), italic=out[-1].italic)
    return out


def generate(paper: Paper, style: str, overrides: dict[str, Any] | None = None) -> CitationResponse:
    m = Meta(paper, overrides)
    fn = {"apa7": apa7, "ieee": ieee, "bibtex": bibtex}[style]
    segments, warnings = fn(m)
    if paper.conflicts and not overrides:
        warnings.append("Sources disagree on some metadata (" + ", ".join(c.field for c in paper.conflicts)
                        + ") – review before citing.")
    return CitationResponse(style=style, text="".join(s.text for s in segments), segments=segments, warnings=warnings)
