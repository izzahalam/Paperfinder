"""arXiv API adapter (Atom feed)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..models import ProviderRecord, VersionCandidate, VersionLabel
from ..normalize import clean_title, doi_to_arxiv_id, make_author, normalize_arxiv_id, normalize_doi, safe_int, strip_markup
from .base import Ctx, NotSupported, Provider, Query

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivProvider(Provider):
    name = "arxiv"
    label = "arXiv"
    description = "Preprint server (author-submitted versions)."
    homepage = "https://info.arxiv.org/help/api/"
    supports_arxiv = True

    async def lookup_arxiv(self, ctx: Ctx, arxiv_id: str) -> list[ProviderRecord]:
        resp = await ctx.get(self.name, API, params={"id_list": arxiv_id, "max_results": 1})
        return parse_feed(resp.text)

    async def lookup_doi(self, ctx: Ctx, doi: str) -> list[ProviderRecord]:
        ax = doi_to_arxiv_id(doi)
        if not ax:
            raise NotSupported("arXiv cannot look up publisher DOIs (only arXiv DOIs 10.48550/arXiv.*).")
        return await self.lookup_arxiv(ctx, ax)

    async def search(self, ctx: Ctx, q: Query) -> list[ProviderRecord]:
        parts = []
        if q.title:
            parts.append(f'ti:"{_esc(q.title)}"' if len(q.title.split()) > 1 else f"ti:{_esc(q.title)}")
        for a in q.authors:
            parts.append(f'au:"{_esc(a)}"')
        if q.keywords:
            parts.append(" AND ".join(f"all:{_esc(w)}" for w in q.keywords.split()[:8]))
        if q.year_from or q.year_to:
            lo = f"{q.year_from or 1991}01010000"
            hi = f"{q.year_to or 2100}12312359"
            parts.append(f"submittedDate:[{lo} TO {hi}]")
        if not parts:
            raise NotSupported("arXiv needs a title, author or keyword.")
        if q.venue:
            ctx.notes.append("arXiv does not support venue search; venue ignored for arXiv.")
        resp = await ctx.get(self.name, API, params={
            "search_query": " AND ".join(parts), "max_results": q.limit, "sortBy": "relevance",
        })
        return parse_feed(resp.text)


def _esc(s: str) -> str:
    return re.sub(r'["():\[\]]', " ", s).strip()


def parse_feed(xml_text: str) -> list[ProviderRecord]:
    if len(xml_text) > 10_000_000:
        raise ValueError("arXiv response too large")
    root = ET.fromstring(xml_text)
    out: list[ProviderRecord] = []
    for e in root.findall("a:entry", NS):
        raw_id = (e.findtext("a:id", default="", namespaces=NS) or "").strip()
        m = re.search(r"arxiv\.org/abs/(.+)$", raw_id)
        if not m:
            continue  # error entries have no abs URL
        arxiv_id = normalize_arxiv_id(m.group(1))
        if not arxiv_id:
            continue
        title = clean_title(e.findtext("a:title", default="", namespaces=NS))
        if not title or title.lower() == "error":
            continue
        authors = [a for a in (make_author(name=x.findtext("a:name", default="", namespaces=NS))
                               for x in e.findall("a:author", NS)) if a]
        published = e.findtext("a:published", default="", namespaces=NS)
        doi = normalize_doi(e.findtext("arxiv:doi", default="", namespaces=NS))
        journal_ref = strip_markup(e.findtext("arxiv:journal_ref", default="", namespaces=NS))
        pdf_url = None
        for link in e.findall("a:link", NS):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href")
        pdf_url = pdf_url or f"https://arxiv.org/pdf/{arxiv_id}"
        rec = ProviderRecord(
            source="arxiv",
            source_id=arxiv_id,
            doi=doi,  # publisher DOI when the author linked one
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            year=safe_int(published[:4]),
            venue=journal_ref,
            publisher=None,
            type="preprint",
            abstract=strip_markup(e.findtext("a:summary", default="", namespaces=NS)),
            url=f"https://arxiv.org/abs/{arxiv_id}",
            is_oa=True,
            candidates=[VersionCandidate(
                url=pdf_url.replace("http://", "https://"), is_pdf_url=True,
                landing_page_url=f"https://arxiv.org/abs/{arxiv_id}",
                version=VersionLabel.preprint, source="arxiv", host_type="repository", is_oa=True,
                evidence=["Hosted on arXiv (author-submitted preprint; may differ from the published version)"],
            )],
        )
        out.append(rec)
    return out
