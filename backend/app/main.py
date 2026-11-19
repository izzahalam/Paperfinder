"""FastAPI application: local API + static UI (SRS §8, FR-89..96, §11)."""

from __future__ import annotations

import logging
import logging.handlers
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from . import __version__
from .assistant import AssistantError, AssistantUnavailable, assistant_configured, interpret
from .citations import generate as generate_citation
from .config import ALL_PROVIDERS, Preferences, Settings, get_settings
from .db import Database
from .downloads import DownloadService
from .fulltext import discover_versions
from .models import (
    AssistantRequest, CitationRequest, CitationResponse, DownloadRequest, SavedPaperIn, SearchRequest,
)
from .orchestrator import SearchInputError, SearchService, TooManySearches
from .providers import REGISTRY

log = logging.getLogger("paperfinder")

# ---------------------------------------------------------------- logging (NFR-27, FR-87)

_SECRET_PATTERNS = [
    (re.compile(r"(api_key=)[^&\s\"']+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(x-api-key['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(authorization['\"]?\s*[:=]\s*['\"]?)(bearer\s+)?[^\s'\",}]+", re.I), r"\1[REDACTED]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]+"), "[REDACTED]"),
    (re.compile(r"((?:mailto|email)[=:]\s*)[^&\s\"']+", re.I), r"\1[REDACTED]"),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat, repl in _SECRET_PATTERNS:
            msg = pat.sub(repl, msg)
        record.msg, record.args = msg, ()
        return True


def setup_logging(settings: Settings) -> None:
    logs = settings.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(getattr(h, "_paperfinder", False) for h in root.handlers):
        return
    fh = logging.handlers.RotatingFileHandler(logs / "paperfinder.log", maxBytes=2_000_000, backupCount=3,
                                              encoding="utf-8")
    fh._paperfinder = True  # type: ignore[attr-defined]
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    fh.addFilter(RedactingFilter())
    root.addHandler(fh)
    root.setLevel(logging.INFO)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for h in root.handlers:
        h.addFilter(RedactingFilter())


