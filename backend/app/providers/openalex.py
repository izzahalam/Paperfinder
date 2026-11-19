"""OpenAlex adapter – broad coverage, OA locations with version labels."""

from __future__ import annotations

from ..models import ProviderRecord, VersionCandidate
from ..normalize import (
    clean_title, make_author, normalize_arxiv_id, normalize_doi, normalize_pages, normalize_text,
    reconstruct_inverted_index, safe_int, strip_markup,
)
from .base import Ctx, Provider, Query, version_from_label

API = "https://api.openalex.org"


class OpenAlexProvider(Provider):
    name = "openalex"
    label = "OpenAlex"
    description = "Open scholarly index with open-access locations and version labels."
    homepage = "https://docs.openalex.org/"

    def _params(self, ctx: Ctx) -> dict:
        p: dict = {}
        if ctx.settings.openalex_api_key:
            p["api_key"] = ctx.settings.openalex_api_key
        if ctx.prefs.contact_email:
            p["mailto"] = ctx.prefs.contact_email
        return p

    async def lookup_doi(self, ctx: Ctx, doi: str) -> list[ProviderRecord]:
        resp = await ctx.get(self.name, f"{API}/works/doi:{doi}", params=self._params(ctx), ok_404=True)
        if resp.status_code == 404:
            return []
        return [parse_work(resp.json())]

    async def search(self, ctx: Ctx, q: Query) -> list[ProviderRecord]:
        params = self._params(ctx) | {"per-page": q.limit}
        filters = []
        if q.title:
            filters.append(f"title.search:{_clean_filter(q.title)}")
        if q.keywords:
            params["search"] = q.keywords
        if q.year_from or q.year_to:
            filters.append(f"publication_year:{q.year_from or ''}-{q.year_to or ''}")
        if q.authors:
            filters.append("raw_author_name.search:" + _clean_filter(" ".join(q.authors)))
        if filters:
            params["filter"] = ",".join(filters)
        resp = await ctx.get(self.name, f"{API}/works", params=params)
        recs = [parse_work(w) for w in resp.json().get("results") or []]
        if q.venue:
            ctx.notes.append("Venue criterion applied locally to OpenAlex results.")
            v = normalize_text(q.venue)
            recs = [r for r in recs if r.venue and v in normalize_text(r.venue)]
        return recs


def _clean_filter(s: str) -> str:
    # OpenAlex filter syntax uses ',' and '|' as separators.
    return s.replace(",", " ").replace("|", " ").replace(":", " ").strip()


def parse_work(w: dict) -> ProviderRecord:
    doi = normalize_doi(w.get("doi"))
    authors = []
    for au in w.get("authorships") or []:
        a = au.get("author") or {}
        made = make_author(name=a.get("display_name") or au.get("raw_author_name"), orcid=a.get("orcid"))
        if made:
            authors.append(made)
    prim = w.get("primary_location") or {}
    src = prim.get("source") or {}
    biblio = w.get("biblio") or {}
    arxiv_id = None
    candidates: list[VersionCandidate] = []
    for loc in w.get("locations") or []:
        landing = loc.get("landing_page_url")
        if landing and "arxiv.org/abs/" in landing:
            arxiv_id = arxiv_id or normalize_arxiv_id(landing.split("arxiv.org/abs/")[-1])
        if not loc.get("is_oa"):
            continue
        url = loc.get("pdf_url") or landing
        if not url:
            continue
        lsrc = loc.get("source") or {}
        candidates.append(VersionCandidate(
            url=url, is_pdf_url=bool(loc.get("pdf_url")), landing_page_url=landing,
            version=version_from_label(loc.get("version")), source="openalex",
            host_type=lsrc.get("type"), license=loc.get("license"), is_oa=True,
            evidence=[
                f"OpenAlex marks this location open access (version: {loc.get('version') or 'not stated'})",
                f"Host: {lsrc.get('display_name') or 'unknown'}",
            ],
        ))
    oa = w.get("open_access") or {}
    venue = src.get("display_name")
    return ProviderRecord(
        source="openalex",
        source_id=(w.get("id") or "").rsplit("/", 1)[-1] or None,
        doi=doi,
        arxiv_id=arxiv_id,
        title=clean_title(w.get("title") or w.get("display_name")),
        authors=authors,
        year=safe_int(w.get("publication_year")),
        venue=strip_markup(venue),
        publisher=strip_markup(src.get("host_organization_name")),
        type=w.get("type"),
        volume=biblio.get("volume"),
        issue=biblio.get("issue"),
        pages=normalize_pages(biblio.get("first_page"), biblio.get("last_page")),
        abstract=reconstruct_inverted_index(w.get("abstract_inverted_index")),
        url=prim.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None),
        is_oa=oa.get("is_oa"),
        candidates=candidates,
    )
