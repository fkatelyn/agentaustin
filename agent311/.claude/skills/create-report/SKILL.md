---
name: create-report
description: >
  Use when the user asks to "create a report", "generate a report", "make a report",
  "build a report", "report on 311 data", "311 report", "weekly report", "summary report",
  or any request that involves producing a standalone HTML report, PNG chart, or CSV export
  from 311 data. This skill uses the save_report MCP tool to write files to the reports
  directory so they appear in the sidebar file tree.
version: 3.0.0
---

# Create Report (DuckDB)

Generate self-contained reports from Austin 311 data using **DuckDB + plotly** and save them using the `save_report` MCP tool. Reports appear in the sidebar file tree for easy access.

## Important Rules

1. **Always use `save_report`** to write report files. Do NOT use Write, Bash, or any other tool to create report files.
2. **Filename convention:** `<topic>-report-<YYYY-MM-DD>.html` for HTML reports, `<topic>-chart-<YYYY-MM-DD>.png` for PNG charts, `<topic>-data-<YYYY-MM-DD>.csv` for CSV exports.
3. **Sanitize filenames:** Use lowercase, hyphens instead of spaces, no special characters.
4. **Use DuckDB for all queries.** Query DuckDB directly, use `fetchdf()` to get pandas DataFrames for plotly.

## DuckDB Path

```bash
DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-$(cd "$(dirname "$(find . -name pyproject.toml -maxdepth 1)")" && pwd)/data}"
DUCKDB_PATH="$DATA_DIR/311.duckdb"
```

## Report Types

### HTML Reports (Primary)

Build a Python script that:
1. Queries DuckDB for aggregations
2. Creates plotly charts from the result DataFrames
3. Wraps everything in an HTML template with metric cards, tables, and takeaways

#### Python Template

```python
import duckdb
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DUCKDB_PATH = '<DUCKDB_PATH>'  # substitute actual path
con = duckdb.connect(DUCKDB_PATH)

# --- Query data ---
total = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
df_types = con.execute("""
    SELECT sr_type_desc, COUNT(*) as cnt
    FROM service_requests GROUP BY sr_type_desc ORDER BY cnt DESC LIMIT 10
""").fetchdf()

# --- Build plotly charts ---
fig = make_subplots(rows=1, cols=2, ...)
fig.add_trace(...)
fig.update_layout(
    template='plotly_dark',
    paper_bgcolor='#1a1a2e',
    plot_bgcolor='#16213e',
    font=dict(color='#e0e0e0'),
    showlegend=False,
    height=400,
)

# Get chart HTML (div only, no full page)
chart_div = fig.to_html(full_html=False, include_plotlyjs=False)

con.close()

# --- Build report HTML ---
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>REPORT TITLE</title>
  <script src="https://cdn.jsdelivr.net/npm/plotly.js@2/dist/plotly.min.js"></script>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      background: #1a1a2e;
      color: #e0e0e0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
      padding: 32px;
    }}
    .report {{ max-width: 1100px; margin: 0 auto; }}
    h1 {{ text-align: center; font-size: 1.8rem; color: #fff; margin-bottom: 8px; }}
    .subtitle {{ text-align: center; color: #888; margin-bottom: 32px; }}
    .summary {{
      background: #0f3460; border-radius: 12px; padding: 20px 24px;
      margin-bottom: 32px; line-height: 1.6; color: #ccc;
    }}
    .metrics {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px; margin-bottom: 32px;
    }}
    .metric-card {{
      background: #16213e; border-radius: 12px; padding: 20px; text-align: center;
    }}
    .metric-card .value {{ font-size: 2rem; font-weight: 700; color: #00d2ff; }}
    .metric-card .label {{ font-size: 0.85rem; color: #888; margin-top: 4px; }}
    .chart-section {{
      background: #16213e; border-radius: 12px; padding: 24px; margin-bottom: 32px;
    }}
    table {{
      width: 100%; border-collapse: collapse; margin-bottom: 32px;
      background: #16213e; border-radius: 12px; overflow: hidden;
    }}
    th {{ background: #0f3460; padding: 12px 16px; text-align: left; font-size: 0.85rem; color: #aaa; }}
    td {{ padding: 12px 16px; border-top: 1px solid #333355; }}
    .takeaways {{
      background: #0f3460; border: 1px solid #1a5276; border-radius: 12px;
      padding: 24px; margin-top: 32px;
    }}
    .takeaways h2 {{ color: #00d2ff; margin-bottom: 12px; }}
    .takeaways li {{ margin-bottom: 8px; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="report">
    <h1>REPORT TITLE</h1>
    <p class="subtitle">Austin 311 · Date Range · {total:,} records</p>
    <div class="summary">Executive summary goes here.</div>
    <div class="metrics">
      <div class="metric-card"><div class="value">{total:,}</div><div class="label">Total Requests</div></div>
    </div>
    <div class="chart-section">
      {chart_div}
    </div>
    <div class="takeaways">
      <h2>Key Takeaways</h2>
      <ul><li>Finding 1</li><li>Finding 2</li></ul>
    </div>
  </div>
</body>
</html>"""

with open('/tmp/report_output.html', 'w') as f:
    f.write(html)
```

Then save via `save_report` and tell the user to check the sidebar.

### PNG Charts

For standalone chart images, use plotly with kaleido:

```python
import duckdb
import plotly.graph_objects as go

con = duckdb.connect('<DUCKDB_PATH>')
df = con.execute("SELECT ... FROM service_requests ...").fetchdf()
con.close()

fig = go.Figure(...)
fig.update_layout(template='plotly_dark', paper_bgcolor='#1a1a2e', plot_bgcolor='#16213e')
fig.write_image('/tmp/chart.png', width=1200, height=600, scale=2)
```

Then base64-encode and save:
```python
import base64
with open('/tmp/chart.png', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()
```
Call `save_report(filename="...", content=b64, encoding="base64")`

### CSV Exports

```python
import duckdb
con = duckdb.connect('<DUCKDB_PATH>')
csv_content = con.execute("SELECT ... FROM service_requests ...").fetchdf().to_csv(index=False)
con.close()
```
Call `save_report(filename="...", content=csv_content)`

## Workflow

1. Query DuckDB for aggregations and statistics
2. Build plotly charts from DataFrames
3. Wrap in report HTML template
4. Write to `/tmp/` as intermediate, then call `save_report` to persist
5. Summarize key findings in your chat response
6. Tell the user to check the file tree in the sidebar for the full report
