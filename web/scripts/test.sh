#!/usr/bin/env sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PROJECT_DIR/.uv-cache}"

cd "$PROJECT_DIR"

if [ "$#" -eq 0 ]; then
    exec uv run pytest tests/
fi

exec uv run pytest "$@"
