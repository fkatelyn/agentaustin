---
name: analyze-311-data
description: >
  Use when the user asks to "analyze 311 data", "explore 311 data",
  "311 insights", "311 statistics", "311 trends", "what's interesting in 311",
  "show 311 charts", "311 bar chart", "311 report", or discusses analyzing,
  exploring, or visualizing Austin 311 service request data.
version: 2.0.0
---

# Analyze Austin 311 Data (DuckDB)

Run exploratory analysis on the local DuckDB database and present findings.

## Prerequisites

The DuckDB database must exist with data. Check first:
```python
import duckdb, os
DUCKDB_PATH = '<volume>/311.duckdb'  # substitute actual path
if not os.path.exists(DUCKDB_PATH):
    print("No database found — run /download-311-data first")
else:
    con = duckdb.connect(DUCKDB_PATH)
    count = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
    print(f"Database has {count:,} rows")
    con.close()
```

If no data exists, tell the user to run `/download-311-data` first.

## DuckDB Path

```bash
DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-$(cd "$(dirname "$(find . -name pyproject.toml -maxdepth 1)")" && pwd)/data}"
DUCKDB_PATH="$DATA_DIR/311.duckdb"
```

## Analysis Script

Run this Python script with `uv run python3`. All queries use DuckDB SQL for speed:

