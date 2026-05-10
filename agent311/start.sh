#!/bin/bash
set -e
exec uv run python -m uvicorn agent311.main:app --host 0.0.0.0 --port ${PORT:-8000}
