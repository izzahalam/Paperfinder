"""Input validation and normalisation helpers (pure functions, fully testable)."""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from .models import Author

# DOI syntax: 10.<registrant>/<suffix>. We accept the common printable suffix set.
_DOI_CORE = r"10\.\d{4,9}/[^\s\"<>]+"
DOI_RE = re.compile(rf"^{_DOI_CORE}$", re.IGNORECASE)
DOI_SEARCH_RE = re.compile(rf"({_DOI_CORE})", re.IGNORECASE)

# New-style (2007+) and old-style arXiv identifiers, optional version.
ARXIV_NEW = r"\d{4}\.\d{4,5}(?:v\d+)?"
ARXIV_OLD = r"[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
ARXIV_ID_RE = re.compile(rf"^(?:arxiv:)?({ARXIV_NEW}|{ARXIV_OLD})$", re.IGNORECASE)
ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)

_TRAILING_PUNCT = ".,;:)]}>'\""


def normalize_doi(raw: str | None) -> str | None:
    """Return a canonical lowercase DOI or ``None`` when the input is not a DOI.

    Accepts bare DOIs, ``doi:`` prefixes and doi.org URLs.
    """
    if not raw:
        return None
    s = unquote(raw.strip())
    s = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi:\s*", "", s, flags=re.IGNORECASE)
    s = s.strip().rstrip(_TRAILING_PUNCT)
    if not DOI_RE.match(s):
        return None
    return s.lower()


def normalize_arxiv_id(raw: str | None, keep_version: bool = False) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    m = ARXIV_DOI_RE.match(s)
    if m:
        s = m.group(1)
    m = ARXIV_ID_RE.match(s)
    if not m:
        return None
    ident = m.group(1)
    if not keep_version:
        ident = re.sub(r"v\d+$", "", ident)
    return ident.lower() if "/" not in ident else ident


def doi_to_arxiv_id(doi: str | None) -> str | None:
    if not doi:
        return None
    m = ARXIV_DOI_RE.match(doi)
    return normalize_arxiv_id(m.group(1)) if m else None


class ParsedIdentifier:
    """Result of classifying a DOI/URL-mode input."""

    def __init__(self, kind: str, value: str | None, url: str | None = None, note: str = ""):
        self.kind = kind  # "doi" | "arxiv" | "url" | "invalid"
        self.value = value
        self.url = url
        self.note = note

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ParsedIdentifier({self.kind!r}, {self.value!r})"


def parse_identifier(raw: str) -> ParsedIdentifier:
    """Classify a DOI/URL input without making network calls."""
    s = raw.strip()
    if not s:
        return ParsedIdentifier("invalid", None, note="Input is empty.")
    if len(s) > 2000:
        return ParsedIdentifier("invalid", None, note="Input is too long.")

    doi = normalize_doi(s)
    if doi:
        ax = doi_to_arxiv_id(doi)
        return ParsedIdentifier("doi", doi, note="arXiv DOI" if ax else "")

    ax = normalize_arxiv_id(s)
    if ax:
        return ParsedIdentifier("arxiv", ax)

    if re.match(r"^[a-z][a-z0-9+.\-]*://", s, re.IGNORECASE):
        try:
            parts = urlsplit(s)
        except ValueError:
            return ParsedIdentifier("invalid", None, note="URL could not be parsed.")
        if parts.scheme.lower() not in ("http", "https"):
            return ParsedIdentifier("invalid", None, note="Only http and https URLs are supported.")
        if not parts.hostname:
            return ParsedIdentifier("invalid", None, note="URL has no host name.")
        host = parts.hostname.lower()
        path = unquote(parts.path)
        # arXiv abs/pdf links
        if host.endswith("arxiv.org"):
            m = re.match(r"^/(?:abs|pdf|html)/(.+?)(?:\.pdf)?/?$", path)
            if m and normalize_arxiv_id(m.group(1)):
                return ParsedIdentifier("arxiv", normalize_arxiv_id(m.group(1)), url=s)
        # DOI embedded in URL path or query (most publisher sites)
        m = DOI_SEARCH_RE.search(path + ("?" + unquote(parts.query) if parts.query else ""))
        if m:
            cand = m.group(1)
            # Strip common publisher path suffixes after the DOI.
            cand = re.sub(r"/(?:full|abstract|pdf|epdf|html|meta|references|figures)(?:/.*)?$", "", cand, flags=re.I)
            cand = re.sub(r"\.pdf$", "", cand, flags=re.I)
            d = normalize_doi(cand)
            if d:
                return ParsedIdentifier("doi", d, url=s, note="DOI extracted from URL")
        return ParsedIdentifier("url", None, url=s)

    if s.lower().startswith("10."):
        return ParsedIdentifier("invalid", None, note="This looks like a DOI but its format is invalid (expected 10.xxxx/suffix).")
    return ParsedIdentifier(
        "invalid", None, note="Enter a DOI (e.g. 10.1038/nature14539), an arXiv ID, or an http(s) URL."
    )


