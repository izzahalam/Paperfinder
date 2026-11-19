#!/usr/bin/env bash
# PaperFinder AI - local start for macOS/Linux (development convenience).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x backend/.venv/bin/python ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && npm ci && npm run build)
fi
[ -f .env ] || cp .env.example .env
cd backend && exec .venv/bin/python -m app "$@"
