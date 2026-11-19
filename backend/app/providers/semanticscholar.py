"""Semantic Scholar Academic Graph adapter."""

from __future__ import annotations

from urllib.parse import quote

from ..models import ProviderRecord, VersionCandidate, VersionLabel
from ..normalize import clean_title, make_author, normalize_arxiv_id, normalize_doi, normalize_text, safe_int, strip_markup
from .base import Ctx, Provider, Query

API = "https://api.semanticscholar.org/graph/v1"
FIELDS = "title,authors,year,venue,externalIds,abstract,openAccessPdf,publicationTypes,journal,isOpenAccess,url"


class SemanticScholarProvider(Provider):
    name = "semanticscholar"
    label = "Semantic Scholar"
    description = "AI2 academic graph with abstracts and open-access PDF links."
    homepage = "https://api.semanticscholar.org/api-docs/graph"
    supports_arxiv = True

    def _headers(self, ctx: Ctx) -> dict:
        return {"x-api-key": ctx.settings.s2_api_key} if ctx.settings.s2_api_key else {}

    async def _paper(self, ctx: Ctx, ident: str) -> list[ProviderRecord]:
        resp = await ctx.get(self.name, f"{API}/paper/{quote(ident, safe=':/')}",
                             params={"fields": FIELDS}, headers=self._headers(ctx), ok_404=True)
        if resp.status_code == 404:
            return []
        return [parse_paper(resp.json())]

    async def lookup_doi(self, ctx: Ctx, doi: str) -> list[ProviderRecord]:
        return await self._paper(ctx, f"DOI:{doi}")

    async def lookup_arxiv(self, ctx: Ctx, arxiv_id: str) -> list[ProviderRecord]:
        return await self._paper(ctx, f"ARXIV:{arxiv_id}")

    async def search(self, ctx: Ctx, q: Query) -> list[ProviderRecord]:
        text = " ".join(p for p in (q.title, q.keywords) if p) or " ".join(q.authors)
        params: dict = {"query": text[:300], "limit": q.limit, "fields": FIELDS}
        if q.year_from or q.year_to:
            params["year"] = f"{q.year_from or ''}-{q.year_to or ''}"
        if q.venue:
            params["venue"] = q.venue
        resp = await ctx.get(self.name, f"{API}/paper/search", params=params, headers=self._headers(ctx))
        recs = [parse_paper(p) for p in resp.json().get("data") or []]
        if q.authors and (q.title or q.keywords):
            ctx.notes.append("Author criterion applied locally to Semantic Scholar results.")
            keys = [normalize_text(a).split(" ")[-1] for a in q.authors]
            recs = [r for r in recs if any(k and k in normalize_text(" ".join(x.name for x in r.authors)) for k in keys)]
        return recs


def parse_paper(p: dict) -> ProviderRecord:
    ext = p.get("externalIds") or {}
    doi = normalize_doi(ext.get("DOI"))
    arxiv_id = normalize_arxiv_id(ext.get("ArXiv")) if ext.get("ArXiv") else None
    journal = p.get("journal") or {}
    authors = [a for a in (make_author(name=x.get("name")) for x in p.get("authors") or []) if a]
    types = p.get("publicationTypes") or []
    rec = ProviderRecord(
        source="semanticscholar",
        source_id=p.get("paperId"),
        doi=doi,
        arxiv_id=arxiv_id,
        title=clean_title(p.get("title")),
        authors=authors,
        year=safe_int(p.get("year")),
        venue=strip_markup(journal.get("name") or p.get("venue")) or None,
        type=types[0] if types else None,
        volume=(journal.get("volume") or "").strip() or None,
        pages=(journal.get("pages") or "").strip() or None,
        abstract=strip_markup(p.get("abstract")),
        url=p.get("url"),
        is_oa=p.get("isOpenAccess"),
    )
    pdf = p.get("openAccessPdf") or {}
    if pdf.get("url"):
        rec.candidates.append(VersionCandidate(
            url=pdf["url"], is_pdf_url=True, version=VersionLabel.unknown, source="semanticscholar",
            license=pdf.get("license"), is_oa=True if p.get("isOpenAccess") else None,
            evidence=[f"Semantic Scholar openAccessPdf link (status: {pdf.get('status') or 'not stated'})",
                      "Version not stated by source"],
        ))
    return rec
