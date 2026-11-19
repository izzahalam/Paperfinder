"""Aggregation: deduplicate provider records into papers, keep provenance,
surface conflicts and classify access status from evidence.

SRS: FR-14/15 (aggregate, dedupe by DOI then metadata), FR-28/29 (provenance,
conflicts), FR-35 (access status with evidence), R-10 (version confusion),
R-13 (no silent overwrite), R-21 (duplicates).
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

from rapidfuzz import fuzz

from .models import (
    AbstractRecord, AccessInfo, AccessStatus, Conflict, Paper, ProviderRecord, ProvValue,
    VersionCandidate, VersionLabel,
)
from .normalize import author_key, doi_to_arxiv_id, normalize_text

# Metadata priority (most authoritative first). Abstract priority differs.
FIELD_PRIORITY = ["crossref", "openalex", "semanticscholar", "unpaywall", "arxiv"]
ABSTRACT_PRIORITY = ["crossref", "semanticscholar", "arxiv", "openalex"]
SCALAR_FIELDS = ["title", "year", "venue", "publisher", "type", "volume", "issue", "pages", "article_number", "url"]


def stable_id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _rank(source: str, order: list[str]) -> int:
    return order.index(source) if source in order else len(order)


def _fingerprint(r: ProviderRecord) -> str | None:
    t = normalize_text(r.title)
    if not t:
        return None
    first = author_key(r.authors[0]) if r.authors else ""
    return f"{t}|{first}"


def _group_key(r: ProviderRecord) -> str | None:
    if r.doi:
        ax = doi_to_arxiv_id(r.doi)
        return f"arxiv:{ax}" if ax else f"doi:{r.doi}"
    if r.arxiv_id:
        return f"arxiv:{r.arxiv_id}"
    fp = _fingerprint(r)
    return f"fp:{fp}" if fp else None


def group_records(records: list[ProviderRecord]) -> list[list[ProviderRecord]]:
    """Group records that describe the same work (DOI first, then IDs, then fingerprint)."""
    groups: "OrderedDict[str, list[ProviderRecord]]" = OrderedDict()
    alias: dict[str, str] = {}  # secondary key -> group key

    def find(keys: list[str]) -> str | None:
        for k in keys:
            if k in groups:
                return k
            if k in alias:
                return alias[k]
        return None

    for r in records:
        primary = _group_key(r)
        if primary is None:
            continue
        keys = [primary]
        # A record carrying both a publisher DOI and an arXiv id links the two.
        if r.arxiv_id and not primary.startswith("arxiv:"):
            keys.append(f"arxiv:{r.arxiv_id}")
        fp = _fingerprint(r)
        if fp and not r.doi:  # fingerprints only join DOI-less records
            keys.append(f"fp:{fp}")
        existing = find(keys)
        # Never merge two different publisher DOIs.
        if existing and existing.startswith("doi:") and primary.startswith("doi:") and existing != primary:
            existing = None
        target = existing or primary
        groups.setdefault(target, []).append(r)
        for k in keys:
            if k != target and k not in groups:
                alias.setdefault(k, target)
    return list(groups.values())


def _dedupe_candidates(cands: list[VersionCandidate]) -> list[VersionCandidate]:
    out: "OrderedDict[str, VersionCandidate]" = OrderedDict()
    for c in cands:
        key = c.url.replace("http://", "https://").rstrip("/").lower()
        if key in out:
            existing = out[key]
            # Keep the most specific version label and merge evidence.
            if existing.version == VersionLabel.unknown and c.version != VersionLabel.unknown:
                existing.version = c.version
            for e in c.evidence:
                if e not in existing.evidence:
                    existing.evidence.append(e)
            existing.license = existing.license or c.license
            continue
        c = c.model_copy(deep=True)
        c.id = stable_id("v_", key)
        out[key] = c
    return list(out.values())


def _conflicts(prov: dict[str, list[ProvValue]], authors_by_source: dict[str, list[str]]) -> list[Conflict]:
    conflicts: list[Conflict] = []

    def distinct(values: list[ProvValue], same) -> bool:
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                if not same(values[i].value, values[j].value):
                    return True
        return False

    title_vals = prov.get("title", [])
    if distinct(title_vals, lambda a, b: fuzz.ratio(normalize_text(a), normalize_text(b)) >= 90
                or normalize_text(a).startswith(normalize_text(b)) or normalize_text(b).startswith(normalize_text(a))):
        conflicts.append(Conflict(field="title", values=title_vals, note="Sources report noticeably different titles."))

    year_vals = prov.get("year", [])
    if distinct(year_vals, lambda a, b: a == b):
        years = sorted({v.value for v in year_vals})
        note = "Online and print publication years can differ by one." if years[-1] - years[0] == 1 else \
            "Sources report different publication years."
        conflicts.append(Conflict(field="year", values=year_vals, note=note))

    venue_vals = [v for v in prov.get("venue", []) if v.source != "arxiv"]
    if distinct(venue_vals, lambda a, b: fuzz.token_set_ratio(normalize_text(a), normalize_text(b)) >= 80):
        conflicts.append(Conflict(field="venue", values=venue_vals, note="Sources name different venues."))

    firsts = [ProvValue(source=s, value=names[0]) for s, names in authors_by_source.items() if names]
    if distinct(firsts, lambda a, b: normalize_text(a).split(" ")[-1:] == normalize_text(b).split(" ")[-1:]):
        conflicts.append(Conflict(field="first_author", values=firsts, note="Sources disagree on the first author."))
    return conflicts


def merge_group(group: list[ProviderRecord], failed_sources: list[str]) -> Paper:
    group = sorted(group, key=lambda r: _rank(r.source, FIELD_PRIORITY))
    dois = [r.doi for r in group if r.doi and not doi_to_arxiv_id(r.doi)]
    doi = dois[0] if dois else next((r.doi for r in group if r.doi), None)
    arxiv_id = next((r.arxiv_id for r in group if r.arxiv_id), None) or doi_to_arxiv_id(doi)
    if doi:
        pid = stable_id("p_", f"doi:{doi}")
    elif arxiv_id:
        pid = stable_id("p_", f"arxiv:{arxiv_id}")
    else:
        pid = stable_id("p_", f"fp:{_fingerprint(group[0])}|{group[0].year}")

    prov: dict[str, list[ProvValue]] = {}
    chosen: dict = {}
    for f in SCALAR_FIELDS:
        vals = [ProvValue(source=r.source, value=getattr(r, f)) for r in group if getattr(r, f) not in (None, "")]
        # arXiv's preprint "type"/"venue" must not override publisher metadata.
        if f in ("type", "venue") and any(v.source != "arxiv" for v in vals):
            non_ax = [v for v in vals if v.source != "arxiv"]
            chosen[f] = non_ax[0].value if non_ax else None
        else:
            chosen[f] = vals[0].value if vals else None
        if vals:
            prov[f] = vals

    authors_by_source = {r.source: [a.name for a in r.authors] for r in group if r.authors}
    author_src = next((r for r in group if r.authors), None)
    if author_src:
        prov["authors"] = [ProvValue(source=s, value=n) for s, n in authors_by_source.items()]
    if doi:
        prov["doi"] = [ProvValue(source=r.source, value=r.doi) for r in group if r.doi]

    # Abstract: from the identity-matched group only (R-15), with its source.
    checked = list(dict.fromkeys(r.source for r in group))
    abstract = AbstractRecord(state="unavailable", checked_sources=checked,
                              failed_sources=[s for s in failed_sources if s in ("crossref", "openalex", "semanticscholar", "arxiv")])
    for r in sorted(group, key=lambda r: _rank(r.source, ABSTRACT_PRIORITY)):
        if r.abstract and len(r.abstract) > 40:
            abstract = AbstractRecord(state="available", text=r.abstract, source=r.source,
                                      checked_sources=checked, failed_sources=abstract.failed_sources)
            break
    else:
        if abstract.failed_sources:
            abstract.state = "retrieval_failed"

    candidates = _dedupe_candidates([c for r in group for c in r.candidates])
    paper = Paper(
        id=pid, doi=doi, arxiv_id=arxiv_id,
        authors=author_src.authors if author_src else [],
        abstract=abstract,
        sources=checked,
        source_ids={r.source: r.source_id for r in group if r.source_id},
        provenance=prov,
        conflicts=_conflicts(prov, authors_by_source),
        candidates=candidates,
        **chosen,
    )
    paper.access = classify_access(paper, group)
    return paper


def link_preprints(papers: list[Paper]) -> list[Paper]:
    """Attach DOI-less arXiv preprints to the published paper with the same
    title and first author, as *separate version candidates* (R-10)."""
    published = [p for p in papers if p.doi and not doi_to_arxiv_id(p.doi)]
    keep: list[Paper] = []
    for p in papers:
        if p.doi or not p.arxiv_id or not p.title:
            keep.append(p)
            continue
        target = None
        for q in published:
            # A preprint normally precedes (or closely follows) publication.
            years_ok = p.year is not None and q.year is not None and -1 <= q.year - p.year <= 3
            if (years_ok and q.title and fuzz.ratio(normalize_text(p.title), normalize_text(q.title)) >= 95
                    and p.authors and q.authors and author_key(p.authors[0]) == author_key(q.authors[0])):
                target = q
                break
        if target is None:
            keep.append(p)
            continue
        for c in p.candidates:
            c = c.model_copy(deep=True)
            c.evidence.append(f"Linked to this paper by matching title and first author (arXiv:{p.arxiv_id})")
            if all(c.url != x.url for x in target.candidates):
                target.candidates.append(c)
        target.arxiv_id = target.arxiv_id or p.arxiv_id
        target.source_ids.setdefault("arxiv", p.arxiv_id)
        if "arxiv" not in target.sources:
            target.sources.append("arxiv")
        target.access = classify_access(target, [])
    return keep


def classify_access(paper: Paper, group: list[ProviderRecord]) -> AccessInfo:
    """Access status from evidence only; otherwise Unknown (NFR-10)."""
    oa = [c for c in paper.candidates if c.is_oa]
    vor = [c for c in oa if c.version == VersionLabel.vor]
    alt = [c for c in oa if c.version in (VersionLabel.accepted, VersionLabel.preprint)]
    unknown_ver = [c for c in oa if c.version == VersionLabel.unknown]
    if vor:
        return AccessInfo(status=AccessStatus.open_access,
                          evidence=[f"Open version of record reported by {', '.join(sorted({c.source for c in vor}))}"])
    if alt:
        kinds = sorted({c.version.value for c in alt})
        return AccessInfo(status=AccessStatus.alternative,
                          evidence=[f"Open {' / '.join(kinds)} reported by {', '.join(sorted({c.source for c in alt}))}",
                                    "No open version of record was found in the sources checked"])
    if unknown_ver:
        return AccessInfo(status=AccessStatus.unknown,
                          evidence=[f"An open copy is reported by {', '.join(sorted({c.source for c in unknown_ver}))}, "
                                    "but its version is not stated"])
    says_closed = [r.source for r in group if r.is_oa is False]
    says_open = [r.source for r in group if r.is_oa is True]
    if says_closed and not says_open:
        return AccessInfo(status=AccessStatus.closed,
                          evidence=[f"{', '.join(says_closed)} report no open-access copy",
                                    "A lawful repository copy may still exist elsewhere"])
    if says_open:
        return AccessInfo(status=AccessStatus.unknown,
                          evidence=[f"{', '.join(says_open)} flag the work as open access, but no usable location was given"])
    return AccessInfo(status=AccessStatus.unknown, evidence=["No access information from the sources checked"])


def aggregate(records: list[ProviderRecord], failed_sources: list[str]) -> list[Paper]:
    papers = [merge_group(g, failed_sources) for g in group_records(records)]
    return link_preprints(papers)
