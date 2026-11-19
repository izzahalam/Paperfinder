"""Search orchestration (SRS §4.3 workflow, FR-06..20, FR-82..88, NFR-03/05/30).

* Every enabled provider is queried concurrently inside a global time budget.
* Each provider gets its own ``SourceOutcome`` (attempted / success /
  no results / failed / timeout / rate-limited / skipped / cancelled).
* One provider failing never discards other providers' results.
* Results are re-aggregated as each provider finishes so the UI can show
  progressive, partial results; cancellation keeps completed results.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from html.parser import HTMLParser

from .aggregate import aggregate
from .config import Preferences, Settings
from .db import Database
from .matching import MatchContext, classify, rank
from .models import (
    JobStatus, ProviderRecord, SearchJob, SearchMode, SearchRequest, SourceOutcome, SourceStatus, utcnow,
)
from .net import FetchError, ProviderClient, RateLimitedError, SafeFetcher
from .normalize import doi_to_arxiv_id, normalize_doi, parse_identifier
from .providers import REGISTRY, Ctx, NotSupported, Query

log = logging.getLogger("paperfinder.search")

MAX_CONCURRENT_SEARCHES = 3
MAX_RESULTS = 100


class SearchInputError(ValueError):
    pass


class TooManySearches(RuntimeError):
    pass


class _MetaParser(HTMLParser):
    """Collect <meta name=... content=...> and <title> from a landing page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, []).append(a["content"].strip())
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(self.title) < 500:
            self.title += data


def parse_landing_page(html: str) -> dict:
    p = _MetaParser()
    try:
        p.feed(html[:2_000_000])
    except Exception:  # malformed HTML – use whatever was parsed
        pass
    doi = None
    for key in ("citation_doi", "dc.identifier", "prism.doi", "bepress_citation_doi", "dc.identifier.doi"):
        for v in p.meta.get(key, []):
            doi = normalize_doi(v)
            if doi:
                break
        if doi:
            break
    title = (p.meta.get("citation_title") or p.meta.get("dc.title") or p.meta.get("og:title") or [None])[0]
    pdf = (p.meta.get("citation_pdf_url") or [None])[0]
    return {"doi": doi, "title": title, "pdf_url": pdf}


class _Running:
    def __init__(self, job: SearchJob):
        self.job = job
        self.task: asyncio.Task | None = None
        self.records: dict[str, list[ProviderRecord]] = {}
        self.mctx = MatchContext()


