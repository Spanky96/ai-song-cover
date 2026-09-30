#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
exec uv run --python 3.12 uvicorn app.main:app --host 127.0.0.1 --port "${COVER_PORT:-8765}"
