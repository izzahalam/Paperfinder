"""Full-text discovery (SRS FR-31..36, UC-05, R-09/10/11).

Finds *lawful* open copies only: locations that a source explicitly reports as
open access (OpenAlex, Unpaywall, Semantic Scholar openAccessPdf, arXiv,
Crossref Creative-Commons links). Nothing behind access controls is attempted.
"""

from __future__ import annotations

import time

from .aggregate import _dedupe_candidates, classify_access
from .db import Database
from .models import Paper, ProviderRecord, SourceOutcome, SourceStatus, VersionCandidate, VersionLabel, utcnow
from .net import FetchError, RateLimitedError
from .orchestrator import SearchService
from .providers import REGISTRY, Ctx, NotSupported


async def discover_versions(paper: Paper, svc: SearchService, db: Database) -> tuple[Paper, list[SourceOutcome]]:
    prefs = db.get_preferences()
    client = svc.client(prefs)
    outcomes: list[SourceOutcome] = []
    deadline = time.monotonic() + prefs.search_budget_seconds
    records: list[ProviderRecord] = []

    plan: list[tuple[str, str]] = []
    if paper.doi:
        for name in ("unpaywall", "openalex", "semanticscholar"):
            if name in prefs.enabled_providers and (name == "unpaywall" or name not in paper.sources):
                plan.append((name, "doi"))
    if paper.arxiv_id and "arxiv" in prefs.enabled_providers and not any(c.source == "arxiv" for c in paper.candidates):
        plan.append(("arxiv", "arxiv"))

    for name, op in plan:
        prov = REGISTRY[name]
        o = SourceOutcome(provider=name, status=SourceStatus.running)
        outcomes.append(o)
        reason = prov.unavailable_reason(prefs, svc.settings)
        if reason:
            o.status, o.notes = SourceStatus.skipped, [reason]
            continue
        ctx = Ctx(client=client, prefs=prefs, settings=svc.settings, deadline=deadline,
                  on_attempt=lambda o=o: setattr(o, "attempts", o.attempts + 1))
        t0 = time.monotonic()
        try:
            recs = await (prov.lookup_doi(ctx, paper.doi) if op == "doi" else prov.lookup_arxiv(ctx, paper.arxiv_id))
            # Identity guard: only accept records for the same identifier.
            recs = [r for r in recs if (op == "doi" and r.doi == paper.doi) or
                    (op == "arxiv" and r.arxiv_id == paper.arxiv_id)]
            records.extend(recs)
            o.count = sum(len(r.candidates) for r in recs)
            o.status = SourceStatus.success if o.count else SourceStatus.no_results
        except NotSupported as e:
            o.status, o.notes = SourceStatus.skipped, [str(e)]
        except RateLimitedError as e:
            o.status, o.error = SourceStatus.rate_limited, str(e)
        except FetchError as e:
            o.status = SourceStatus.timeout if e.category == "timeout" else SourceStatus.failed
            o.error = str(e)
        except Exception:
            o.status, o.error = SourceStatus.failed, "The source returned data in an unexpected format."
        finally:
            o.duration_ms = int((time.monotonic() - t0) * 1000)
            o.finished_at = utcnow()

    new_cands = [c for r in records for c in r.candidates]
    if paper.arxiv_id and not any(c.source == "arxiv" for c in paper.candidates + new_cands):
        new_cands.append(VersionCandidate(
            url=f"https://arxiv.org/pdf/{paper.arxiv_id}", is_pdf_url=True,
            landing_page_url=f"https://arxiv.org/abs/{paper.arxiv_id}", version=VersionLabel.preprint,
            source="arxiv", host_type="repository", is_oa=True,
            evidence=[f"arXiv identifier {paper.arxiv_id} reported by {', '.join(paper.sources)}",
                      "arXiv hosts author-submitted preprints"],
        ))
    # Preserve verification state of candidates already checked.
    merged = _dedupe_candidates(paper.candidates + new_cands)
    old = {c.id: c for c in paper.candidates}
    for c in merged:
        if c.id in old:
            c.verification = old[c.id].verification
            c.verification_detail = old[c.id].verification_detail
    order = {VersionLabel.vor: 0, VersionLabel.accepted: 1, VersionLabel.preprint: 2, VersionLabel.unknown: 3}
    paper.candidates = sorted(merged, key=lambda c: (order[c.version], not c.is_pdf_url))
    for r in records:
        if r.source not in paper.sources:
            paper.sources.append(r.source)
        if r.source_id:
            paper.source_ids.setdefault(r.source, r.source_id)
    paper.access = classify_access(paper, records)
    db.upsert_papers([paper])
    return paper, outcomes
