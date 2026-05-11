---
name: analyze-311-data
description: >
  Use when the user asks to "analyze 311 data", "explore 311 data",
  "311 insights", "311 statistics", "311 trends", "what's interesting in 311",
  "show 311 charts", "311 bar chart", "311 report", or discusses analyzing,
  exploring, or visualizing Austin 311 service request data.
version: 3.0.0
---

# Analyze Austin 311 Data

Run exploratory analysis using the `query_duckdb` MCP tool and present findings.

## How to query

Use `query_duckdb(sql, max_rows=1000)`. It runs in-process against the local DuckDB
and returns JSON:

- `truncated=false` — full result in `rows`; use it directly.
- `truncated=true` — `rows` is a preview; `path` is a parquet with the full result, load via `pd.read_parquet(path)`.

Columns are stored as VARCHAR. Cast in SQL when needed (`::TIMESTAMP`, `::DOUBLE`, `::DATE`).

## Prerequisites

First confirm data is loaded:

    query_duckdb("SELECT count(*) AS total, MIN(sr_created_date) AS min_d, MAX(sr_created_date) AS max_d FROM service_requests")

If `total = 0`, tell the user the initial 7-day bootstrap hasn't finished and ask them to try again in a minute.

## Standard analysis queries

Run each as a separate `query_duckdb` call. Most are aggregates that return <100 rows — inline, fast, lock-safe.

### Volume

```
SELECT sr_type_desc, COUNT(*) c FROM service_requests GROUP BY 1 ORDER BY c DESC LIMIT 15
SELECT sr_department_desc, COUNT(*) c FROM service_requests GROUP BY 1 ORDER BY c DESC
SELECT sr_status_desc, COUNT(*) c FROM service_requests GROUP BY 1 ORDER BY c DESC
SELECT sr_method_received_desc, COUNT(*) c FROM service_requests GROUP BY 1 ORDER BY c DESC
```

### Timing

```
SELECT dayname(sr_created_date::TIMESTAMP) d, COUNT(*) c FROM service_requests WHERE sr_created_date IS NOT NULL GROUP BY 1 ORDER BY c DESC
SELECT hour(sr_created_date::TIMESTAMP) h, COUNT(*) c FROM service_requests WHERE sr_created_date IS NOT NULL GROUP BY 1 ORDER BY 1
SELECT sr_created_date::DATE d, COUNT(*) c FROM service_requests WHERE sr_created_date IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 5
```

### Geography

```
SELECT sr_location_zip_code z, COUNT(*) c FROM service_requests WHERE sr_location_zip_code IS NOT NULL AND sr_location_zip_code <> '' GROUP BY 1 ORDER BY c DESC LIMIT 10
SELECT sr_location_council_district d, COUNT(*) c FROM service_requests WHERE sr_location_council_district IS NOT NULL AND sr_location_council_district <> '' GROUP BY 1 ORDER BY c DESC
```

### Performance (closed requests only)

```
SELECT
  COUNT(*) AS total,
  AVG(EPOCH(sr_closed_date::TIMESTAMP - sr_created_date::TIMESTAMP) / 3600) AS avg_h,
  MEDIAN(EPOCH(sr_closed_date::TIMESTAMP - sr_created_date::TIMESTAMP) / 3600) AS med_h,
  MIN(EPOCH(sr_closed_date::TIMESTAMP - sr_created_date::TIMESTAMP) / 3600) AS min_h,
  MAX(EPOCH(sr_closed_date::TIMESTAMP - sr_created_date::TIMESTAMP) / 3600) AS max_h
FROM service_requests
WHERE sr_status_desc = 'Closed'
  AND sr_created_date IS NOT NULL AND sr_closed_date IS NOT NULL
  AND sr_closed_date::TIMESTAMP >= sr_created_date::TIMESTAMP
```

Average resolution by top 10 types:

```
WITH top_types AS (
  SELECT sr_type_desc FROM service_requests GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 10
)
SELECT
  s.sr_type_desc,
  COUNT(*) AS n,
  AVG(EPOCH(s.sr_closed_date::TIMESTAMP - s.sr_created_date::TIMESTAMP) / 3600) AS avg_h,
  MEDIAN(EPOCH(s.sr_closed_date::TIMESTAMP - s.sr_created_date::TIMESTAMP) / 3600) AS med_h
FROM service_requests s
JOIN top_types t ON s.sr_type_desc = t.sr_type_desc
WHERE s.sr_status_desc = 'Closed'
  AND s.sr_created_date IS NOT NULL AND s.sr_closed_date IS NOT NULL
  AND s.sr_closed_date::TIMESTAMP >= s.sr_created_date::TIMESTAMP
GROUP BY 1 ORDER BY avg_h DESC
```

### Backlog

```
SELECT sr_type_desc, COUNT(*) c FROM service_requests WHERE sr_status_desc <> 'Closed' GROUP BY 1 ORDER BY c DESC LIMIT 10
```

## Summarize

After running queries, present a numbered list of takeaways with specific numbers:

- **Volume** — top request types and departments
- **Performance** — fastest/slowest categories, average resolution
- **Geography** — top ZIPs and council districts
- **Timing** — day-of-week and hour-of-day patterns, anomalous days
- **Backlogs** — still-open categories with the largest counts

## When to visualize

If the user asks for a chart, follow the CHART WORKFLOW from the system prompt: fetch with `query_duckdb`, write a small pandas+plotly script (NO `import duckdb`), then `save_chart` + `view_content`.