```python
import duckdb

DUCKDB_PATH = '<DUCKDB_PATH>'  # substitute actual path
con = duckdb.connect(DUCKDB_PATH)

total = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
print(f"Total requests: {total:,}\n")

# --- Top 15 request types ---
print("=== TOP 15 REQUEST TYPES ===")
for row in con.execute("""
    SELECT sr_type_desc, COUNT(*) as cnt
    FROM service_requests GROUP BY sr_type_desc ORDER BY cnt DESC LIMIT 15
""").fetchall():
    print(f"  {row[1]:>7,}  {row[0]}")

# --- By department ---
print("\n=== BY DEPARTMENT ===")
for row in con.execute("""
    SELECT sr_department_desc, COUNT(*) as cnt
    FROM service_requests GROUP BY sr_department_desc ORDER BY cnt DESC
""").fetchall():
    print(f"  {row[1]:>7,}  {row[0]}")

# --- Status breakdown ---
print("\n=== STATUS ===")
for row in con.execute("""
    SELECT sr_status_desc, COUNT(*) as cnt
    FROM service_requests GROUP BY sr_status_desc ORDER BY cnt DESC
""").fetchall():
    print(f"  {row[1]:>7,}  {row[0]}")

# --- How reported ---
print("\n=== HOW REPORTED ===")
for row in con.execute("""
    SELECT sr_method_received_desc, COUNT(*) as cnt
    FROM service_requests GROUP BY sr_method_received_desc ORDER BY cnt DESC
""").fetchall():
    print(f"  {row[1]:>7,}  {row[0]}")

# --- By day of week ---
print("\n=== BY DAY OF WEEK ===")
for row in con.execute("""
    SELECT dayname(sr_created_date::TIMESTAMP) as dow, COUNT(*) as cnt
    FROM service_requests
    WHERE sr_created_date IS NOT NULL
    GROUP BY dow ORDER BY cnt DESC
""").fetchall():
    print(f"  {row[1]:>7,}  {row[0]}")

# --- By hour of day ---
print("\n=== BY HOUR OF DAY ===")
for row in con.execute("""
    SELECT hour(sr_created_date::TIMESTAMP) as hr, COUNT(*) as cnt
    FROM service_requests
    WHERE sr_created_date IS NOT NULL
    GROUP BY hr ORDER BY hr
""").fetchall():
    bar = '#' * (row[1] // 5000)
    print(f"  {row[0]:>2}:00  {row[1]:>7,}  {bar}")

# --- Busiest days ---
print("\n=== BUSIEST DAYS ===")
for row in con.execute("""
    SELECT sr_created_date::DATE as d, COUNT(*) as cnt
    FROM service_requests
    WHERE sr_created_date IS NOT NULL
    GROUP BY d ORDER BY cnt DESC LIMIT 5
""").fetchall():
    print(f"  {row[1]:>7,}  {row[0]}")

# --- Top 10 ZIP codes ---
print("\n=== TOP 10 ZIP CODES ===")
for row in con.execute("""
    SELECT sr_location_zip_code, COUNT(*) as cnt
    FROM service_requests
    WHERE sr_location_zip_code IS NOT NULL AND sr_location_zip_code != ''
    GROUP BY sr_location_zip_code ORDER BY cnt DESC LIMIT 10
""").fetchall():
    print(f"  {row[1]:>7,}  {row[0]}")

# --- By council district ---
print("\n=== BY COUNCIL DISTRICT ===")
for row in con.execute("""
    SELECT sr_location_council_district, COUNT(*) as cnt
    FROM service_requests
    WHERE sr_location_council_district IS NOT NULL AND sr_location_council_district != ''
    GROUP BY sr_location_council_district ORDER BY cnt DESC
""").fetchall():
    print(f"  {row[1]:>7,}  District {row[0]}")

# --- Resolution time stats ---
print("\n=== RESOLUTION TIME (closed requests) ===")
res = con.execute("""
    SELECT
        COUNT(*) as total,
        AVG(EPOCH(sr_closed_date::TIMESTAMP - sr_created_date::TIMESTAMP) / 3600) as avg_hours,
        MEDIAN(EPOCH(sr_closed_date::TIMESTAMP - sr_created_date::TIMESTAMP) / 3600) as med_hours,
        MIN(EPOCH(sr_closed_date::TIMESTAMP - sr_created_date::TIMESTAMP) / 3600) as min_hours,
        MAX(EPOCH(sr_closed_date::TIMESTAMP - sr_created_date::TIMESTAMP) / 3600) as max_hours
    FROM service_requests
    WHERE sr_status_desc = 'Closed'
      AND sr_created_date IS NOT NULL AND sr_closed_date IS NOT NULL
      AND sr_closed_date >= sr_created_date
""").fetchone()
print(f"  Total closed: {res[0]:,}")
if res[0] > 0:
    print(f"  Avg resolution: {res[1]:.1f} hours ({res[1]/24:.1f} days)")
    print(f"  Median resolution: {res[2]:.1f} hours ({res[2]/24:.1f} days)")
    print(f"  Fastest: {res[3]:.2f} hours")
    print(f"  Slowest: {res[4]:.1f} hours ({res[4]/24:.0f} days)")

# --- Avg resolution by type (top 10 types) ---
print("\n=== AVG RESOLUTION BY TYPE (top 10 types) ===")
for row in con.execute("""
    WITH top_types AS (
        SELECT sr_type_desc FROM service_requests GROUP BY sr_type_desc ORDER BY COUNT(*) DESC LIMIT 10
    )
    SELECT
        s.sr_type_desc,
        COUNT(*) as n,
        AVG(EPOCH(s.sr_closed_date::TIMESTAMP - s.sr_created_date::TIMESTAMP) / 3600) as avg_h,
        MEDIAN(EPOCH(s.sr_closed_date::TIMESTAMP - s.sr_created_date::TIMESTAMP) / 3600) as med_h
    FROM service_requests s
    JOIN top_types t ON s.sr_type_desc = t.sr_type_desc
    WHERE s.sr_status_desc = 'Closed'
      AND s.sr_created_date IS NOT NULL AND s.sr_closed_date IS NOT NULL
      AND s.sr_closed_date >= s.sr_created_date
    GROUP BY s.sr_type_desc
    ORDER BY avg_h DESC
""").fetchall():
    print(f"  {row[2]:>7.1f}h avg | {row[3]:>6.1f}h med  {row[0]} (n={row[1]:,})")

# --- Still open requests ---
print("\n=== STILL OPEN - BY TYPE ===")
open_count = con.execute("SELECT count(*) FROM service_requests WHERE sr_status_desc != 'Closed'").fetchone()[0]
print(f"  Total open: {open_count:,}")
for row in con.execute("""
    SELECT sr_type_desc, COUNT(*) as cnt
    FROM service_requests WHERE sr_status_desc != 'Closed'
    GROUP BY sr_type_desc ORDER BY cnt DESC LIMIT 10
""").fetchall():
    print(f"  {row[1]:>7,}  {row[0]}")

# --- Date range ---
print("\n=== DATA RANGE ===")
dr = con.execute("SELECT MIN(sr_created_date), MAX(sr_created_date) FROM service_requests").fetchone()
print(f"  From: {dr[0]}")
print(f"  To:   {dr[1]}")

con.close()
```

## Summarize Key Findings

After running the analysis, write a summary highlighting:

- **Volume:** Most common request types and departments
- **Performance:** Resolution times — fastest/slowest types
- **Geography:** Top ZIPs and council districts
- **Timing:** Day of week / hour patterns, anomalous days
- **Backlogs:** Still-open request categories

Present as a numbered list of key takeaways with specific numbers.
