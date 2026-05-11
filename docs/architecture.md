# Architecture

Agent Austin is a full-stack app: a FastAPI backend that runs a Claude AI agent, and a Next.js frontend for the chat UI.

> **See also:** [architecture/](architecture/README.md) for the system diagram and agent skills reference.

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, uvicorn |
| AI | Claude Agent SDK, Claude Code CLI |
| Database | PostgreSQL, SQLAlchemy async, asyncpg |
| Auth | JWT (PyJWT, HS256) |
| Frontend | Next.js 16, React 19, Tailwind CSS 4 |
| UI | AI Elements, shadcn/ui, Streamdown |
| Deployment | Railway, Railpack |
| Package mgmt | uv (Python), npm (JavaScript) |

## How It Works

The backend exposes a `/api/chat` endpoint that streams AI responses using Server-Sent Events (SSE) in Vercel AI SDK v6 format. The frontend connects via JWT-authenticated requests and renders responses with live markdown streaming.

Tool invocations are emitted as `text-delta` markers: `[Using tool: Read]`, `[Using tool: view_content /path/to/file.html]`.

### MCP Tools

The agent has three custom MCP tools:

- **`view_content`** — Exposes a file for frontend preview in the artifact panel. Restricted to `/tmp/` and the persistent volume mount, max 200KB, allowed extensions: `.html`, `.js`, `.jsx`, `.tsx`, `.png`, `.csv`.
- **`save_chart`** — Saves an agent-generated chart (HTML or PNG) to `<volume>/analysis/charts/`. Returns the saved path for use with `view_content`.
- **`save_report`** — Saves a report (HTML, PNG, CSV, PDF) to `<volume>/reports/`. Returns the saved path for use with `view_content`.

## Project Structure

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

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Get JWT token |
| `POST` | `/api/chat` | Stream chat response (SSE) |
| `GET` | `/api/sessions` | List all sessions |
| `POST` | `/api/sessions` | Create session |
| `GET` | `/api/sessions/{id}` | Get session with messages |
| `PATCH` | `/api/sessions/{id}` | Update title or favorite |
| `DELETE` | `/api/sessions/{id}` | Delete session |
| `PATCH` | `/api/messages/{id}` | Update a message |
| `GET` | `/api/reports` | List report files |
| `GET` | `/api/reports/download` | Download a report file |
| `POST` | `/api/reports/upload` | Upload a report file |
| `PATCH` | `/api/reports/{filename}` | Rename a report file |
| `DELETE` | `/api/reports/{filename}` | Delete a report file |
| `GET` | `/api/fetch_file` | Fetch file for preview |

### `POST /api/chat`

**Request:**
```json
{
  "messages": [{ "role": "user", "content": "What's the top 311 complaint in Austin?" }],
  "session_id": "uuid"
}
```

**Response:** SSE stream
```
data: {"type":"start","messageId":"..."}
data: {"type":"text-start","id":"..."}
data: {"type":"text-delta","id":"...","delta":"The top complaint is..."}
data: {"type":"text-end","id":"..."}
data: {"type":"finish"}
data: [DONE]
```

## Austin 311 Dataset

Data comes from the **City of Austin Open Data Portal** via Socrata, stored locally in **DuckDB**.

- **Dataset:** [311 Unified Data](https://data.austintexas.gov/City-Government/311-Unified-Data/i26j-ai4z)
- **API Endpoint:** `https://data.austintexas.gov/resource/xwdj-i9he.csv`
- **Local store:** DuckDB file at `<volume>/311.duckdb`, table `service_requests`, unique index on `sr_number`
- **Size:** ~2.4M rows (2014–present, ~1,500–2,000 new rows daily)
- **No API key required** for reasonable request volumes

### Refresh model

- **Boot seed (`start.sh`):** if the DuckDB is empty, downloads the last 7 days. Boots in seconds.
- **Lifespan async task (`main.py`):** at startup and every 24h, runs two phases:
  1. **Catch up forward** from `MAX(sr_created_date)` — daily delta.
  2. **Extend history backward** from `MIN(sr_created_date)` toward `2014-01-01`.
- All inserts dedupe by `sr_number`. No cron, no separate process.

### Schema

```
sr_number                    # Unique service request ID
sr_type_desc                 # Request type (e.g., "ARR - Garbage")
sr_department_desc           # Responsible city department
sr_method_received_desc      # How reported (Phone, App, Web)
sr_status_desc               # Status (Open, Closed, Duplicate)
sr_created_date              # When the request was filed
sr_closed_date               # When the request was closed
sr_location                  # Full address string
sr_location_zip_code         # ZIP code
sr_location_council_district # City council district (1–10)
sr_location_lat / _long      # Geocoded coordinates
```

### Service Request Categories

| Category | Example Services | Volume |
|----------|-----------------|--------|
| **Code Compliance** | Overgrown vegetation, junk vehicles, illegal dumping | ~25% |
| **Austin Resource Recovery** | Missed collection, recycling, bulk items | ~20% |
| **Transportation & Public Works** | Potholes, street lights, traffic signals | ~15% |
| **Animal Services** | Stray animals, wildlife, barking dogs | ~10% |
| **Austin Water** | Water leaks, pressure issues, billing | ~8% |
| **Other** | Parks, libraries, health, development | ~22% |

### Example API Queries

```bash
# Get recent 311 requests
curl "https://data.austintexas.gov/resource/xwdj-i9he.csv?\$where=sr_created_date>='2026-01-01T00:00:00'&\$limit=50000"

# Filter by department
curl "https://data.austintexas.gov/resource/xwdj-i9he.csv?\$where=sr_department_desc='Austin Resource Recovery'"

# Filter by ZIP code
curl "https://data.austintexas.gov/resource/xwdj-i9he.csv?\$where=sr_location_zip_code='78704'"
```