# ---------------------------------------------------------------- text helpers

_INLINE_TAG_RE = re.compile(
    r"</?(?:[a-z]+:)?(?:i|b|u|em|strong|sup|sub|sc|span|italic|bold|underline|small-caps|monospace|inline-formula)"
    r"(?:\s[^>]*)?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_markup(s: str | None) -> str | None:
    """Remove JATS/HTML tags from provider text and collapse whitespace."""
    if s is None:
        return None
    s = _INLINE_TAG_RE.sub("", s)  # inline formatting must not split words (H<sub>2</sub>O)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    # Crossref JATS abstracts often start with a literal "Abstract" heading.
    s = re.sub(r"^(?:abstract)[\s.:]+", "", s, flags=re.IGNORECASE)
    return s or None


def clean_title(s: str | None) -> str | None:
    s = strip_markup(s)
    return (s.rstrip(".").strip() or None) if s else None


def normalize_text(s: str | None) -> str:
    """Aggressive normalisation for comparisons: ascii-fold, lowercase, alnum only."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return _WS_RE.sub(" ", s).strip()


def reconstruct_inverted_index(inv: dict[str, list[int]] | None) -> str | None:
    """OpenAlex ships abstracts as {word: [positions]}; rebuild the text."""
    if not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(w for _, w in positions)


_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "da", "di", "la", "le", "du", "dos", "bin", "al"}


def split_name(name: str) -> tuple[str | None, str | None]:
    """Best-effort split of a display name into (given, family).

    Used only for providers that return a single name string. Returns
    ``(None, name)`` for single-token names.
    """
    name = _WS_RE.sub(" ", name.strip())
    if not name:
        return None, None
    if "," in name:
        family, _, given = name.partition(",")
        return (given.strip() or None), family.strip() or None
    parts = name.split(" ")
    if len(parts) == 1:
        return None, parts[0]
    # Keep lowercase nobiliary particles with the family name.
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in _PARTICLES:
        i -= 1
    return " ".join(parts[:i]), " ".join(parts[i:])


def make_author(name: str | None = None, given: str | None = None, family: str | None = None,
                orcid: str | None = None) -> Author | None:
    given = (given or "").strip() or None
    family = (family or "").strip() or None
    if not name:
        name = " ".join(p for p in (given, family) if p)
    name = _WS_RE.sub(" ", (name or "")).strip()
    if not name:
        return None
    if not family and not given:
        given, family = split_name(name)
    if orcid:
        m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", orcid)
        orcid = m.group(1) if m else None
    return Author(name=name, given=given, family=family, orcid=orcid)


def author_key(a: Author) -> str:
    return normalize_text(a.family or a.name.split(" ")[-1])


def normalize_pages(first: str | None, last: str | None = None) -> str | None:
    first = (first or "").strip()
    last = (last or "").strip()
    if first and last and last != first:
        return f"{first}-{last}"
    return first or None


def safe_int(v) -> int | None:
    try:
        i = int(str(v)[:4]) if v is not None else None
    except (TypeError, ValueError):
        return None
    return i if i and 1000 <= i <= 2100 else None
