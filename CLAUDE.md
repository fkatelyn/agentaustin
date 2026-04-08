# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Austin is an AI-powered data science agent for Austin 311 service request data — a full-stack application with a FastAPI backend and Next.js frontend.

## Tech Stack

- **Backend:** FastAPI + Claude Agent SDK (`claude-agent-sdk`)
- **Frontend:** shadcn + AI Elements
- **Database:** PostgreSQL
- **Data Analysis:** pandas + plotly (charts), kaleido (PNG/PDF export)
- **Package Manager:** uv (Python), npm (JavaScript)
- **Deployment:** Railway (Railpack builder, persistent volume for data)

## Architecture

### Backend (`agent311/`)
- Self-contained backend directory with its own `pyproject.toml`, `uv.lock`, `railpack.json`, `railway.json`, and `start.sh`
- Python package: `agent311/agent311/` — contains `main.py`, `auth.py`, `db.py`
- Entry point: `agent311/agent311/main.py` — FastAPI app with CORS-enabled streaming chat endpoint
- Chat endpoint: `POST /api/chat` — accepts messages array + optional session_id, returns SSE stream in Vercel AI SDK v6 protocol format
- Must be imported as `agent311.main:app` (package-qualified import)

### Frontend (`agentui/`)
- Next.js 16 with App Router, Tailwind CSS 4, shadcn/ui
- Uses AI Elements components for chat UI primitives (Message, PromptInput, Artifact, CodeBlock, JSXPreview)
- Streamdown for markdown + syntax highlighting + mermaid + math rendering
- Custom SSE parsing via raw `fetch` + `ReadableStream` (does NOT use AI SDK `useChat` hook)
- JWT auth stored in localStorage; auto-redirects to `/login` on 401
- Sessions and messages persisted to backend PostgreSQL (not localStorage)
- Backend URL configured via `NEXT_PUBLIC_API_URL` env var

### Deployment (Railway)
- Two separate Railway services: one for backend (`agent311/`), one for frontend (`agentui/`)
- Backend Root Directory set to `/agent311` in Railway dashboard
- Backend uses `agent311/railpack.json`; uv auto-detected via mise from `pyproject.toml` + `uv.lock`
- Frontend uses `agentui/railpack.json`; Node.js and Next.js auto-detected; `NEXT_PUBLIC_API_URL` set in Railway dashboard
- Railway auto-detects uv via `agent311/pyproject.toml` + `agent311/uv.lock`
- Start command: `bash start.sh` (downloads year-to-date 311 data, starts uvicorn)
- Persistent volume (`RAILWAY_VOLUME_MOUNT_PATH`) stores CSV data, reports, and charts across deploys

## Development Commands

### Backend

```bash
cd agent311

# Run FastAPI dev server
uv run uvicorn agent311.main:app --host 0.0.0.0 --port 8000

# Or with auto-reload
uv run uvicorn agent311.main:app --reload --host 0.0.0.0 --port 8000

# Add dependencies
uv add <package>

# Sync dependencies
uv sync
```

### Frontend

```bash
cd agentui

# Install dependencies
npm install

# Run dev server
npm run dev

# Build for production
npm run build

# Preview production build
npm run start
```

## Git Workflow

- **Commit directly to `main`** — do not create pull requests
- **Commits use a single-line title only** — no message body, no multi-line descriptions
- **Commit titles** must be short, clear, and easy to understand at a glance
  - Good: "Fix UUID crash on mobile Safari"
  - Bad: "feat: implement crypto.randomUUID fallback for non-secure HTTP contexts on mobile Safari browsers"
- **Do not add** "Co-Authored-By" or other metadata that makes commits look automated
- **Do not `git push`** unless the user explicitly asks to push or deploy to Railway. Commits are local-only by default.

## Key Files

