#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p data

python3 -m pip install -r requirements.txt
exec python3 -m uvicorn run:app --host 127.0.0.1 --port 8000 --reload
