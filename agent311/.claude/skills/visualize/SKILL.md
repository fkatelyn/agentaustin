---
name: visualize
description: >
  Use AUTOMATICALLY whenever responding to data-related prompts — any question
  involving counts, comparisons, trends, percentages, rankings, distributions,
  breakdowns, or statistics from 311 data or any CSV/dataset. Do NOT wait for
  the user to ask for a chart. If the answer involves numbers, visualize them.
  DEFAULT: Generate a plotly HTML chart and save it with save_chart for persistent
  storage, then use view_content to preview it in the artifact panel.
  EXCEPTION: If the user says "in text", "ascii", "terminal", or "in the terminal",
  use ASCII text charts instead.
  Trigger phrases include but are not limited to: "how many", "what percentage",
  "compare", "top", "worst", "most common", "trend", "over time", "by district",
  "by zip", "breakdown", "distribution", "average", "which", or any question
  whose answer benefits from a visual representation.
version: 4.0.0
---

# Visualize Data (DuckDB)

Whenever you answer a data-related question, **always include a visualization**. There are two modes:

- **Default → Plotly HTML** (saved persistently via `save_chart`, previewed via `view_content`)
- **Text mode → ASCII** (only when user says "in text", "ascii", "terminal", or "in the terminal")

---

## MODE 1: Plotly HTML (Default)

Write a Python script using **DuckDB + plotly** to query data and generate an interactive HTML chart. Save it with `save_chart`, preview with `view_content`.

### Rules

1. **Always visualize.** If your answer includes 3+ data points, generate a chart. No exceptions.
2. **Use DuckDB for queries.** Query DuckDB directly, use `fetchdf()` to get pandas DataFrames for plotly.
3. **Dark theme.** Use `template='plotly_dark'` with custom background colors to match the app aesthetic.
4. **Save with `save_chart`.** ALWAYS use the `save_chart` MCP tool to save the final HTML. Do NOT use `Write` or `Bash` to write chart files. Filename convention: `<descriptive-name>-chart-<YYYY-MM-DD>.html`.
5. **Preview with `view_content`.** After saving, call `view_content` on the saved path to display it in the artifact panel.
6. **Multiple charts per page.** Use `make_subplots` for multi-panel dashboards.
7. **Summarize in your response.** After generating the chart, include a brief text summary of the key findings in your message.
8. **CDN mode.** Always use `include_plotlyjs='cdn'` in `write_html()` to keep file size small.

### DuckDB Path

```bash
DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-$(cd "$(dirname "$(find . -name pyproject.toml -maxdepth 1)")" && pwd)/data}"
DUCKDB_PATH="$DATA_DIR/311.duckdb"
```

### Python Template

```python
import duckdb
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DUCKDB_PATH = '<DUCKDB_PATH>'  # substitute actual path
con = duckdb.connect(DUCKDB_PATH)

# Query and get DataFrame
df = con.execute("""
    SELECT sr_type_desc, COUNT(*) as cnt
    FROM service_requests
    WHERE sr_type_desc LIKE '%Pothole%'
    GROUP BY sr_type_desc ORDER BY cnt DESC
""").fetchdf()

daily = con.execute("""
    SELECT sr_created_date::DATE as day, COUNT(*) as count
    FROM service_requests
    WHERE sr_type_desc LIKE '%Pothole%'
    GROUP BY day ORDER BY day
""").fetchdf()

con.close()

# Build figure
fig = make_subplots(
    rows=1, cols=2,
    subplot_titles=('Daily Trend', 'Status Breakdown'),
    specs=[[{'type': 'xy'}, {'type': 'domain'}]]
)

fig.add_trace(go.Bar(x=daily['day'], y=daily['count'],
    marker_color='#00d2ff'), row=1, col=1)

# Apply dark theme
fig.update_layout(
    template='plotly_dark',
    paper_bgcolor='#1a1a2e',
    plot_bgcolor='#16213e',
    font=dict(color='#e0e0e0'),
    title=dict(text='Chart Title', font=dict(size=20, color='white')),
    showlegend=False,
    height=500,
    margin=dict(t=80, b=40, l=60, r=40),
)

# Save to /tmp (intermediate), then save_chart will persist it
fig.write_html('/tmp/chart_output.html', include_plotlyjs='cdn')
```

