# PaperFinder AI

PaperFinder AI is an AI-assisted academic paper discovery tool. It searches by DOI, URL, title, or keywords, retrieves metadata and abstracts, finds accessible full-text versions, validates PDFs for download, and generates citations. It prioritises accuracy, privacy, transparency, and secure retrieval.

It is a **local-first** app: a FastAPI backend and React UI that run on your own computer, with history and files stored locally in SQLite and a `data/` folder.

> **Core principle (SRS):** paper identity, metadata, abstracts, access status and files must be evidence-based. Uncertainty and partial failure are shown, never hidden.

## Features

| Area | What it does |
|---|---|
| **Search** | DOI / arXiv ID / paper URL, title, and advanced search (title, authors, keywords, year range, venue) |
| **Sources** | Crossref, OpenAlex, Semantic Scholar, arXiv, Unpaywall. They are queried in parallel inside a time budget, and you can switch each one on or off |
| **Coverage report** | Every source shows *found / no results / failed / timed out / rate-limited / skipped*. One failing source never discards the others' results |
| **Identity matching** | Each result is labelled **Exact / Probable / Similar / Unverified** with the evidence behind the label. A title match alone is never labelled *Exact* |
| **Deduplication & provenance** | Records are merged by DOI first. You can see which source supplied each field, and conflicts between sources are flagged rather than silently overwritten |
| **Abstracts** | Shown with their source. "Unavailable" and "retrieval failed" are reported as different states. Abstracts are never generated |
| **Full text** | Finds only lawful open copies: version of record, accepted manuscript, or preprint, each with evidence. Paywalls are never bypassed |
| **Safe downloads** | SSRF-protected fetching, size limits, PDF validation in an isolated process (signature, structure, title/DOI/author match), atomic save with a sanitised filename |
| **Citations** | APA 7, IEEE and BibTeX, built from structured metadata, with warnings for missing fields. You can correct fields, copy the citation, or export a `.bib` file |
| **Local data** | Search history (restorable), saved papers, and download tracking. Deleting a record and deleting a file are separate actions |
| **AI assistant (optional)** | "Ask AI" turns a plain-language request in any language (including Urdu or Roman Urdu) into search fields **for you to review**. It never supplies paper metadata |

## Quick start (Windows 10/11)

1. Install **Python 3.11+** (tick *Add to PATH*) and **Node.js 20+** (only needed the first time, to build the UI).
2. Double-click **`start.bat`**. The first run sets everything up, then opens `http://127.0.0.1:8000/` in your browser.
3. To stop the app, close the window or press **Ctrl+C**.

On macOS/Linux, run `./start.sh`.

### Optional configuration (`.env`)

Copy `.env.example` to `.env` (the start script does this for you) and fill in only what you need:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Turns on the "Ask AI" assistant |
| `PAPERFINDER_OPENALEX_API_KEY` | Free OpenAlex key. **Recommended**, because the shared no-key budget runs out quickly |
| `PAPERFINDER_S2_API_KEY` | Higher Semantic Scholar rate limits |
| `PAPERFINDER_PORT`, `PAPERFINDER_DATA_DIR` | Server port and data folder |

Secrets stay in `.env`. They are never sent to the browser, stored in the database, or written to logs, and logs are redacted.

In **Settings** you can also add a contact email. It is used by Crossref and OpenAlex's "polite" pools and is required by Unpaywall. If you leave it empty, no personal information is sent.

## Development

```bash
# backend (terminal 1)
cd backend
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m app --no-browser                       # API on http://127.0.0.1:8000 (docs: /api/docs)

# frontend (terminal 2)
cd frontend
npm ci
npm run dev                                      # UI on http://127.0.0.1:5173 (proxies /api)
```

### Tests

```bash
cd backend && python -m pytest        # 112 tests, fully offline (saved real API responses + mocks)
cd frontend && npm test               # UI unit tests
```

Tests map to the SRS test IDs (T-01 to T-35). Examples: DOI search, partial source failure, rate limits, deduplication, conflicts, HTML-as-PDF, wrong-paper PDF, corrupt PDF, oversized file, unsafe redirect, private IP, path traversal, secret redaction, restart persistence, cancellation, and loopback-only binding.

## Architecture

```
frontend/  React + TypeScript (Vite)       search modes, result cards, versions, citations, history
backend/app/
  main.py            FastAPI routes, loopback/CSRF guards, security headers, static UI
  orchestrator.py    bounded concurrent search, per-source outcomes, cancel, partial results
  providers/         one adapter per source (schema, auth, limits, error mapping)
  aggregate.py       DOI-first dedupe/merge, provenance, conflicts, access classification
  matching.py        identity rules (Exact/Probable/Similar/Unverified) and ranking
  fulltext.py        lawful open-version discovery
  net.py             provider client (retries, backoff, rate limits, circuit breaker) + SSRF-safe fetcher
  pdfcheck.py        isolated PDF validation
  downloads.py       temp file -> validate -> atomic finalise; separate record/file deletion
  citations.py       APA 7 / IEEE / BibTeX
  assistant.py       optional, bounded Claude query interpreter
  db.py              SQLite (transactions, history, saved, downloads, preferences)
```

API endpoints follow SRS §8.4: `POST /api/search`, `GET /api/search/{id}`, `POST /api/search/{id}/cancel`, `GET /api/papers/{id}`, `POST /api/papers/{id}/versions`, `POST /api/downloads`, `GET /api/downloads/{id}`, `POST /api/citations`, `GET/DELETE /api/history`, `GET /api/health`, and others. Interactive docs are at `/api/docs`.

## Security & legal boundaries

- The server binds to `127.0.0.1` only and rejects non-loopback `Host` headers (DNS-rebinding defence). Cross-origin writes are blocked.
- Arbitrary URLs are fetched with http/https only and standard ports only. Private, loopback and link-local addresses are blocked, every redirect hop is re-checked, and when no proxy is configured the connection is pinned to the checked IP.
- Only copies that a source reports as open access are downloadable. There is no paywall, DRM or login bypass, and no institutional credentials are stored.
- The app never claims exhaustive coverage. "Not found" always means "not found in the sources searched".