# ---------------------------------------------------------------- app factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    setup_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = Database(settings.db_path)
        app.state.search = SearchService(app.state.db, settings)
        app.state.downloads = DownloadService(app.state.db, settings)
        # Clean temp files left by an interrupted previous run.
        for p in settings.temp_dir.glob("*.part"):
            p.unlink(missing_ok=True)
        log.info("PaperFinder AI %s started", __version__)
        yield
        await app.state.search.aclose()
        await app.state.downloads.aclose()
        app.state.db.close()
        log.info("PaperFinder AI stopped")

    app = FastAPI(title="PaperFinder AI", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings = settings

    allowed_hosts = {"127.0.0.1", "localhost", "[::1]", "testserver"}
    allowed_origins = {f"http://{h}:{p}" for h in ("127.0.0.1", "localhost") for p in (settings.port, 5173)}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # DNS-rebinding defence: only answer to loopback host names (R-20).
        host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") \
            else (request.headers.get("host") or "").split("]")[0] + "]"
        if not settings.allow_lan and host not in allowed_hosts:
            return JSONResponse({"detail": "Host not allowed."}, status_code=421)
        # CSRF defence for state-changing requests from other web pages.
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and origin not in allowed_origins and not settings.allow_lan:
                return JSONResponse({"detail": "Cross-origin request blocked."}, status_code=403)
            ctype = request.headers.get("content-type", "")
            if request.headers.get("content-length", "0") not in ("0", "") and "application/json" not in ctype:
                return JSONResponse({"detail": "Requests must use application/json."}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        if origin := request.headers.get("origin"):
            if origin in allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Vary"] = "Origin"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        msgs = []
        for e in exc.errors():
            m = str(e.get("msg", "Invalid input")).removeprefix("Value error, ")
            loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
            msgs.append(m if not loc or m.endswith(".") else f"{loc}: {m}")
        return JSONResponse({"detail": "; ".join(msgs) or "Invalid input."}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Unexpected server error. Details were written to the local log."},
                            status_code=500)

    def db(request: Request) -> Database:
        return request.app.state.db

    def paper_or_404(request: Request, paper_id: str):
        p = db(request).get_paper(paper_id)
        if p is None:
            raise HTTPException(404, "Paper not found. It may have been removed from local history.")
        return p

    # ------------------------------------------------------------ health/config
    @app.get("/api/health")
    async def health(request: Request):
        return {"status": "ok" if db(request).healthy() else "degraded", "version": __version__,
                "database": db(request).healthy(), "assistant_configured": assistant_configured()}

    @app.get("/api/providers")
    async def providers(request: Request):
        prefs = db(request).get_preferences()
        client = request.app.state.search.client(prefs)
        return [{
            "name": p.name, "label": p.label, "description": p.description, "homepage": p.homepage,
            "enabled": p.name in prefs.enabled_providers,
            "unavailable_reason": p.unavailable_reason(prefs, settings),
            "paused": client.breaker_open(p.name),
            "supports_search": p.supports_search,
        } for p in (REGISTRY[n] for n in ALL_PROVIDERS)]

    @app.get("/api/settings")
    async def get_prefs(request: Request):
        return {"preferences": db(request).get_preferences(),
                "credentials": {"openalex_api_key": bool(settings.openalex_api_key),
                                "s2_api_key": bool(settings.s2_api_key),
                                "anthropic_api_key": assistant_configured()},
                "data_dir": str(settings.data_dir)}

    @app.put("/api/settings")
    async def put_prefs(request: Request, prefs: Preferences):
        db(request).put_preferences(prefs)
        return {"preferences": prefs}

    @app.get("/api/storage")
    async def storage(request: Request):
        return request.app.state.downloads.storage_info()

    # ------------------------------------------------------------ search
    @app.post("/api/search", status_code=202)
    async def search(request: Request, req: SearchRequest):
        try:
            return await request.app.state.search.start(req)
        except SearchInputError as e:
            raise HTTPException(400, str(e)) from None
        except TooManySearches as e:
            raise HTTPException(429, str(e)) from None

    @app.get("/api/search/{search_id}")
    async def get_search(request: Request, search_id: str):
        job = request.app.state.search.get(search_id)
        if job is None:
            raise HTTPException(404, "Search not found.")
        saved = db(request).saved_ids()
        data = job.model_dump(mode="json")
        for p in data["results"]:
            p["saved"] = p["id"] in saved
        return data

    @app.post("/api/search/{search_id}/cancel")
    async def cancel_search(request: Request, search_id: str):
        job = await request.app.state.search.cancel(search_id)
        if job is None:
            raise HTTPException(404, "Search not found.")
        return job

    # ------------------------------------------------------------ papers / versions
    @app.get("/api/papers/{paper_id}")
    async def get_paper(request: Request, paper_id: str):
        p = paper_or_404(request, paper_id)
        data = p.model_dump(mode="json")
        data["saved"] = paper_id in db(request).saved_ids()
        return data

    @app.get("/api/papers/{paper_id}/versions")
    async def get_versions(request: Request, paper_id: str):
        p = paper_or_404(request, paper_id)
        return {"paper_id": p.id, "access": p.access, "candidates": p.candidates, "sources": []}

    @app.post("/api/papers/{paper_id}/versions")
    async def discover(request: Request, paper_id: str):
        p = paper_or_404(request, paper_id)
        paper, outcomes = await discover_versions(p, request.app.state.search, db(request))
        return {"paper_id": paper.id, "access": paper.access, "candidates": paper.candidates, "sources": outcomes}

    # ------------------------------------------------------------ downloads
    @app.post("/api/downloads", status_code=202)
    async def start_download(request: Request, body: DownloadRequest):
        p = paper_or_404(request, body.paper_id)
        try:
            return request.app.state.downloads.start(p, body.candidate_id)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None

    @app.get("/api/downloads")
    async def list_downloads(request: Request):
        svc: DownloadService = request.app.state.downloads
        out = []
        for d in svc.list():
            item = d.model_dump(mode="json")
            item["file_available"] = svc.file_path(d) is not None
            p = db(request).get_paper(d.paper_id)
            item["paper_title"] = p.title if p else None
            out.append(item)
        return out

    @app.get("/api/downloads/{download_id}")
    async def get_download(request: Request, download_id: str):
        svc: DownloadService = request.app.state.downloads
        d = svc.get(download_id)
        if d is None:
            raise HTTPException(404, "Download not found.")
        item = d.model_dump(mode="json")
        item["file_available"] = svc.file_path(d) is not None
        return item

    @app.post("/api/downloads/{download_id}/cancel")
    async def cancel_download(request: Request, download_id: str):
        d = await request.app.state.downloads.cancel(download_id)
        if d is None:
            raise HTTPException(404, "Download not found.")
        return d

    @app.get("/api/downloads/{download_id}/file")
    async def download_file(request: Request, download_id: str):
        svc: DownloadService = request.app.state.downloads
        d = svc.get(download_id)
        if d is None:
            raise HTTPException(404, "Download not found.")
        path = svc.file_path(d)
        if path is None:
            raise HTTPException(410, "The file is no longer available on disk.")
        return FileResponse(path, media_type="application/pdf", filename=d.filename,
                            headers={"Cache-Control": "no-store"})

    @app.delete("/api/downloads/{download_id}/file")
    async def delete_download_file(request: Request, download_id: str):
        try:
            return request.app.state.downloads.delete_file(download_id)
        except LookupError as e:
            raise HTTPException(404, str(e)) from None
        except OSError as e:
            raise HTTPException(409, f"The file could not be deleted ({e.strerror}).") from None

    @app.delete("/api/downloads/{download_id}")
    async def delete_download(request: Request, download_id: str, delete_file: bool = False):
        svc: DownloadService = request.app.state.downloads
        if svc.get(download_id) is None:
            raise HTTPException(404, "Download not found.")
        file_deleted = False
        if delete_file:
            try:
                file_deleted = svc.delete_file(download_id).file_deleted
            except OSError as e:
                raise HTTPException(409, f"The file could not be deleted ({e.strerror}).") from None
        db(request).delete_download_record(download_id)
        return {"record_deleted": True, "file_deleted": file_deleted}

    # ------------------------------------------------------------ citations
    @app.post("/api/citations", response_model=CitationResponse)
    async def citation(request: Request, body: CitationRequest):
        if not body.paper_id:
            raise HTTPException(400, "paper_id is required.")
        p = paper_or_404(request, body.paper_id)
        return generate_citation(p, body.style, body.overrides)

    # ------------------------------------------------------------ history / saved
    @app.get("/api/history")
    async def history(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        return db(request).list_searches(limit, offset)

    @app.delete("/api/history/{search_id}")
    async def delete_history(request: Request, search_id: str):
        if not db(request).delete_search(search_id):
            raise HTTPException(404, "History entry not found.")
        request.app.state.search._recent.pop(search_id, None)
        return {"deleted": 1}

    @app.delete("/api/history")
    async def delete_all_history(request: Request):
        n = db(request).delete_all_searches()
        request.app.state.search._recent.clear()
        return {"deleted": n}

    @app.get("/api/saved")
    async def saved(request: Request):
        return db(request).list_saved()

    @app.post("/api/saved")
    async def save(request: Request, body: SavedPaperIn):
        paper_or_404(request, body.paper_id)
        db(request).save_paper(body.paper_id, body.note)
        return {"saved": True}

    @app.delete("/api/saved/{paper_id}")
    async def unsave(request: Request, paper_id: str):
        if not db(request).unsave_paper(paper_id):
            raise HTTPException(404, "Paper is not saved.")
        return {"saved": False}

    # ------------------------------------------------------------ AI assistant
    @app.post("/api/assistant/interpret")
    async def assistant(request: Request, body: AssistantRequest):
        prefs = db(request).get_preferences()
        if not prefs.assistant_enabled:
            raise HTTPException(403, "The AI assistant is turned off in Settings.")
        previous = request.app.state.search.get(body.previous_search_id) if body.previous_search_id else None
        try:
            return await interpret(body.text, previous, settings.assistant_model)
        except AssistantUnavailable as e:
            raise HTTPException(503, str(e)) from None
        except AssistantError as e:
            raise HTTPException(502, str(e)) from None
        except ValidationError:
            raise HTTPException(502, "The AI assistant returned an invalid suggestion.") from None

    # ------------------------------------------------------------ static UI
    dist: Path = settings.frontend_dist
    if dist.is_dir():
        index = dist / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404, "Not found.")
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        async def no_ui():
            return JSONResponse({"detail": "UI not built. Run the frontend dev server (npm run dev) or build it "
                                           "(npm run build). API docs: /api/docs"})

    return app
