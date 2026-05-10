#!/bin/bash
set -e

# Factory-reset escape hatch: set FACTORY_RESET_311_DB=yes in Railway, deploy
# once, then unset it. The container deletes the DuckDB file on boot and the
# lifespan task in main.py rebootstraps from empty.
if [ "$FACTORY_RESET_311_DB" = "yes" ]; then
  DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-$(cd "$(dirname "$0")" && pwd)/data}"
  DUCKDB_PATH="$DATA_DIR/311.duckdb"
  echo "[factory-reset] FACTORY_RESET_311_DB=yes — deleting $DUCKDB_PATH (and .wal / .lock)"
  rm -f "$DUCKDB_PATH" "$DUCKDB_PATH.wal" "$DUCKDB_PATH.lock"
fi

exec uv run python -m uvicorn agent311.main:app --host 0.0.0.0 --port ${PORT:-8000}