class SearchService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self._client: ProviderClient | None = None
        self._client_key: tuple | None = None
        self._running: dict[str, _Running] = {}
        self._recent: dict[str, SearchJob] = {}  # finished jobs kept in memory briefly
        self.fetcher_factory = lambda prefs: SafeFetcher(prefs.contact_email, timeout=prefs.provider_timeout_seconds)
        self.transport = None  # tests inject an httpx MockTransport

    # ---------------------------------------------------------------- clients
    def client(self, prefs: Preferences) -> ProviderClient:
        key = (prefs.contact_email, prefs.provider_timeout_seconds, prefs.max_retries)
        if self._client is None or self._client_key != key:
            old = self._client
            self._client = ProviderClient(prefs.contact_email, prefs.provider_timeout_seconds, prefs.max_retries,
                                          transport=self.transport)
            self._client_key = key
            if old is not None:
                asyncio.get_event_loop().create_task(old.aclose())
        return self._client

    async def aclose(self) -> None:
        for r in list(self._running.values()):
            if r.task:
                r.task.cancel()
        if self._client:
            await self._client.aclose()

    # ---------------------------------------------------------------- API
    def validate(self, req: SearchRequest) -> None:
        if req.mode == SearchMode.doi_url:
            ident = parse_identifier(req.query or "")
            if ident.kind == "invalid":
                raise SearchInputError(ident.note)
        if req.providers is not None:
            unknown = [p for p in req.providers if p not in REGISTRY]
            if unknown:
                raise SearchInputError(f"Unknown provider(s): {', '.join(unknown)}")

    async def start(self, req: SearchRequest) -> SearchJob:
        self.validate(req)
        active = [r for r in self._running.values() if r.job.status == JobStatus.running]
        if len(active) >= MAX_CONCURRENT_SEARCHES:
            raise TooManySearches("Too many searches are running. Wait for one to finish or cancel it.")
        prefs = self.db.get_preferences()
        names = [p for p in (req.providers if req.providers is not None else prefs.enabled_providers) if p in REGISTRY]
        if not names:
            raise SearchInputError("No sources are enabled. Enable at least one source in Settings.")
        job = SearchJob(
            id="s_" + uuid.uuid4().hex[:16], mode=req.mode, request=req, status=JobStatus.running,
            started_at=utcnow(), sources=[SourceOutcome(provider=n) for n in names],
        )
        run = _Running(job)
        self._running[job.id] = run
        run.task = asyncio.create_task(self._run(run, prefs))
        return job

    def get(self, search_id: str) -> SearchJob | None:
        r = self._running.get(search_id)
        if r is not None:
            return r.job
        if search_id in self._recent:
            return self._recent[search_id]
        return self.db.get_search(search_id)

    async def cancel(self, search_id: str) -> SearchJob | None:
        r = self._running.get(search_id)
        if r is None:
            return self.get(search_id)
        if r.task and not r.task.done():
            r.task.cancel()
            try:
                await r.task
            except asyncio.CancelledError:
                pass
        return r.job

    async def wait(self, search_id: str) -> SearchJob | None:
        r = self._running.get(search_id)
        if r and r.task:
            try:
                await r.task
            except asyncio.CancelledError:
                pass
        return self.get(search_id)

    # ---------------------------------------------------------------- core
    async def _run(self, run: _Running, prefs: Preferences) -> None:
        job = run.job
        req = job.request
        start = time.monotonic()
        deadline = start + prefs.search_budget_seconds
        mctx = MatchContext()
        query = Query(limit=prefs.max_results_per_provider)
        op = "search"
        ident_value = None
        tasks: list[asyncio.Task] = []

        try:
            if req.mode == SearchMode.doi_url:
                ident = parse_identifier(req.query or "")
                if ident.note:
                    job.notes.append(ident.note)
                if ident.kind == "url":
                    ident = await self._resolve_url(job, ident.url, prefs)
                if ident.kind == "doi":
                    op, ident_value = "doi", ident.value
                    mctx.doi = ident.value
                    mctx.arxiv_id = doi_to_arxiv_id(ident.value)
                elif ident.kind == "arxiv":
                    op, ident_value = "arxiv", ident.value
                    mctx.arxiv_id = ident.value
                elif ident.kind == "title":
                    query.title = ident.value
                    mctx.title = ident.value
                else:
                    for o in job.sources:
                        o.status = SourceStatus.skipped
                        o.notes.append("No identifier could be extracted from the URL.")
                    job.notes.append("No DOI, arXiv ID or title could be found on that page. Try the paper's DOI or title.")
                    self._finish(run, failed=True)
                    return
            elif req.mode == SearchMode.title:
                query.title = req.query.strip()
                mctx.title = query.title
            else:
                query.title = (req.title or "").strip() or None
                query.authors = req.authors
                query.keywords = (req.keywords or "").strip() or None
                query.venue = (req.venue or "").strip() or None
                query.year_from, query.year_to = req.year_from, req.year_to
                mctx = MatchContext(title=query.title, authors=query.authors, year_from=req.year_from,
                                    year_to=req.year_to, venue=query.venue, keywords=query.keywords)
            if req.mode != SearchMode.advanced and (req.year_from or req.year_to):
                query.year_from, query.year_to = req.year_from, req.year_to

            run.mctx = mctx
            client = self.client(prefs)
            tasks = [
                asyncio.create_task(self._one(run, o, client, prefs, op, ident_value, query, deadline))
                for o in job.sources
            ]
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=max(1.0, deadline - time.monotonic()) + 2)
            except asyncio.TimeoutError:
                for o in job.sources:
                    if o.status in (SourceStatus.pending, SourceStatus.running):
                        o.status = SourceStatus.timeout
                        o.error = "Stopped: overall search time budget reached."
            self._finish(run)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            for o in job.sources:
                if o.status in (SourceStatus.pending, SourceStatus.running):
                    o.status = SourceStatus.cancelled
            self._finish(run, cancelled=True)
            raise
        except Exception:  # never crash the app on unexpected input (NFR-06)
            log.exception("search %s failed", job.id)
            job.notes.append("An unexpected error occurred while searching; partial results (if any) are shown.")
            self._finish(run, failed=True)

    async def _resolve_url(self, job: SearchJob, url: str, prefs: Preferences):
        from .normalize import ParsedIdentifier

        fetcher = self.fetcher_factory(prefs)
        try:
            _res, html = await asyncio.wait_for(fetcher.fetch_text(url), timeout=prefs.provider_timeout_seconds + 5)
        except (FetchError, asyncio.TimeoutError) as e:
            job.notes.append(f"Could not read the page at that URL: {e}")
            return ParsedIdentifier("invalid", None)
        meta = parse_landing_page(html)
        if meta["doi"]:
            job.notes.append(f"DOI {meta['doi']} found in the page's citation metadata.")
            return ParsedIdentifier("doi", meta["doi"])
        if meta["title"]:
            job.notes.append("No DOI on the page; searching by the page's citation title instead "
                             "(this is not an identifier match).")
            return ParsedIdentifier("title", meta["title"])
        return ParsedIdentifier("invalid", None)

    async def _one(self, run: _Running, o: SourceOutcome, client: ProviderClient, prefs: Preferences,
                   op: str, ident: str | None, query: Query, deadline: float) -> None:
        provider = REGISTRY[o.provider]
        reason = provider.unavailable_reason(prefs, self.settings)
        if reason:
            o.status, o.notes = SourceStatus.skipped, [reason]
            return
        if client.breaker_open(o.provider):
            o.status = SourceStatus.skipped
            o.notes = ["Temporarily paused after repeated failures; will retry automatically in about a minute."]
            return

        def bump() -> None:
            o.attempts += 1

        ctx = Ctx(client=client, prefs=prefs, settings=self.settings, deadline=deadline, on_attempt=bump)
        o.status = SourceStatus.running
        t0 = time.monotonic()
        try:
            if op == "doi":
                recs = await provider.lookup_doi(ctx, ident)
            elif op == "arxiv":
                if provider.supports_arxiv:
                    recs = await provider.lookup_arxiv(ctx, ident)
                elif o.provider == "openalex":
                    recs = await provider.lookup_doi(ctx, f"10.48550/arxiv.{ident}")
                else:
                    raise NotSupported(f"{provider.label} cannot look up arXiv identifiers.")
            else:
                if not provider.supports_search:
                    raise NotSupported(f"{provider.label} only supports DOI lookups; it is used for full-text discovery.")
                recs = await provider.search(ctx, query)
            recs = recs[: prefs.max_results_per_provider]
            run.records[o.provider] = recs
            o.count = len(recs)
            o.status = SourceStatus.success if recs else SourceStatus.no_results
        except NotSupported as e:
            o.status = SourceStatus.skipped
            o.notes.append(str(e))
        except RateLimitedError as e:
            o.status, o.error = SourceStatus.rate_limited, str(e)
        except FetchError as e:
            o.status = SourceStatus.timeout if e.category == "timeout" else SourceStatus.failed
            o.error = str(e)
        except asyncio.CancelledError:
            o.status = SourceStatus.cancelled
            raise
        except Exception as e:  # unexpected/malformed provider response (NFR-06, R-23)
            log.warning("provider %s returned unusable data: %r", o.provider, e)
            o.status = SourceStatus.failed
            o.error = "The source returned data in an unexpected format."
        finally:
            o.duration_ms = int((time.monotonic() - t0) * 1000)
            o.finished_at = utcnow()
            o.notes.extend(n for n in ctx.notes if n not in o.notes)
            self._refresh_results(run)

    def _refresh_results(self, run: _Running) -> None:
        job = run.job
        failed = [o.provider for o in job.sources if o.status in (SourceStatus.failed, SourceStatus.timeout,
                                                                 SourceStatus.rate_limited)]
        records = [r for recs in run.records.values() for r in recs]
        papers = aggregate(records, failed)
        mctx = run.mctx
        for p in papers:
            p.match = classify(p, mctx)
        # In identifier mode only show records that match the identifier.
        if mctx.doi or (mctx.arxiv_id and not mctx.title):
            exact = [p for p in papers if p.match.label.value == "Exact"]
            if exact:
                papers = exact
        job.results = rank(papers)[:MAX_RESULTS]

    def _finish(self, run: _Running, cancelled: bool = False, failed: bool = False) -> None:
        job = run.job
        self._refresh_results(run)
        attempted = [o for o in job.sources if o.status != SourceStatus.skipped]
        ok = [o for o in attempted if o.status in (SourceStatus.success, SourceStatus.no_results)]
        bad = [o for o in attempted if o.status in (SourceStatus.failed, SourceStatus.timeout, SourceStatus.rate_limited)]
        if cancelled:
            job.status = JobStatus.cancelled
        elif failed or (attempted and not ok):
            job.status = JobStatus.failed if not job.results else JobStatus.partial
        elif bad:
            job.status = JobStatus.partial
        else:
            job.status = JobStatus.completed
        if not attempted and not cancelled:
            job.status = JobStatus.failed
            job.notes.append("No source could run this kind of query.")
        job.finished_at = utcnow()
        job.notes.append(
            "Results come only from the sources listed; a paper missing here may still exist elsewhere."
        )
        try:
            self.db.save_search(job)
        except Exception:
            log.exception("could not persist search %s", job.id)
            job.notes.append("Results could not be saved to local history (database error).")
        self._running.pop(job.id, None)
        self._recent[job.id] = job
        while len(self._recent) > 20:
            self._recent.pop(next(iter(self._recent)))