### Choosing Plotly Chart Types

| Data Shape | Plotly Type | Notes |
|---|---|---|
| Ranked categories | `go.Bar` with `orientation='h'` | Use for top-N lists |
| Values over time | `go.Scatter` with `fill='tozeroy'` | Area chart for trends |
| Proportions / shares | `go.Pie` with `hole=0.4` | Donut for status breakdowns |
| Numeric distributions | `go.Histogram` | Automatic binning |
| 24-hour / weekly cycle | `go.Bar` or `go.Scatter` | Bar for discrete, line for continuous |
| Comparing 2 groups | `go.Bar` with grouped `barmode='group'` | Side-by-side bars |
| Geographic (district/zip) | `go.Bar` with `orientation='h'` | Sorted by value descending |

### Color Palette

Use these colors consistently:
```python
COLORS = [
    '#00d2ff',  # cyan
    '#7b2ff7',  # purple
    '#ff6b6b',  # red/coral
    '#ffd93d',  # yellow
    '#6bcb77',  # green
    '#4d96ff',  # blue
    '#ff922b',  # orange
    '#845ef7',  # violet
    '#20c997',  # teal
    '#f06595',  # pink
]
```

### Workflow

1. Write a Python script using duckdb + plotly to query data and build the chart
2. Run the script with `uv run python3 /tmp/chart_script.py` (write script to /tmp first)
3. The script saves HTML to `/tmp/<name>.html` via `fig.write_html(..., include_plotlyjs='cdn')`
4. Read the HTML file content back, then call `save_chart(filename="<name>-chart-<YYYY-MM-DD>.html", content=<html_string>, encoding="text")` to persist it
5. Call `view_content(path=<saved_path>)` to display the chart in the artifact panel
6. In your response text, summarize the key findings

---

## MODE 2: ASCII Text (On Request)

Use this mode ONLY when the user explicitly asks for "text", "ascii", "terminal", or "in the terminal".

### Rules

1. **Always visualize.** If your answer includes 3+ data points, include a chart.
2. **Print directly in your response message.** The ASCII charts MUST appear in your chat response as markdown fenced code blocks (triple backticks). Do NOT rely on Bash tool output — the user cannot see tool output. After computing the data with Bash, copy/reproduce the charts into your response text inside ``` blocks.
3. **Pick the right chart type** for the data.
4. **Keep it compact.** Max 40 characters for bar width.
5. **Include numbers.** Every bar/row must show its count and percentage.
6. **Add context.** Use `◄──` markers for peaks, outliers, or notable values.
7. **Use box drawing.** Frame titles with `┌─┐ │ │ └─┘` borders.

### ASCII Chart Types

**Horizontal Bar:**
```
┌─────────────────────────────────────────┐
│  TITLE HERE                             │
└─────────────────────────────────────────┘
Label A   ████████████████████████████  142 (38%)
Label B   ██████████████████░░░░░░░░░░   95 (25%)
Label C   ████████████░░░░░░░░░░░░░░░░   63 (17%)
```
Use `█` for filled and `░` for unfilled.

**Timeline:**
```
┌─────────────────────────────────────────┐
│  TITLE OVER TIME                        │
└─────────────────────────────────────────┘
Jan  ███████████░░░░░░░░░░░░░░░░░░░  118
Feb  ██████████████████████████░░░░░  278
Mar  ████████████████████████████░░░  317 ◄── peak
```

**Distribution:**
```
┌─────────────────────────────────────────┐
│  DISTRIBUTION OF X                      │
└─────────────────────────────────────────┘
< 6 hrs    █████████████████████████████  1144 (39%)
6-12 hrs   ████████░░░░░░░░░░░░░░░░░░░░   288 (10%)

Median: 12.3 hours  ·  Mean: 17.0 hours
```

### Bar Scaling
```
filled = int((value / max_value) * BAR_WIDTH)
bar = '█' * filled + '░' * (BAR_WIDTH - filled)
```
Use `BAR_WIDTH = 30` for most charts.
