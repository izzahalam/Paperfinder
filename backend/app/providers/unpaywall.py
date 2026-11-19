"""Unpaywall adapter – legal open-access locations for a DOI."""

from __future__ import annotations

from urllib.parse import quote

from ..models import ProviderRecord, VersionCandidate
from ..normalize import clean_title, make_author, normalize_doi, safe_int, strip_markup
from .base import Ctx, Provider, version_from_label

API = "https://api.unpaywall.org/v2"


class UnpaywallProvider(Provider):
    name = "unpaywall"
    label = "Unpaywall"
    description = "Finds legal open-access copies by DOI. Requires a contact email."
    homepage = "https://unpaywall.org/products/api"
    supports_search = False

    def unavailable_reason(self, prefs, settings) -> str | None:
        if not prefs.contact_email:
            return "Unpaywall requires a contact email – set one in Settings to enable it."
        return None

    async def lookup_doi(self, ctx: Ctx, doi: str) -> list[ProviderRecord]:
        resp = await ctx.get(self.name, f"{API}/{quote(doi, safe='/')}",
                             params={"email": ctx.prefs.contact_email}, ok_404=True)
        if resp.status_code == 404:
            return []
        return [parse_record(resp.json())]


def parse_record(d: dict) -> ProviderRecord:
    doi = normalize_doi(d.get("doi"))
    authors = []
    for a in d.get("z_authors") or []:
        made = make_author(name=a.get("raw_author_name"), given=a.get("given"), family=a.get("family"),
                           orcid=a.get("ORCID"))
        if made:
            authors.append(made)
    cands = []
    for loc in d.get("oa_locations") or []:
        url = loc.get("url_for_pdf") or loc.get("url_for_landing_page") or loc.get("url")
        if not url:
            continue
        ev = [f"Unpaywall OA location (host: {loc.get('host_type') or 'unknown'}, version: {loc.get('version') or 'not stated'})"]
        if loc.get("repository_institution"):
            ev.append(f"Repository: {loc['repository_institution']}")
        if loc.get("oa_date"):
            ev.append(f"Open since {loc['oa_date']}")
        cands.append(VersionCandidate(
            url=url, is_pdf_url=bool(loc.get("url_for_pdf")), landing_page_url=loc.get("url_for_landing_page"),
            version=version_from_label(loc.get("version")), source="unpaywall", host_type=loc.get("host_type"),
            license=loc.get("license"), is_oa=True, evidence=ev,
        ))
    return ProviderRecord(
        source="unpaywall",
        source_id=doi,
        doi=doi,
        title=clean_title(d.get("title")),
        authors=authors,
        year=safe_int(d.get("year")),
        venue=strip_markup(d.get("journal_name")),
        publisher=strip_markup(d.get("publisher")),
        type=d.get("genre"),
        url=d.get("doi_url"),
        is_oa=d.get("is_oa"),
        candidates=cands,
    )