- `agent311/pyproject.toml` + `agent311/uv.lock` — Python dependencies
- `agent311/railpack.json` — Backend Railway build config (system packages, Claude Code CLI install step)
- `agent311/railway.json` — Specifies Railpack builder
- `agent311/start.sh` — Startup script (downloads/updates 311 data via `download_311.py`, starts uvicorn)
- `agent311/agent311/download_311.py` — 311 data downloader with pagination and delta merge (dedup by `sr_number`)
- `agent311/.python-version` — Pins Python 3.12
- `agent311/agent311/main.py` — FastAPI app, all endpoints, SSE streaming, MCP tools
- `agent311/agent311/db.py` — SQLAlchemy async ORM, PostgreSQL config, Session/Message models
- `agent311/agent311/auth.py` — JWT auth (create_token, get_current_user)
- `agentui/railpack.json` — Frontend Railway build config (start command)
- `agentui/components/chat.tsx` — Main chat orchestrator (SSE, state, layout, view_content fetch)
- `agentui/components/chat-messages.tsx` — Message rendering, tool summary, artifact cards
- `agentui/components/sidebar.tsx` — Session list, favorites, delete with confirmation
- `agentui/components/artifact-panel.tsx` — Preview panel (iframe for HTML, JSXPreview for JSX)
- `agentui/lib/session-api.ts` — Backend session CRUD API calls
- `agentui/lib/auth.ts` — JWT login, token storage, authFetch wrapper

## Data & Persistent Storage

- **Volume mount:** `RAILWAY_VOLUME_MOUNT_PATH` on Railway; falls back to `data/` relative to `agent311/` locally
- **311 CSV:** `<volume>/311_recent.csv` — from Jan 1 of last year, downloaded/updated incrementally by `start.sh` on startup
- **Reports:** `<volume>/reports/` — user-curated HTML/CSV/PNG reports, shown in sidebar file tree
- **Charts:** `<volume>/analysis/charts/` — agent-generated plotly charts, previewed in artifact panel
- The `data/` directory is gitignored — generated data should never be committed
- Data source: City of Austin Open Data (Socrata API), dataset ID `xwdj-i9he`

## Environment Variables

This project uses a `~/.env` file (not checked into git) to store local credentials:

**`~/.env` file contains:**
- `ANTHROPIC_API_KEY` - For Claude Agent SDK integration
  - Get from: https://console.anthropic.com/settings/keys

**Backend env vars (set in Railway dashboard):**
- `DATABASE_URL` — PostgreSQL connection string (provided automatically by Railway Postgres plugin)
- `JWT_SECRET` — Secret key for JWT signing
- `ANTHROPIC_API_KEY` — Claude API key
- `RAILWAY_VOLUME_MOUNT_PATH` — Persistent volume path (provided automatically by Railway volume plugin)

**Frontend env var:**
- `NEXT_PUBLIC_API_URL` — Backend API URL (set in Railway dashboard; get URL via `railway domain` in the backend service dir)

**Railway CLI Note:** Use the Railway CLI (installed via `brew install railway`) for deployments. Authenticate with `railway login`.

The `~/.env` file should never be committed to git.

## Important Notes

- Backend must be run using `python -m uvicorn` (not bare `uvicorn`) to avoid PATH issues on Railway
- Frontend uses custom SSE parsing, not AI SDK `useChat` hook
- Backend streams messages using Vercel AI SDK v6 SSE protocol (`start`, `text-start`, `text-delta`, `text-end`, `finish`, `[DONE]`)
- Tool invocations are emitted as `text-delta` markers: `[Using tool: Read]`, `[Using tool: view_content /path/to/file.html]`
- Custom MCP tools: `view_content` (preview files), `save_chart` (persist charts), `save_report` (persist reports)
- `save_chart` saves to `<volume>/analysis/charts/`, `save_report` saves to `<volume>/reports/` — both return the saved path for `view_content`
- File preview via `view_content` is restricted to `/tmp/` and the volume mount, max 200KB, allowed extensions: `.html`, `.js`, `.jsx`, `.tsx`, `.png`, `.csv`
- CORS is wide-open (`*`) for development; should be restricted in production
- Railway backend service Root Directory must be set to `/agent311`
- Railway containers run as root — use `permission_mode="acceptEdits"` not `"bypassPermissions"`
- Hardcoded auth credentials: `default@agentaustin.org` / `password` (single-user app)
- DB tables (`sessions`, `messages`) are created automatically on startup; `is_favorite` column added via migration guard if missing
