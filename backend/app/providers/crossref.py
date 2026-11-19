"""Crossref REST API adapter – authoritative DOI registration metadata."""

from __future__ import annotations

from urllib.parse import quote

from ..models import Author, ProviderRecord, VersionCandidate, VersionLabel
from ..normalize import clean_title, make_author, normalize_doi, safe_int, strip_markup
from .base import Ctx, Provider, Query

API = "https://api.crossref.org"
SELECT = ",".join([
    "DOI", "title", "subtitle", "author", "issued", "published", "published-print", "published-online",
    "container-title", "publisher", "type", "volume", "issue", "page", "article-number",
    "abstract", "URL", "link", "license",
])


class CrossrefProvider(Provider):
    name = "crossref"
    label = "Crossref"
    description = "DOI registration agency metadata (authoritative for DOIs)."
    homepage = "https://www.crossref.org/documentation/retrieve-metadata/rest-api/"

    def _params(self, ctx: Ctx) -> dict:
        return {"mailto": ctx.prefs.contact_email} if ctx.prefs.contact_email else {}

    async def lookup_doi(self, ctx: Ctx, doi: str) -> list[ProviderRecord]:
        resp = await ctx.get(self.name, f"{API}/works/{quote(doi, safe='/')}", params=self._params(ctx), ok_404=True)
        if resp.status_code == 404:
            return []
        return [parse_work(resp.json().get("message") or {})]

    async def search(self, ctx: Ctx, q: Query) -> list[ProviderRecord]:
        params = self._params(ctx) | {"rows": q.limit, "select": SELECT}
        if q.text():
            params["query.bibliographic"] = q.text()
        if q.authors:
            params["query.author"] = " ".join(q.authors)
        if q.venue:
            params["query.container-title"] = q.venue
        filters = []
        if q.year_from:
            filters.append(f"from-pub-date:{q.year_from}")
        if q.year_to:
            filters.append(f"until-pub-date:{q.year_to}")
        if filters:
            params["filter"] = ",".join(filters)
        resp = await ctx.get(self.name, f"{API}/works", params=params)
        items = (resp.json().get("message") or {}).get("items") or []
        return [parse_work(it) for it in items]


def _year(w: dict) -> int | None:
    for key in ("issued", "published", "published-print", "published-online"):
        parts = (w.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return safe_int(parts[0][0])
    return None


def _first(v) -> str | None:
    if isinstance(v, list):
        return strip_markup(v[0]) if v else None
    return strip_markup(v) if v else None


def parse_work(w: dict) -> ProviderRecord:
    title = _first(w.get("title"))
    subtitle = _first(w.get("subtitle"))
    if title and subtitle and subtitle.lower() not in title.lower():
        title = f"{title}: {subtitle}"
    authors: list[Author] = []
    for a in w.get("author") or []:
        au = make_author(name=a.get("name"), given=a.get("given"), family=a.get("family"), orcid=a.get("ORCID"))
        if au:
            authors.append(au)
    doi = normalize_doi(w.get("DOI"))
    rec = ProviderRecord(
        source="crossref",
        source_id=doi,
        doi=doi,
        title=clean_title(title),
        authors=authors,
        year=_year(w),
        venue=_first(w.get("container-title")),
        publisher=strip_markup(w.get("publisher")),
        type=w.get("type"),
        volume=strip_markup(w.get("volume")),
        issue=strip_markup(w.get("issue")),
        pages=strip_markup(w.get("page")),
        article_number=strip_markup(w.get("article-number")),
        abstract=strip_markup(w.get("abstract")),
        url=w.get("URL"),
    )
    # Only offer publisher full-text links when Crossref records an open
    # (Creative Commons) licence – other links usually require a subscription.
    cc = [lic.get("URL", "") for lic in (w.get("license") or []) if "creativecommons.org" in (lic.get("URL") or "")]
    if cc:
        rec.is_oa = True
        for link in w.get("link") or []:
            if (link.get("content-type") or "").lower() == "application/pdf" and link.get("URL"):
                rec.candidates.append(VersionCandidate(
                    url=link["URL"], is_pdf_url=True, landing_page_url=rec.url,
                    version=VersionLabel.vor if link.get("content-version") == "vor" else VersionLabel.unknown,
                    source="crossref", host_type="publisher", license=cc[0], is_oa=True,
                    evidence=[f"Crossref records a Creative Commons licence ({cc[0]})",
                              f"Publisher full-text link (content-version: {link.get('content-version') or 'unspecified'})"],
                ))
    return rec
