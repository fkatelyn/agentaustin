# Agent Austin

An AI-powered data science agent for Austin 311 service request data. Powered by Claude, Anthropic's LLM, it analyzes, visualizes, and summarizes city service data — just ask in plain English.

## What You Can Do

Talk to the agent like you're chatting with a data analyst. Here are some things to try:

**Get the data**
> "Download the last month of 311 data"
> "Fetch 311 requests from the past 3 months"

**Explore it**
> "What are the most common complaints in Austin?"
> "Show me a breakdown by department"
> "Which neighborhoods generate the most 311 requests?"
> "What day of the week has the most requests?"

**Visualize it**
> "Make a chart of request types"
> "Show trends over time"
> "Compare response rates by district"

**Check resolution**
> "What's the resolution rate for pothole complaints?"
> "How long does it take to resolve a missed trash pickup?"

## Built-in Skills

**Download 311 Data** — Fetches real service request data from the City of Austin Open Data Portal. Ask for any date range, filter by department or ZIP code.

**Analyze 311 Data** — Runs exploratory analysis on downloaded data: top request types, busiest days and hours, resolution times, open vs. closed rates, and more.

**Visualize** — Automatically creates interactive charts whenever the answer involves numbers. Opens them in your browser. Can also produce ASCII charts if you prefer the terminal.

**Resolution Rate** — Given any type of 311 complaint, shows the historical resolution rate for that category, why some don't get resolved, and tips to improve your odds.

**Estimate Resolution Time** — Takes any complaint and estimates how long it's likely to take to resolve.

## Quick Start

### Backend

```bash
cd agent311
export ANTHROPIC_API_KEY=sk-ant-...
export JWT_SECRET=your-secret-key
uv run uvicorn agent311.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd agentui
npm install
npm run dev
```

Open http://localhost:3000.

## What is Austin 311?

Austin 311 is the City of Austin's non-emergency service request system. Residents use it to report issues like potholes, illegal dumping, stray animals, missed trash pickups, and more. The dataset goes back to 2014 and is updated daily — about 2.4 million records total.

- **Phone:** 3-1-1 (or 512-974-2000 from outside Austin)
- **Web:** https://311.austin.gov
- **App:** Austin 311 (iOS/Android)

## Directory Layout

```
agentaustin/
├── agent311/                  # Backend
│   ├── agent311/              # Python package
│   ├── .claude/skills/        # Agent skills (download, analyze, visualize, etc.)
│   └── data/                  # Downloaded data, charts, and reports (gitignored)
├── agentui/                   # Frontend
│   ├── app/                   # Pages
│   ├── components/            # React components
│   └── lib/                   # API clients and utilities
└── docs/                      # Documentation
```

**Data storage:** The `agent311/data/` directory holds all downloaded 311 CSV data, generated charts, and saved reports. On Railway, this maps to a persistent volume (`RAILWAY_VOLUME_MOUNT_PATH`). Locally it lives at `agent311/data/`. This directory is gitignored — data files are never committed.

## Data Analysis Example: Potholes

Here's a walkthrough of how to use the agent to analyze pothole complaints in Austin.

**1. Download the data**
> "Download 311 data"

This refreshes the local DuckDB at `agent311/data/311.duckdb` with the latest service requests from the City of Austin Open Data Portal. (`start.sh` seeds the last 7 days on first boot; the FastAPI lifespan async task catches up forward and extends history backward every 24h.)

**2. Ask about potholes**
> "How many pothole complaints were filed this year?"
> "What's the average resolution time for potholes?"
> "Which ZIP codes have the most pothole reports?"

**3. Visualize trends**
> "Show me a monthly trend of pothole complaints"
> "Make a bar chart of potholes by council district"

The agent generates interactive Plotly charts and saves them to `agent311/data/analysis/charts/`.

**4. Generate a report**
> "Create a pothole analysis report"

The agent produces an HTML report with charts, tables, and summary statistics, saved to `agent311/data/reports/`. Reports are accessible from the sidebar in the web UI.

**5. Compare and dig deeper**
> "Compare pothole resolution rates across departments"
> "Are pothole complaints increasing or decreasing year over year?"
> "What percentage of pothole requests are still open?"

All generated files (CSVs, charts, reports) are stored under `agent311/data/` and persist across server restarts on Railway.

## Documentation

- **[Architecture](docs/architecture.md)** — Stack, project structure, API endpoints, dataset schema
- **[Frontend Guide](docs/agentui-frontend.md)** — Component breakdown, SSE parsing, artifact preview
- **[Backend Guide](docs/agent-sdk-guide.md)** — SDK integration, skills, custom MCP tools
- **[Railway Deployment Guide](docs/railway-deployment-guide.md)** — Full deployment instructions

