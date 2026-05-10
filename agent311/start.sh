#!/bin/bash
set -e

echo "============================================"
echo "  Agent 311 — Starting up"
echo "============================================"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-$SCRIPT_DIR/data}"
DUCKDB_PATH="$DATA_DIR/311.duckdb"
mkdir -p "$DATA_DIR"

echo "Data directory: $DATA_DIR"
echo "DuckDB path:    $DUCKDB_PATH"
echo ""

# If the DB is empty (or missing), seed it with the last 7 days so the API is
# useful immediately. The lifespan async task in main.py then catches up the
# tail and extends history backward in the background.
uv run python3 -c "
import duckdb, urllib.parse, urllib.request, sys
from datetime import datetime, timedelta, timezone

DUCKDB_PATH = '$DUCKDB_PATH'
SOCRATA = 'https://data.austintexas.gov/resource/xwdj-i9he.csv'
WINDOW_DAYS = 7

con = duckdb.connect(DUCKDB_PATH)

try:
    rows = con.execute('SELECT count(*) FROM service_requests').fetchone()[0]
    if rows > 0:
        print(f'[bootstrap] DB already has {rows:,} rows — skipping seed')
        con.close()
        sys.exit(0)
except duckdb.CatalogException:
    pass

since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime('%Y-%m-%dT%H:%M:%S')
where = urllib.parse.quote(f\"sr_created_date >= '{since}'\")
url = f'{SOCRATA}?\$where={where}&\$order=sr_created_date&\$limit=100000'

print(f'[bootstrap] Seeding DB with rows since {since[:10]} ...')
req = urllib.request.Request(url, headers={'User-Agent': 'agent-austin/1.0'})
with urllib.request.urlopen(req, timeout=120) as resp:
    body = resp.read()
with open('/tmp/311_seed.csv', 'wb') as f:
    f.write(body)

con.execute(\"CREATE TABLE service_requests AS SELECT * FROM read_csv_auto('/tmp/311_seed.csv', all_varchar=true)\")
con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_number ON service_requests(sr_number)')
final = con.execute('SELECT count(*) FROM service_requests').fetchone()[0]
print(f'[bootstrap] Seeded {final:,} rows')
con.close()
"

echo ""
echo "Starting uvicorn..."
exec uv run python -m uvicorn agent311.main:app --host 0.0.0.0 --port ${PORT:-8000}
