#!/usr/bin/env bash
# NEXUS-DC one-command launcher.
#
#   bash demo/run_demo.sh
#
# On a fresh clone this bootstraps everything: Python venv + dependencies,
# .env scaffold (you add two free API keys, then re-run), parsed document
# cache, frontend dependencies - then starts the FastAPI backend (:8000)
# and the React frontend (:5173).
set -euo pipefail
cd "$(dirname "$0")/.."

# ---------------------------------------------------------------- toolchain
command -v python3 >/dev/null || { echo "ERROR: python3 not found (need Python 3.11+)." >&2; exit 1; }
command -v npm >/dev/null     || { echo "ERROR: npm not found (need Node 18+)." >&2; exit 1; }

# -------------------------------------------------------------- python venv
PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "[bootstrap] creating .venv and installing Python dependencies (one-time, a few minutes)"
  python3 -m venv .venv
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r requirements.txt
fi

# -------------------------------------------------------------------- .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "  Created .env from .env.example - add your two free API keys, then re-run:"
  echo ""
  echo "    GEMINI_API_KEY   https://aistudio.google.com/apikey"
  echo "    GROQ_API_KEYS    https://console.groq.com/keys"
  echo ""
  echo "  Then:  bash demo/run_demo.sh"
  exit 1
fi
if grep -q "your-ai-studio-key" .env; then
  echo "ERROR: .env still has placeholder keys - set GEMINI_API_KEY and GROQ_API_KEYS." >&2
  exit 1
fi

# preflight: the cache is a parsed fallback, never authored - build it if empty
if [ ! -f data/cache/schedule.json ]; then
  echo "[preflight] parsing data/sources/ -> data/cache/ (one-time, ~2 minutes, uses Groq)"
  "$PY" scripts/build_cache.py
fi

if [ ! -d frontend/node_modules ]; then
  echo "[preflight] installing frontend dependencies"
  (cd frontend && npm install)
fi

trap 'kill 0' EXIT

echo "[1/2] FastAPI backend  -> http://localhost:8000  (docs: /docs)"
.venv/bin/uvicorn api.main:app --port 8000 &
sleep 3

echo "[2/2] React frontend   -> http://localhost:5173"
echo "      Risk Dashboard   -> http://localhost:5173/"
echo "      Commissioning    -> http://localhost:5173/commissioning"
echo "      ORACLE Chat      -> http://localhost:5173/oracle"
echo "      Documents        -> http://localhost:5173/documents"
echo "      NCR / Quality    -> http://localhost:5173/ncr"
(cd frontend && npm run dev) &

wait
