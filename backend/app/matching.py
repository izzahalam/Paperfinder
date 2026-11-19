"""Identity verification and ranking (SRS FR-17..20, NFR-09, R-01).

Rules (deterministic, explainable):

* DOI / arXiv-ID queries: a record whose identifier equals the query is
  **Exact** – DOI is strong evidence. Metadata conflicts are surfaced as
  warnings but do not hide the record.
* Title queries: title similarity alone can never produce **Exact**.
  Exact needs a near-identical title **plus** a user-supplied corroborating
  field (author or year) that matches **plus** a registered identifier, and no
  title conflict between sources.
* Keyword-only searches are relevance matches: **Unverified**.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .models import MatchInfo, MatchLabel, Paper
from .normalize import normalize_text

LABEL_ORDER = {MatchLabel.exact: 0, MatchLabel.probable: 1, MatchLabel.similar: 2, MatchLabel.unverified: 3}

NEAR_IDENTICAL = 97.0
CLOSE = 85.0
LOOSE = 60.0


@dataclass
class MatchContext:
    doi: str | None = None
    arxiv_id: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    venue: str | None = None
    keywords: str | None = None


def title_similarity(a: str | None, b: str | None) -> float:
    """Order-sensitive similarity (word order matters in titles: "Is X all you
    need?" is a different paper from "X is all you need"). A query that equals
    the main title of a "Title: subtitle" record scores as close, not identical."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    sim = fuzz.ratio(na, nb)
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 20 and long_.startswith(short + " "):
        sim = max(sim, 92.0)
    return float(sim)


def _author_matches(paper: Paper, wanted: list[str]) -> bool | None:
    if not wanted:
        return None
    have = {normalize_text(a.family or a.name.split(" ")[-1]) for a in paper.authors}
    have |= {normalize_text(a.name) for a in paper.authors}
    for w in wanted:
        nw = normalize_text(w)
        last = nw.split(" ")[-1] if nw else ""
        if last and (last in have or any(last in h.split(" ") for h in have)):
            return True
    return False


def _year_matches(paper: Paper, lo: int | None, hi: int | None) -> bool | None:
    if lo is None and hi is None:
        return None
    if paper.year is None:
        return None
    return (lo is None or paper.year >= lo) and (hi is None or paper.year <= hi)


def classify(paper: Paper, ctx: MatchContext) -> MatchInfo:
    ev: list[str] = []
    warn: list[str] = []
    conflict_fields = {c.field for c in paper.conflicts}
    if conflict_fields:
        warn.append("Sources disagree on: " + ", ".join(sorted(conflict_fields)) + " (see provenance)")

    # ---- identifier queries -------------------------------------------------
    if ctx.doi:
        if paper.doi and paper.doi == ctx.doi:
            srcs = [p.source for p in paper.provenance.get("doi", [])] or paper.sources
            ev.append(f"DOI {paper.doi} matches the query (reported by {', '.join(dict.fromkeys(srcs))})")
            return MatchInfo(label=MatchLabel.exact, score=1.0, evidence=ev, warnings=warn)
        if ctx.arxiv_id and paper.arxiv_id == ctx.arxiv_id:
            ev.append(f"arXiv ID {ctx.arxiv_id} matches the query's arXiv DOI")
            return MatchInfo(label=MatchLabel.exact, score=0.99, evidence=ev, warnings=warn)
        ev.append("Record DOI does not equal the queried DOI")
        return MatchInfo(label=MatchLabel.unverified, score=0.1, evidence=ev, warnings=warn)
    if ctx.arxiv_id and not ctx.title:
        if paper.arxiv_id == ctx.arxiv_id:
            ev.append(f"arXiv ID {ctx.arxiv_id} matches the query")
            return MatchInfo(label=MatchLabel.exact, score=1.0, evidence=ev, warnings=warn)
        ev.append("arXiv ID does not match the query")
        return MatchInfo(label=MatchLabel.unverified, score=0.1, evidence=ev, warnings=warn)

    # ---- metadata queries -----------------------------------------------------
    author_ok = _author_matches(paper, ctx.authors)
    year_ok = _year_matches(paper, ctx.year_from, ctx.year_to)
    venue_ok = None
    if ctx.venue:
        venue_ok = bool(paper.venue) and fuzz.partial_ratio(normalize_text(ctx.venue), normalize_text(paper.venue)) >= 85

    if author_ok:
        ev.append("Author matches the query")
    elif author_ok is False:
        warn.append("No listed author matches the queried author")
    if year_ok:
        ev.append(f"Year {paper.year} is within the queried range")
    elif year_ok is False:
        warn.append(f"Year {paper.year} is outside the queried range")
    if venue_ok:
        ev.append("Venue matches the query")
    elif venue_ok is False:
        warn.append("Venue does not match the query")

    if not ctx.title:
        ev.insert(0, "Found by keyword/author search – identity is not assessed")
        score = 0.3 + 0.1 * sum(bool(x) for x in (author_ok, year_ok, venue_ok))
        return MatchInfo(label=MatchLabel.unverified, score=round(score, 3), evidence=ev, warnings=warn)

    sim = title_similarity(ctx.title, paper.title)
    ev.insert(0, f"Title similarity {sim:.0f}%")
    has_id = bool(paper.doi or paper.arxiv_id)
    multi = len(paper.sources) >= 2
    if has_id:
        ev.append("Has a registered identifier (" + ("DOI" if paper.doi else "arXiv ID") + ")")
    if multi:
        ev.append(f"{len(paper.sources)} independent sources agree on this record")
    corroborated = bool(author_ok or year_ok)
    contradicted = author_ok is False or year_ok is False

    if sim >= NEAR_IDENTICAL:
        if corroborated and has_id and not contradicted and "title" not in conflict_fields:
            label = MatchLabel.exact
        elif (has_id or multi) and not contradicted:
            label = MatchLabel.probable
            if not corroborated:
                ev.append("Add an author or year to allow an Exact match (title alone is not sufficient)")
        else:
            label = MatchLabel.similar
    elif sim >= CLOSE:
        label = MatchLabel.probable if (corroborated and has_id and not contradicted) else MatchLabel.similar
    elif sim >= LOOSE:
        label = MatchLabel.similar
    else:
        label = MatchLabel.unverified
        ev.append("Title differs substantially from the query")

    score = sim / 100.0 + 0.05 * bool(author_ok) + 0.03 * bool(year_ok) + 0.02 * multi - 0.1 * contradicted
    return MatchInfo(label=label, score=round(min(score, 1.0), 3), evidence=ev, warnings=warn)


def rank(papers: list[Paper]) -> list[Paper]:
    """Exact identity first, then score, then corroboration (FR-17, FR-20)."""
    return sorted(
        papers,
        key=lambda p: (LABEL_ORDER[p.match.label], -p.match.score, -len(p.sources), -(p.year or 0)),
    )
