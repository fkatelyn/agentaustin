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

# Download all 311 data into DuckDB if empty
uv run python3 -c "
import duckdb, urllib.request, os, sys, time

DUCKDB_PATH = '$DUCKDB_PATH'
LIMIT = 100000

con = duckdb.connect(DUCKDB_PATH)
try:
    count = con.execute('SELECT count(*) FROM service_requests').fetchone()[0]
    if count > 0:
        date_range = con.execute('SELECT MIN(sr_created_date), MAX(sr_created_date) FROM service_requests').fetchone()
        print(f'[data] DuckDB has {count:,} rows ({date_range[0][:10]} to {date_range[1][:10]})')
        print(f'[data] Skipping download — data already loaded')
        con.close()
        sys.exit(0)
except Exception:
    pass

print('=' * 50)
print('  First run — downloading all Austin 311 data')
print('  Source: data.austintexas.gov (Socrata API)')
print('=' * 50)
print()

start_time = time.time()

count_url = 'https://data.austintexas.gov/resource/xwdj-i9he.csv?\$select=count(*)&\$limit=1'
response = urllib.request.urlopen(count_url)
total = int(response.read().decode().strip().split('\n')[1].strip('\"'))
total_chunks = (total + LIMIT - 1) // LIMIT
print(f'[data] Total records: {total:,} ({total_chunks} batches of {LIMIT:,})')
print()

con.execute('DROP TABLE IF EXISTS service_requests')

offset = 0
chunk = 0
while offset < total:
    chunk += 1
    pct = min(offset / total * 100, 99.9)
    bar_len = 30
    filled = int(bar_len * offset / total)
    bar = '█' * filled + '░' * (bar_len - filled)
    elapsed = time.time() - start_time
    print(f'\r  [{bar}] {pct:5.1f}%  Batch {chunk}/{total_chunks}  ({offset:,}/{total:,} rows)  {elapsed:.0f}s', end='', flush=True)

    url = f'https://data.austintexas.gov/resource/xwdj-i9he.csv?\$limit={LIMIT}&\$offset={offset}&\$order=:id'
    response = urllib.request.urlopen(url)
    tmp = '/tmp/311_chunk.csv'
    with open(tmp, 'wb') as f:
        f.write(response.read())

    rows = con.execute(f\"SELECT count(*) FROM read_csv_auto('{tmp}', all_varchar=true)\").fetchone()[0]

    if chunk == 1:
        con.execute(f\"CREATE TABLE service_requests AS SELECT * FROM read_csv_auto('{tmp}', all_varchar=true)\")
    else:
        con.execute(f\"INSERT INTO service_requests SELECT * FROM read_csv_auto('{tmp}', all_varchar=true)\")

    os.unlink(tmp)
    offset += LIMIT

    if rows < LIMIT:
        break

bar = '█' * bar_len
elapsed = time.time() - start_time
print(f'\r  [{bar}] 100.0%  Done!{\" \" * 40}')
print()

print('[data] Creating index on sr_number...')
con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_number ON service_requests(sr_number)')

final = con.execute('SELECT count(*) FROM service_requests').fetchone()[0]
date_range = con.execute('SELECT MIN(sr_created_date), MAX(sr_created_date) FROM service_requests').fetchone()
db_size = os.path.getsize(DUCKDB_PATH) / (1024 * 1024)

print()
print('=' * 50)
print(f'  Download complete!')
print(f'  Rows:      {final:,}')
print(f'  Range:     {date_range[0][:10]} to {date_range[1][:10]}')
print(f'  DB size:   {db_size:.1f} MB')
print(f'  Time:      {elapsed:.0f}s')
print('=' * 50)
print()
con.close()
"

echo "Starting uvicorn..."
uv run python -m uvicorn agent311.main:app --host 0.0.0.0 --port ${PORT:-8000}
