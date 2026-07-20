#!/usr/bin/env bash
# NEXUS-DC dev/testing launcher - like demo/run_demo.sh but it DELIBERATELY
# does NOT pre-build the parsed cache.
#
#   bash run_dev.sh
#
# Use this to see the cold-start path: with data/cache/ empty, the app still
# starts, and the first "Run All Agents" (dashboard button) rebuilds the cache
# from data/sources/ automatically - parse every document, re-index the vector
# store, repopulate the knowledge graph - then runs the agents. Delete
# data/cache/ any time and the next "Run All Agents" rebuilds it again, so you
# can watch how the system behaves from scratch. After that first run the cache
# exists and subsequent runs are fast, exactly like a normal install.
#
# (demo/run_demo.sh is the audience-facing launcher - it pre-builds the cache
#  so the first click is quick. This one is for understanding the internals.)
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "ERROR: .venv not found - run 'bash demo/run_demo.sh' once to bootstrap." >&2
  exit 1
fi
if [ ! -f .env ] || grep -q "your-ai-studio-key" .env; then
  echo "ERROR: .env missing or still has placeholder keys - set GEMINI_API_KEY and GROQ_API_KEYS." >&2
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  echo "[preflight] installing frontend dependencies"
  (cd frontend && npm install)
fi

if [ -f data/cache/schedule.json ]; then
  echo "[note] data/cache/ exists - delete it to test the cold-start rebuild:"
  echo "       rm -rf data/cache  (then click 'Run All Agents')"
else
  echo "[note] data/cache/ is empty - the FIRST 'Run All Agents' will rebuild it"
  echo "       from data/sources/ (~2 min, uses Groq). This is expected."
fi

trap 'kill 0' EXIT

echo "[1/2] FastAPI backend  -> http://localhost:8000  (docs: /docs)"
.venv/bin/uvicorn api.main:app --port 8000 &
sleep 3

echo "[2/2] React frontend   -> http://localhost:5173"
(cd frontend && npm run dev) &

wait
