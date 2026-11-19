"""PDF validation (SRS FR-37/38/44, R-03/04/05/26, T-14..16).

``validate_file`` is a pure, top-level function so it can run in a separate
worker process (isolation from malformed/hostile PDFs). It never executes PDF
content; it only parses structure and extracts a bounded amount of text.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

MAX_PAGES_TO_READ = 3
MAX_TEXT_CHARS = 60_000


@dataclass
class Expected:
    title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    author_families: list[str] = field(default_factory=list)
    year: int | None = None


@dataclass
class CheckResult:
    state: str  # Verified | Verification Limited | Rejected | Failed
    details: list[str]
    pages: int | None = None
    pdf_title: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sniff(head: bytes) -> str | None:
    """Return a rejection reason if the first bytes are clearly not a PDF."""
    if b"%PDF-" in head[:1024]:
        return None
    low = head[:2048].lower().lstrip()
    if low.startswith(b"<!doctype html") or low.startswith(b"<html") or b"<html" in low[:512]:
        return "The server returned an HTML page, not a PDF (often a login, paywall or error page)."
    if low.startswith(b"<?xml") or low.startswith(b"{"):
        return "The server returned XML/JSON, not a PDF."
    return "File does not have a PDF signature (%PDF-)."


def _limit_resources() -> None:  # pragma: no cover - platform specific
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    except (ImportError, ValueError, OSError):
        pass


def worker_init() -> None:  # pragma: no cover - runs in child process
    _limit_resources()


def validate_file(path: str, expected: dict) -> dict:
    """Validate a downloaded file against the target paper. Returns CheckResult dict."""
    exp = Expected(**expected)
    details: list[str] = []
    with open(path, "rb") as fh:
        head = fh.read(2048)
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 2048))
        tail = fh.read()
    if size == 0:
        return CheckResult("Failed", ["The downloaded file is empty."]).to_dict()
    reason = sniff(head)
    if reason:
        return CheckResult("Rejected", [reason]).to_dict()
    details.append("PDF signature present")
    if b"%%EOF" not in tail:
        return CheckResult("Failed", details + ["File appears truncated or corrupt (no end-of-file marker)."]).to_dict()

    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:  # pragma: no cover
        return CheckResult("Verification Limited", details + ["PDF parser unavailable; content not checked."]).to_dict()

    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return CheckResult("Verification Limited",
                                   details + ["PDF is encrypted; content could not be compared."]).to_dict()
        pages = len(reader.pages)
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as e:
        return CheckResult("Failed", details + [f"PDF could not be parsed ({type(e).__name__})."]).to_dict()
    except Exception as e:  # any parser crash is a validation failure, never an app crash
        return CheckResult("Failed", details + [f"PDF parser error ({type(e).__name__})."]).to_dict()
    if pages == 0:
        return CheckResult("Failed", details + ["PDF has no pages."]).to_dict()
    details.append(f"Parsed successfully ({pages} page{'s' if pages != 1 else ''})")

    pdf_title = None
    try:
        meta = reader.metadata
        pdf_title = (meta.title if meta else None) or None
    except Exception:
        pass

    text_parts: list[str] = []
    total = 0
    for i in range(min(pages, MAX_PAGES_TO_READ)):
        try:
            t = reader.pages[i].extract_text() or ""
        except Exception:
            t = ""
        text_parts.append(t)
        total += len(t)
        if total > MAX_TEXT_CHARS:
            break
    raw_text = "\n".join(text_parts)[:MAX_TEXT_CHARS]
    text = _norm(raw_text)
    compact = re.sub(r"\s+", "", raw_text.lower())

    if len(text) < 200:
        return CheckResult("Verification Limited",
                           details + ["Little or no extractable text (possibly scanned); content could not be compared "
                                      "with the paper."], pages, pdf_title).to_dict()

    from rapidfuzz import fuzz

    doi_found = bool(exp.doi) and exp.doi.lower() in compact
    arxiv_found = bool(exp.arxiv_id) and exp.arxiv_id.lower() in compact
    title_score = 0.0
    if exp.title:
        nt = _norm(exp.title)
        if nt and len(nt) < 30:
            # Short titles fuzzy-match almost any long text: require the exact phrase.
            title_score = 100.0 if f" {nt} " in f" {text[:20000]} " else min(
                59.0, fuzz.partial_ratio(nt, text[:20000]))
        elif nt:
            title_score = fuzz.partial_ratio(nt, text[:20000])
        if pdf_title and nt:
            title_score = max(title_score, fuzz.ratio(nt, _norm(pdf_title)))
    authors_found = [a for a in exp.author_families if a and _norm(a) and _norm(a) in text]

    if doi_found:
        details.append(f"DOI {exp.doi} found in the document text")
    if arxiv_found:
        details.append(f"arXiv ID {exp.arxiv_id} found in the document text")
    if exp.title:
        details.append(f"Title match {title_score:.0f}%")
    if exp.author_families:
        details.append(f"{len(authors_found)}/{min(len(exp.author_families), 5)} checked author names found")

    title_ok = title_score >= 90
    author_ok = bool(authors_found) or not exp.author_families
    if doi_found or arxiv_found:
        if exp.title and title_score < 60:
            return CheckResult("Verification Limited", details + [
                "Identifier found but the title does not match – the file may be a supplement or a different item."
            ], pages, pdf_title).to_dict()
        return CheckResult("Verified", details, pages, pdf_title).to_dict()
    if title_ok and author_ok:
        return CheckResult("Verified", details, pages, pdf_title).to_dict()
    no_title = not exp.title or title_score < 60 or (not title_ok and exp.author_families and not authors_found)
    if exp.title and no_title and not authors_found:
        return CheckResult("Rejected", details + [
            "The document's content does not match the target paper (likely the wrong PDF)."
        ], pages, pdf_title).to_dict()
    return CheckResult("Verification Limited", details + [
        "Partial match only – review the file before relying on it."
    ], pages, pdf_title).to_dict()
