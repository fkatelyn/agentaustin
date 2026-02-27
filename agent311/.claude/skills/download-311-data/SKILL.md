---
name: download-311-data
description: >
  Use when the user asks to "download 311 data", "get 311 data",
  "fetch Austin 311", "update 311 data", "refresh 311 data",
  "download service requests", or discusses downloading or updating
  City of Austin 311 service request data.
version: 2.0.0
---

# Download / Update Austin 311 Data (DuckDB)

Download City of Austin 311 service request data from the Socrata Open Data API into a local DuckDB database.

## Data Source

- **Portal:** City of Austin Open Data (data.austintexas.gov)
- **Dataset ID:** `xwdj-i9he`
- **API endpoint:** `https://data.austintexas.gov/resource/xwdj-i9he.csv`
- **No API key required** for reasonable request volumes
- **Full dataset:** ~7.8M records from 2014-present

## DuckDB Path

```bash
DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-$(cd "$(dirname "$(find . -name pyproject.toml -maxdepth 1)")" && pwd)/data}"
DUCKDB_PATH="$DATA_DIR/311.duckdb"
mkdir -p "$DATA_DIR"
```

## Modes

### Mode 1: Full Download (empty database or user requests full refresh)

Use this when the DuckDB database doesn't exist, the `service_requests` table is empty, no local CSV is available, or the user explicitly asks to re-download all data.

Write and run this Python script:

```python
import duckdb
import urllib.request
import os

DUCKDB_PATH = '<DUCKDB_PATH>'  # substitute actual path
LIMIT = 100000

con = duckdb.connect(DUCKDB_PATH)

# Get total count from API
count_url = "https://data.austintexas.gov/resource/xwdj-i9he.csv?$select=count(*)&$limit=1"
response = urllib.request.urlopen(count_url)
total = int(response.read().decode().strip().split('\n')[1].strip('"'))
print(f"Total records available from API: {total:,}")

# Drop existing table for full re-download
con.execute("DROP TABLE IF EXISTS service_requests")

offset = 0
chunk = 0
while offset < total:
    url = f"https://data.austintexas.gov/resource/xwdj-i9he.csv?$limit={LIMIT}&$offset={offset}&$order=:id"
    print(f"Downloading chunk {chunk} (offset={offset:,} / {total:,})...")
    response = urllib.request.urlopen(url)
    tmp = '/tmp/311_chunk.csv'
    with open(tmp, 'wb') as f:
        f.write(response.read())

    rows = con.execute(f"SELECT count(*) FROM read_csv_auto('{tmp}', all_varchar=true)").fetchone()[0]

    if chunk == 0:
        con.execute(f"CREATE TABLE service_requests AS SELECT * FROM read_csv_auto('{tmp}', all_varchar=true)")
    else:
        con.execute(f"INSERT INTO service_requests SELECT * FROM read_csv_auto('{tmp}', all_varchar=true)")

    os.unlink(tmp)
    offset += LIMIT
    chunk += 1
    loaded = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
    print(f"  Loaded {loaded:,} rows so far")

    if rows < LIMIT:
        break

# Add index on sr_number for fast merges
con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_number ON service_requests(sr_number)")

final = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
date_range = con.execute("SELECT MIN(sr_created_date), MAX(sr_created_date) FROM service_requests").fetchone()
print(f"\nDone! {final:,} rows loaded")
print(f"Date range: {date_range[0]} to {date_range[1]}")
con.close()
```

### Mode 2: Update / Merge (database already has data)

Use this when the database already has data and the user wants to refresh with recent records. Downloads data since the latest record and merges using `sr_number` to avoid duplicates.

```python
import duckdb
import urllib.request
import os
from datetime import datetime, timedelta

DUCKDB_PATH = '<DUCKDB_PATH>'  # substitute actual path
LIMIT = 100000

con = duckdb.connect(DUCKDB_PATH)

# Find the latest date in the database
latest = con.execute("SELECT MAX(sr_created_date) FROM service_requests").fetchone()[0]
current_count = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
print(f"Current rows in DB: {current_count:,}")
print(f"Latest record date: {latest}")

# Fetch from 2 days before latest (overlap to catch late-arriving records)
start_date = (datetime.fromisoformat(str(latest)[:19]) - timedelta(days=2)).strftime('%Y-%m-%dT00:00:00')
print(f"Fetching records from {start_date}...")

offset = 0
total_processed = 0
total_new = 0

while True:
    url = (
        f"https://data.austintexas.gov/resource/xwdj-i9he.csv"
        f"?$where=sr_created_date>='{start_date}'"
        f"&$limit={LIMIT}&$offset={offset}&$order=:id"
    )
    response = urllib.request.urlopen(url)
    tmp = '/tmp/311_update.csv'
    with open(tmp, 'wb') as f:
        f.write(response.read())

    rows = con.execute(f"SELECT count(*) FROM read_csv_auto('{tmp}', all_varchar=true)").fetchone()[0]
    if rows == 0:
        os.unlink(tmp)
        break

    # Merge via staging table: delete matching sr_numbers then insert all
    con.execute(f"CREATE TEMPORARY TABLE staging AS SELECT * FROM read_csv_auto('{tmp}', all_varchar=true)")
    deleted = con.execute("DELETE FROM service_requests WHERE sr_number IN (SELECT sr_number FROM staging)").fetchone()[0]
    con.execute("INSERT INTO service_requests SELECT * FROM staging")
    con.execute("DROP TABLE staging")

    new_rows = rows - deleted
    total_processed += rows
    total_new += new_rows
    offset += LIMIT
    os.unlink(tmp)
    print(f"  Processed {total_processed:,} records ({total_new:,} new, {total_processed - total_new:,} updated)")

    if rows < LIMIT:
        break

final = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
date_range = con.execute("SELECT MIN(sr_created_date), MAX(sr_created_date) FROM service_requests").fetchone()
print(f"\nDone! DB now has {final:,} rows ({total_new:,} new records added)")
print(f"Date range: {date_range[0]} to {date_range[1]}")
con.close()
```

## Decision Logic

1. Check if DuckDB exists and has data:
   ```python
   import duckdb, os
   DUCKDB_PATH = '<path>'
   if not os.path.exists(DUCKDB_PATH):
       print("No database found — use Mode 1 (full download)")
   else:
       con = duckdb.connect(DUCKDB_PATH)
       try:
           count = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
           print(f"Database has {count:,} rows — use Mode 2 (update/merge)")
       except:
           print("Table missing — use Mode 1 (full download)")
       con.close()
   ```

2. If DuckDB is empty/missing → **Mode 1** (full API download)
3. If user says "download all", "re-download", "fresh download" → **Mode 1**
4. If user says "update", "refresh", "get latest" → **Mode 2**

## Dataset Columns

| Column | Description |
|--------|-------------|
| `sr_number` | Unique service request ID (used as merge key) |
| `sr_type_desc` | Request type (e.g., "ARR - Garbage") |
| `sr_department_desc` | Responsible city department |
| `sr_method_received_desc` | How it was reported (Phone, App, Web) |
| `sr_status_desc` | Status (Open, Closed, Duplicate, etc.) |
| `sr_status_date` | Last status change timestamp |
| `sr_created_date` | When the request was filed |
| `sr_updated_date` | Last update timestamp |
| `sr_closed_date` | When the request was closed |
| `sr_location` | Full address string |
| `sr_location_zip_code` | ZIP code |
| `sr_location_lat` | Latitude |
| `sr_location_long` | Longitude |
| `sr_location_council_district` | City council district number |

## Output

Report to the user:
- Total rows in database
- Number of new records (for updates)
- Date range covered
- Database file location and size
