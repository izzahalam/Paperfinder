"""Local SQLite persistence (SRS §9, FR-73..81, NFR-07/17/19/33).

Records are stored as validated JSON documents with a few indexed columns.
All writes run inside transactions. Secrets are never stored here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Preferences
from .models import DownloadRecord, Paper, SearchJob, utcnow

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preferences (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY, doi TEXT, title TEXT, data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE TABLE IF NOT EXISTS searches (
    id TEXT PRIMARY KEY, mode TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT, result_count INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_searches_started ON searches(started_at DESC);
CREATE TABLE IF NOT EXISTS saved (
    paper_id TEXT PRIMARY KEY, note TEXT, saved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS downloads (
    id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
    data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_downloads_paper ON downloads(paper_id);
"""

SCHEMA_VERSION = "1"


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def healthy(self) -> bool:
        try:
            self._q("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------ preferences
    def get_preferences(self) -> Preferences:
        rows = self._q("SELECT data FROM preferences WHERE id = 1")
        if not rows:
            return Preferences()
        try:
            return Preferences.model_validate_json(rows[0]["data"])
        except ValueError:
            return Preferences()

    def put_preferences(self, prefs: Preferences) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO preferences(id, data) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                      (prefs.model_dump_json(),))

    # ------------------------------------------------------------ papers
    def upsert_papers(self, papers: list[Paper], conn: sqlite3.Connection | None = None) -> None:
        def run(c: sqlite3.Connection) -> None:
            c.executemany(
                "INSERT INTO papers(id, doi, title, data, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET doi=excluded.doi, title=excluded.title, data=excluded.data, "
                "updated_at=excluded.updated_at",
                [(p.id, p.doi, p.title, p.model_dump_json(), utcnow()) for p in papers],
            )
        if conn is not None:
            run(conn)
        else:
            with self.tx() as c:
                run(c)

    def get_paper(self, paper_id: str) -> Paper | None:
        rows = self._q("SELECT data FROM papers WHERE id = ?", (paper_id,))
        return Paper.model_validate_json(rows[0]["data"]) if rows else None

    def get_papers(self, ids: list[str]) -> list[Paper]:
        if not ids:
            return []
        rows = self._q(f"SELECT id, data FROM papers WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
        by_id = {r["id"]: Paper.model_validate_json(r["data"]) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    # ------------------------------------------------------------ searches
    def save_search(self, job: SearchJob) -> None:
        req = job.request
        summary = req.query or " · ".join(
            x for x in [req.title, ", ".join(req.authors) if req.authors else None, req.keywords, req.venue,
                        f"{req.year_from or ''}–{req.year_to or ''}" if (req.year_from or req.year_to) else None] if x
        )
        data = job.model_dump(mode="json", exclude={"results"})
        data["result_ids"] = [p.id for p in job.results]
        with self.tx() as c:
            self.upsert_papers(job.results, conn=c)
            c.execute(
                "INSERT INTO searches(id, mode, summary, status, started_at, finished_at, result_count, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "finished_at=excluded.finished_at, result_count=excluded.result_count, data=excluded.data",
                (job.id, job.mode.value, summary[:500], job.status.value, job.started_at, job.finished_at,
                 len(job.results), json.dumps(data)),
            )

    def get_search(self, search_id: str) -> SearchJob | None:
        rows = self._q("SELECT data FROM searches WHERE id = ?", (search_id,))
        if not rows:
            return None
        data = json.loads(rows[0]["data"])
        ids = data.pop("result_ids", [])
        job = SearchJob.model_validate(data | {"results": []})
        job.results = self.get_papers(ids)
        job.cached = True
        return job

    def list_searches(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self._q(
            "SELECT id, mode, summary, status, started_at, finished_at, result_count FROM searches "
            "ORDER BY started_at DESC LIMIT ? OFFSET ?", (limit, offset))
        return [dict(r) for r in rows]

    def delete_search(self, search_id: str) -> bool:
        with self.tx() as c:
            n = c.execute("DELETE FROM searches WHERE id = ?", (search_id,)).rowcount
            self._purge_orphans(c)
        return n > 0

    def delete_all_searches(self) -> int:
        with self.tx() as c:
            n = c.execute("DELETE FROM searches").rowcount
            self._purge_orphans(c)
        return n

    def _purge_orphans(self, c: sqlite3.Connection) -> None:
        """Remove cached paper records no longer referenced by history, saved
        papers or downloads (keeps privacy deletion meaningful)."""
        referenced: set[str] = set()
        for r in c.execute("SELECT data FROM searches").fetchall():
            referenced.update(json.loads(r["data"]).get("result_ids", []))
        referenced.update(r["paper_id"] for r in c.execute("SELECT paper_id FROM saved").fetchall())
        referenced.update(r["paper_id"] for r in c.execute("SELECT paper_id FROM downloads").fetchall())
        all_ids = [r["id"] for r in c.execute("SELECT id FROM papers").fetchall()]
        orphans = [i for i in all_ids if i not in referenced]
        c.executemany("DELETE FROM papers WHERE id = ?", [(i,) for i in orphans])

    # ------------------------------------------------------------ saved papers
    def save_paper(self, paper_id: str, note: str | None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO saved(paper_id, note, saved_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(paper_id) DO UPDATE SET note=excluded.note", (paper_id, note, utcnow()))

    def unsave_paper(self, paper_id: str) -> bool:
        with self.tx() as c:
            n = c.execute("DELETE FROM saved WHERE paper_id = ?", (paper_id,)).rowcount
            self._purge_orphans(c)
        return n > 0

    def list_saved(self) -> list[dict]:
        rows = self._q("SELECT s.paper_id, s.note, s.saved_at, p.data FROM saved s "
                       "JOIN papers p ON p.id = s.paper_id ORDER BY s.saved_at DESC")
        return [{"paper": json.loads(r["data"]), "note": r["note"], "saved_at": r["saved_at"]} for r in rows]

    def saved_ids(self) -> set[str]:
        return {r["paper_id"] for r in self._q("SELECT paper_id FROM saved")}

    # ------------------------------------------------------------ downloads
    def put_download(self, d: DownloadRecord) -> None:
        d.updated_at = utcnow()
        with self.tx() as c:
            c.execute("INSERT INTO downloads(id, paper_id, status, created_at, data) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
                      (d.id, d.paper_id, d.status, d.created_at, d.model_dump_json()))

    def get_download(self, download_id: str) -> DownloadRecord | None:
        rows = self._q("SELECT data FROM downloads WHERE id = ?", (download_id,))
        return DownloadRecord.model_validate_json(rows[0]["data"]) if rows else None

    def list_downloads(self) -> list[DownloadRecord]:
        rows = self._q("SELECT data FROM downloads ORDER BY created_at DESC")
        return [DownloadRecord.model_validate_json(r["data"]) for r in rows]

    def delete_download_record(self, download_id: str) -> bool:
        with self.tx() as c:
            n = c.execute("DELETE FROM downloads WHERE id = ?", (download_id,)).rowcount
            self._purge_orphans(c)
        return n > 0

    def db_size(self) -> int:
        return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())

    def counts(self) -> dict:
        return {
            "searches": self._q("SELECT COUNT(*) n FROM searches")[0]["n"],
            "papers": self._q("SELECT COUNT(*) n FROM papers")[0]["n"],
            "saved": self._q("SELECT COUNT(*) n FROM saved")[0]["n"],
            "downloads": self._q("SELECT COUNT(*) n FROM downloads")[0]["n"],
        }
