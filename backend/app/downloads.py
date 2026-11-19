"""Download manager (SRS FR-40..46, FR-79/80/92, NFR-08/15/16, R-03..07, R-18, R-21, R-24).

* Only candidates with open-access evidence are downloadable (no bypass).
* Bytes stream into a temporary file under a fixed root; size and disk checks.
* The file is validated in an isolated worker process *before* it is moved
  (atomically) into the downloads folder with a sanitised, unique filename.
* Rejected or failed files are deleted. Records and files are deleted separately.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import os
import re
import shutil
import unicodedata
import uuid
from pathlib import Path

from .config import Settings
from .db import Database
from .models import DownloadRecord, Paper, VerificationState
from .net import FetchError, SafeFetcher
from .orchestrator import parse_landing_page
from .pdfcheck import validate_file, worker_init

log = logging.getLogger("paperfinder.downloads")

MAX_CONCURRENT_DOWNLOADS = 2
VALIDATION_TIMEOUT = 90.0

_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(paper: Paper, suffix: str = ".pdf", max_len: int = 120) -> str:
    """Build a filesystem-safe filename from metadata (no path components)."""
    first = (paper.authors[0].family or paper.authors[0].name) if paper.authors else "Unknown"
    title_words = (paper.title or "untitled").split()[:8]
    stem = f"{first}_{paper.year or 'n.d.'}_{' '.join(title_words)}"
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "", stem)
    stem = re.sub(r"[\s]+", "_", stem).strip("._- ")
    stem = re.sub(r"\.{2,}", ".", stem)
    if not stem or stem.lower().split(".")[0] in _WIN_RESERVED:
        stem = f"paper_{stem or 'download'}"
    return stem[: max_len - len(suffix)] + suffix


def _unique_path(directory: Path, filename: str) -> Path:
    base, ext = os.path.splitext(filename)
    candidate = directory / filename
    n = 2
    while candidate.exists():
        candidate = directory / f"{base}-{n}{ext}"
        n += 1
    return candidate


def inside(root: Path, p: Path) -> bool:
    try:
        return p.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


class DownloadService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self._tasks: dict[str, asyncio.Task] = {}
        self._live: dict[str, DownloadRecord] = {}
        self._pool: concurrent.futures.ProcessPoolExecutor | None = None
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self.fetcher_factory = lambda prefs: SafeFetcher(prefs.contact_email, timeout=prefs.download_timeout_seconds)
        self.use_process_pool = True

    # ---------------------------------------------------------------- lifecycle
    def _get_pool(self) -> concurrent.futures.ProcessPoolExecutor:
        if self._pool is None:
            import multiprocessing as mp

            self._pool = concurrent.futures.ProcessPoolExecutor(
                max_workers=1, mp_context=mp.get_context("spawn"), initializer=worker_init)
        return self._pool

    def _kill_pool(self) -> None:
        pool, self._pool = self._pool, None
        if pool is None:
            return
        for proc in list(getattr(pool, "_processes", {}).values()):
            try:
                proc.kill()
            except Exception:
                pass
        pool.shutdown(wait=False, cancel_futures=True)

    async def aclose(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        self._kill_pool()

    # ---------------------------------------------------------------- API
    def get(self, download_id: str) -> DownloadRecord | None:
        return self._live.get(download_id) or self.db.get_download(download_id)

    def list(self) -> list[DownloadRecord]:
        stored = {d.id: d for d in self.db.list_downloads()}
        stored.update(self._live)
        return sorted(stored.values(), key=lambda d: d.created_at, reverse=True)

    def start(self, paper: Paper, candidate_id: str) -> DownloadRecord:
        cand = next((c for c in paper.candidates if c.id == candidate_id), None)
        if cand is None:
            raise LookupError("That version candidate was not found for this paper. Run full-text discovery again.")
        rec = DownloadRecord(id="d_" + uuid.uuid4().hex[:16], paper_id=paper.id, candidate_id=cand.id,
                             source_url=cand.url, status="queued")
        if not cand.is_oa:
            rec.status = "blocked"
            rec.error = ("No evidence that this copy is openly available, so it will not be downloaded. "
                         "Use your library's legitimate access instead.")
            self.db.put_download(rec)
            return rec
        self._live[rec.id] = rec
        self.db.put_download(rec)
        self._tasks[rec.id] = asyncio.create_task(self._run(rec, paper))
        return rec

    async def cancel(self, download_id: str) -> DownloadRecord | None:
        t = self._tasks.get(download_id)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        return self.get(download_id)

    async def wait(self, download_id: str) -> DownloadRecord | None:
        t = self._tasks.get(download_id)
        if t:
            try:
                await t
            except asyncio.CancelledError:
                pass
        return self.get(download_id)

    def file_path(self, rec: DownloadRecord) -> Path | None:
        if not rec.filename or rec.file_deleted:
            return None
        p = self.settings.downloads_dir / rec.filename
        if not inside(self.settings.downloads_dir, p) or not p.is_file():
            return None
        return p

    def delete_file(self, download_id: str) -> DownloadRecord:
        rec = self.get(download_id)
        if rec is None:
            raise LookupError("Download not found.")
        p = self.file_path(rec)
        if p is not None:
            # Another record may point at the same deduplicated file.
            others = [d for d in self.db.list_downloads() if d.id != rec.id and d.filename == rec.filename
                      and not d.file_deleted]
            if not others:
                p.unlink()
        rec.file_deleted = True
        self.db.put_download(rec)
        return rec

    def storage_info(self) -> dict:
        root = self.settings.downloads_dir
        files = [p for p in root.glob("*") if p.is_file()]
        usage = shutil.disk_usage(root)
        return {
            "downloads_dir": str(root),
            "download_files": len(files),
            "download_bytes": sum(p.stat().st_size for p in files),
            "database_bytes": self.db.db_size(),
            "disk_free_bytes": usage.free,
            **self.db.counts(),
        }

    # ---------------------------------------------------------------- worker
    async def _validate(self, path: Path, paper: Paper) -> dict:
        expected = {
            "title": paper.title, "doi": paper.doi, "arxiv_id": paper.arxiv_id, "year": paper.year,
            "author_families": [a.family or a.name.split(" ")[-1] for a in paper.authors[:5]],
        }
        if not self.use_process_pool:
            return await asyncio.to_thread(validate_file, str(path), expected)
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(self._get_pool(), validate_file, str(path), expected), VALIDATION_TIMEOUT)
        except asyncio.TimeoutError:
            self._kill_pool()
            return {"state": "Failed", "details": ["PDF validation timed out (file may be malformed)."]}
        except concurrent.futures.process.BrokenProcessPool:
            self._kill_pool()
            return {"state": "Failed", "details": ["PDF validator crashed on this file (malformed PDF)."]}

    def _save(self, rec: DownloadRecord) -> None:
        try:
            self.db.put_download(rec)
        except Exception:
            log.exception("could not persist download %s", rec.id)

    async def _resolve_landing(self, fetcher: SafeFetcher, url: str) -> str | None:
        _res, html = await fetcher.fetch_text(url)
        return parse_landing_page(html).get("pdf_url")

    async def _run(self, rec: DownloadRecord, paper: Paper) -> None:
        prefs = self.db.get_preferences()
        max_bytes = prefs.max_pdf_mb * 1024 * 1024
        tmp: Path | None = None
        fh = None
        try:
            async with self._sem:
                self.settings.ensure_dirs()
                if shutil.disk_usage(self.settings.downloads_dir).free < max_bytes * 2:
                    raise FetchError("Not enough free disk space for a safe download.", "disk")
                cand = next(c for c in paper.candidates if c.id == rec.candidate_id)
                fetcher = self.fetcher_factory(prefs)
                url = cand.url
                if not cand.is_pdf_url:
                    pdf = await self._resolve_landing(fetcher, url)
                    if not pdf:
                        rec.status = "blocked"
                        rec.error = ("This source only gives a landing page and no direct PDF link was found on it. "
                                     "Open the landing page in your browser instead.")
                        return
                    from urllib.parse import urljoin

                    url = urljoin(url, pdf)
                rec.status = "downloading"
                tmp = self.settings.temp_dir / f"{rec.id}.part"
                sha = hashlib.sha256()
                async for res, chunk in fetcher.stream(url, accept="application/pdf,*/*;q=0.5", max_bytes=max_bytes):
                    if res is not None:
                        rec.final_url = res.final_url
                        clen = res.headers.get("content-length")
                        rec.size_bytes = int(clen) if clen and clen.isdigit() else None
                        fh = open(tmp, "wb")
                        continue
                    assert fh is not None
                    fh.write(chunk)
                    sha.update(chunk)
                    rec.bytes_received += len(chunk)
                if fh is None:
                    raise FetchError("The server sent no data.", "http")
                fh.close()
                rec.size_bytes = rec.bytes_received
                rec.sha256 = sha.hexdigest()
                rec.status = "validating"
                self._save(rec)
                result = await self._validate(tmp, paper)
                state = VerificationState(result["state"])
                rec.verification = state
                rec.verification_detail = list(result.get("details", []))
                if state in (VerificationState.verified, VerificationState.limited):
                    dup = next((d for d in self.db.list_downloads()
                                if d.sha256 == rec.sha256 and d.id != rec.id and self.file_path(d)), None)
                    if dup:
                        tmp.unlink(missing_ok=True)
                        rec.filename = dup.filename
                        rec.verification_detail.append("Identical file already downloaded – reusing it.")
                    else:
                        final = _unique_path(self.settings.downloads_dir, safe_filename(paper))
                        if not inside(self.settings.downloads_dir, final):
                            raise FetchError("Refusing to write outside the downloads folder.", "path")
                        os.replace(tmp, final)  # atomic finalisation after checks
                        rec.filename = final.name
                    rec.status = "ready"
                else:
                    tmp.unlink(missing_ok=True)
                    rec.status = "blocked" if state == VerificationState.rejected else "failed"
                    rec.error = rec.verification_detail[-1] if rec.verification_detail else "Validation failed."
                self._update_candidate(paper, rec)
        except asyncio.CancelledError:
            rec.status = "cancelled"
            raise
        except FetchError as e:
            rec.status = "blocked" if e.category == "unsafe_url" else "failed"
            rec.error = str(e)
        except StopIteration:
            rec.status, rec.error = "failed", "Version candidate disappeared."
        except Exception:
            log.exception("download %s failed", rec.id)
            rec.status, rec.error = "failed", "Unexpected error during download."
        finally:
            if fh is not None and not fh.closed:
                fh.close()
            if tmp is not None and rec.status != "ready":
                tmp.unlink(missing_ok=True)
            self._save(rec)
            self._live.pop(rec.id, None)
            self._tasks.pop(rec.id, None)

    def _update_candidate(self, paper: Paper, rec: DownloadRecord) -> None:
        fresh = self.db.get_paper(paper.id) or paper
        for c in fresh.candidates:
            if c.id == rec.candidate_id:
                c.verification = rec.verification
                c.verification_detail = rec.verification_detail
        self.db.upsert_papers([fresh])
