#!/usr/bin/env bash
# Local dev server: SQLite + images in .data/, dev sign-in (no authentik), on :8097.
# Generation needs a gpu-scheduler: set SCHEDULER_URL and SCHEDULER_TOKEN in the environment (optional).
set -euo pipefail
cd "$(dirname "$0")/api"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt; }
mkdir -p ../.data
export DATABASE_URL=${DATABASE_URL:-sqlite:///$(pwd)/../.data/genai.db} IMAGES_DIR=${IMAGES_DIR:-$(pwd)/../.data/images}
export PUBLIC_URL=${PUBLIC_URL:-http://localhost:8097} DEV_LOGIN=1
[ -d ../ui/dist ] && export STATIC_DIR=$(pwd)/../ui/dist
nohup .venv/bin/uvicorn --factory genai.main:create_app --host 127.0.0.1 --port 8097 > ../.data/server.log 2>&1 &
echo $! > ../.data/server.pid
echo "gen-ai-platform on http://localhost:8097 (pid $(cat ../.data/server.pid), log .data/server.log)"
