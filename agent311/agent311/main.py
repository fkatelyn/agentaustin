import asyncio
import base64
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent311.auth import (
    create_token,
    get_current_user,
    verify_credentials,
)
from agent311.db import (
    Message,
    Session,
    create_tables,
    get_async_session,
    get_db,
)


_volume_mount = os.environ.get(
    "RAILWAY_VOLUME_MOUNT_PATH",
    str(Path(__file__).resolve().parent.parent / "data"),
)
DUCKDB_PATH = Path(_volume_mount) / "311.duckdb"
REPORTS_DIR = Path(_volume_mount) / "reports"
CHARTS_DIR = Path(_volume_mount) / "analysis" / "charts"
REFRESH_INTERVAL_SECS = int(os.environ.get("REFRESH_INTERVAL_SECS", str(24 * 60 * 60)))
INITIAL_WINDOW_DAYS = int(os.environ.get("INITIAL_WINDOW_DAYS", "7"))
SOCRATA_CSV = "https://datahub.austintexas.gov/resource/xwdj-i9he.csv"
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "10000"))
FETCH_RETRIES = int(os.environ.get("FETCH_RETRIES", "3"))
QUERY_DEFAULT_ROWS = int(os.environ.get("QUERY_DEFAULT_ROWS", "1000"))
QUERY_INLINE_MAX = int(os.environ.get("QUERY_INLINE_MAX", "5000"))
QUERY_HARD_CAP_ROWS = int(os.environ.get("QUERY_HARD_CAP_ROWS", "1000000"))
QUERY_PARQUET_TTL_SECS = int(os.environ.get("QUERY_PARQUET_TTL_SECS", "3600"))
HISTORY_FLOOR = "2014-01-01T00:00:00"


def _fetch_page(where: str, order: str) -> int:
    """Fetch one Socrata page into /tmp/311_page.csv. Returns size in bytes.

    Retries up to FETCH_RETRIES times with exponential backoff (1s, 2s, 4s, ...)
    on any urllib error; re-raises after the last attempt.
    """
    import time
    import urllib.error
    import urllib.parse
    import urllib.request

    url = (
        f"{SOCRATA_CSV}?"
        f"$where={urllib.parse.quote(where)}"
        f"&$order={urllib.parse.quote(order)}"
        f"&$limit={PAGE_SIZE}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "agent-austin/1.0"})
    logger.debug("[refresh][fetch] GET %s", url)
    for attempt in range(FETCH_RETRIES + 1):
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = resp.status
                body = resp.read()
            Path("/tmp/311_page.csv").write_bytes(body)
            elapsed = time.monotonic() - started
            logger.info(
                "[refresh][fetch] %d %d bytes in %.2fs (where=%s, order=%s)",
                status, len(body), elapsed, where, order,
            )
            return len(body)
        except urllib.error.URLError as exc:
            elapsed = time.monotonic() - started
            if attempt == FETCH_RETRIES:
                logger.error(
                    "[refresh][fetch] FAILED after %d attempts in %.2fs: %s (url=%s)",
                    attempt + 1, elapsed, exc, url,
                )
                raise
            delay = 2 ** attempt
            logger.warning(
                "[refresh][fetch] failed (attempt %d/%d, %.2fs): %s — retrying in %ds (where=%s)",
                attempt + 1, FETCH_RETRIES + 1, elapsed, exc, delay, where,
            )
            time.sleep(delay)
    return 0  # unreachable


def _insert_page(con) -> tuple[int, int]:
    """Insert /tmp/311_page.csv, dedup by sr_number. Returns (rows_in_page, rows_added)."""
    page_rows = con.execute(
        "SELECT count(*) FROM read_csv_auto('/tmp/311_page.csv', all_varchar=true)"
    ).fetchone()[0]
    if page_rows == 0:
        return 0, 0
    before = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
    con.execute(
        "INSERT INTO service_requests "
        "SELECT * FROM read_csv_auto('/tmp/311_page.csv', all_varchar=true) src "
        "WHERE NOT EXISTS (SELECT 1 FROM service_requests t WHERE t.sr_number = src.sr_number)"
    )
    after = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
    return page_rows, after - before


def _ensure_table(con) -> None:
    """Create the service_requests table + sr_number index if they don't exist."""
    schema_url = f"{SOCRATA_CSV}?$limit=1"
    logger.info("[refresh][ensure_table] discovering schema from %s", schema_url)
    con.execute(
        f"CREATE TABLE IF NOT EXISTS service_requests AS "
        f"SELECT * FROM read_csv_auto('{schema_url}', all_varchar=true) WHERE 1=0"
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_number ON service_requests(sr_number)"
    )
    cols = con.execute("PRAGMA table_info('service_requests')").fetchall()
    logger.info("[refresh][ensure_table] table ready with %d column(s)", len(cols))


def _do_catchup(con) -> int:
    """Phase 1 — catch up forward from MAX(sr_created_date).

    On an empty DB, falls back to fetching the last INITIAL_WINDOW_DAYS days so
    the table self-bootstraps without help from start.sh.
    """
    import time
    from datetime import datetime, timedelta, timezone

    row = con.execute("SELECT MAX(sr_created_date) FROM service_requests").fetchone()
    if row and row[0]:
        watermark = str(row[0])[:19]
        logger.info("[refresh][catchup] starting from MAX(sr_created_date)=%s", watermark)
    else:
        watermark = (
            datetime.now(timezone.utc) - timedelta(days=INITIAL_WINDOW_DAYS)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        logger.info(
            "[refresh][catchup] empty DB — bootstrapping last %d days from %s",
            INITIAL_WINDOW_DAYS, watermark,
        )

    added = 0
    pages = 0
    started = time.monotonic()
    while True:
        pages += 1
        _fetch_page(f"sr_created_date >= '{watermark}'", "sr_created_date")
        page_rows, page_added = _insert_page(con)
        added += page_added
        if page_rows == 0:
            logger.info("[refresh][catchup] page %d: empty — done", pages)
            break
        new_max = con.execute(
            "SELECT MAX(sr_created_date) FROM service_requests"
        ).fetchone()[0]
        new_watermark = str(new_max)[:19] if new_max else watermark
        logger.info(
            "[refresh][catchup] page %d: %d row(s), %d new, watermark %s -> %s",
            pages, page_rows, page_added, watermark, new_watermark,
        )
        if page_rows < PAGE_SIZE or new_watermark == watermark:
            break
        watermark = new_watermark
    logger.info(
        "[refresh][catchup] done: %d page(s), %d row(s) added in %.1fs",
        pages, added, time.monotonic() - started,
    )
    return added


def _do_history(con) -> int:
    """Phase 2 — extend history backward toward HISTORY_FLOOR. No-op once full."""
    import time

    row = con.execute("SELECT MIN(sr_created_date) FROM service_requests").fetchone()
    if not (row and row[0]):
        logger.info("[refresh][history] skipped — DB is empty (catchup must run first)")
        return 0
    floor = str(row[0])[:19]
    if floor <= HISTORY_FLOOR:
        logger.info("[refresh][history] no-op — already at floor %s", HISTORY_FLOOR)
        return 0
    logger.info(
        "[refresh][history] starting from MIN(sr_created_date)=%s toward %s",
        floor, HISTORY_FLOOR,
    )
    added = 0
    pages = 0
    started = time.monotonic()
    while floor > HISTORY_FLOOR:
        pages += 1
        _fetch_page(f"sr_created_date < '{floor}'", "sr_created_date DESC")
        page_rows, page_added = _insert_page(con)
        added += page_added
        if page_rows == 0:
            logger.info("[refresh][history] page %d: empty — reached source floor", pages)
            break
        new_min = con.execute(
            "SELECT MIN(sr_created_date) FROM service_requests"
        ).fetchone()[0]
        new_floor = str(new_min)[:19] if new_min else floor
        logger.info(
            "[refresh][history] page %d: %d row(s), %d new, floor %s -> %s",
            pages, page_rows, page_added, floor, new_floor,
        )
        if page_rows < PAGE_SIZE or new_floor == floor:
            break
        floor = new_floor
    logger.info(
        "[refresh][history] done: %d page(s), %d row(s) added in %.1fs",
        pages, added, time.monotonic() - started,
    )
    return added


def _stats(con) -> dict:
    total = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
    rng = con.execute(
        "SELECT MIN(sr_created_date), MAX(sr_created_date) FROM service_requests"
    ).fetchone()
    return {
        "total": total,
        "min_date": str(rng[0])[:10] if rng and rng[0] else None,
        "max_date": str(rng[1])[:10] if rng and rng[1] else None,
    }


def _bootstrap_window() -> dict:
    """Run Phase 1 only — blocks the lifespan startup until the DB has data.

    On a fresh container this loads the last INITIAL_WINDOW_DAYS days
    (~5–10 seconds). On a warm container with up-to-date data, it's a cheap
    no-op. Safe to call repeatedly.
    """
    import time

    started = time.time()
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb_connect_writer()
    try:
        _ensure_table(con)
        added = _do_catchup(con)
        stats = _stats(con)
    finally:
        con.close()
    return {"phase": "bootstrap", "added": added, **stats, "elapsed_secs": round(time.time() - started, 1)}


def _refresh_311_data() -> dict:
    """Full refresh — Phase 1 (catch-up) + Phase 2 (history). Called by the background loop."""
    import time

    started = time.time()
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb_connect_writer()
    try:
        _ensure_table(con)
        recent_added = _do_catchup(con)
        history_added = _do_history(con)
        stats = _stats(con)
    finally:
        con.close()
    return {
        "phase": "full",
        "recent_added": recent_added,
        "history_added": history_added,
        **stats,
        "elapsed_secs": round(time.time() - started, 1),
    }


def duckdb_connect_writer():
    """Open the DuckDB file in read-write mode. Only the refresh thread does this."""
    import duckdb

    return duckdb.connect(str(DUCKDB_PATH))


async def _refresh_loop() -> None:
    """Run full refresh immediately (history backfill kicks in here), then once per interval.

    Bootstrap already populated the catchup window synchronously in lifespan;
    the first iteration's catchup is a near no-op, but history backfill runs
    here on day 1 instead of waiting for the next interval.
    """
    while True:
        try:
            result = await asyncio.to_thread(_refresh_311_data)
            logger.info("[refresh] %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[refresh] cycle failed")
        await asyncio.sleep(REFRESH_INTERVAL_SECS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    logger.info("Database tables created")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Reports directory ready: {REPORTS_DIR}")
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Charts directory ready: {CHARTS_DIR}")

    # Synchronous bootstrap: block until the DB has at least the initial window
    # of data. Phase 2 (history backfill) runs in the background after yield.
    try:
        result = await asyncio.to_thread(_bootstrap_window)
        logger.info("[refresh] initial bootstrap: %s", result)
    except Exception:
        logger.exception(
            "[refresh] initial bootstrap failed — continuing; the background "
            "loop will retry on its first cycle"
        )

    refresh_task = asyncio.create_task(_refresh_loop())
    logger.info("[refresh] background task started (interval=%ss)", REFRESH_INTERVAL_SECS)
    try:
        yield
    finally:
        refresh_task.cancel()
        try:
            await refresh_task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_PROMPT = f"""You are Agent 311, an AI assistant specializing in Austin 311 service request data.

You help users explore and analyze Austin's 311 service requests, which include:
- Code Compliance (overgrown vegetation, junk vehicles, illegal dumping)
- Austin Resource Recovery (missed collection, recycling, bulk items)
- Transportation & Public Works (potholes, street lights, traffic signals)
- Animal Services (stray animals, wildlife, barking dogs)
- Austin Water (water leaks, pressure issues, billing)
- Other city services (parks, libraries, health, development)

The 311 dataset contains ~2.4M service requests from 2014-present, available via the City of Austin Open Data Portal (datahub.austintexas.gov). Data is updated in real-time with 1,500-2,000 new requests daily.

## Data Access — DuckDB

All 311 data is stored in a local DuckDB database at: {DUCKDB_PATH}
Table name: `service_requests`

Columns:
- sr_number — unique service request ID
- sr_type_desc — request category (e.g. "ARR - Garbage", "TPW - Pothole Repair")
- sr_department_desc — department (e.g. "Austin Resource Recovery", "Transportation & Public Works")
- sr_method_received_desc — how received (Phone, Web, Mobile App, etc.)
- sr_status_desc — status (Open, Closed, Duplicate, etc.)
- sr_status_date — date of last status change
- sr_created_date — date request was created
- sr_updated_date — date request was last updated
- sr_closed_date — date request was closed
- sr_location — full address
- sr_location_street_number — street number
- sr_location_street_name — street name
- sr_location_city — city
- sr_location_zip_code — ZIP code
- sr_location_county — county
- sr_location_x — X coordinate
- sr_location_y — Y coordinate
- sr_location_lat — latitude
- sr_location_long — longitude
- sr_location_lat_long — combined lat/long
- sr_location_council_district — council district number (1-10)
- sr_location_map_page — map page reference
- sr_location_map_tile — map tile reference

**ALWAYS use the `query_duckdb` MCP tool to query the 311 database.** Do NOT shell out to `duckdb` CLI or write Python that calls `duckdb.connect(...)` — `query_duckdb` runs in-process, avoids file-lock contention with the background refresh, and is much faster.

Usage:
```
query_duckdb(sql="SELECT ... FROM service_requests ...", max_rows=1000)
```

Response (JSON):
- `row_count` — total rows in the result
- `columns` — list of column names
- `rows` — up to `max_rows` of result rows (default 1000, server-capped at 5000)
- `truncated` — true if there are more rows than fit inline; false if `rows` is complete
- `path` — present iff `truncated=true`: parquet file with the FULL result; load via `pd.read_parquet(path)`
- `size_bytes`, `elapsed_ms` — diagnostics
- `error` — if the query failed (DuckDB error, or result > 1M row hard cap)

Decision rule for the agent:
- `truncated=false` → use `rows` directly. Build a DataFrame with `pd.DataFrame(rows, columns=columns)` if needed.
- `truncated=true` → for full-data analysis, `pd.read_parquet(path)`. The inline rows are a preview.

Note: columns are typed as VARCHAR (data was ingested from CSV). Cast in SQL when needed: `sr_created_date::TIMESTAMP`, `sr_location_lat::DOUBLE`, etc.

**Data may still be loading.** The table is bootstrapped to the last {INITIAL_WINDOW_DAYS} days on container startup, then backfilled toward 2014 in the background. For any question that spans more than a week of history, **first check the available range**:
```
query_duckdb("SELECT count(*) AS total, MIN(sr_created_date) AS min_d, MAX(sr_created_date) AS max_d FROM service_requests")
```
If row count is low or `min_d` is recent, tell the user historical backfill is still in progress and answer with the available window.

For data not in the local database, use the Socrata API: https://datahub.austintexas.gov/resource/xwdj-i9he.csv (or .json). Use $where, $limit, $order, $select, $group parameters.

CRITICAL — CHART WORKFLOW (you MUST follow these steps exactly):
1. Use `query_duckdb` to fetch the data you need.
2. Write a Python script to /tmp that uses **pandas + plotly only** (NO `import duckdb`). Build the DataFrame from the `query_duckdb` result — either embed inline `rows` directly, or `pd.read_parquet(path)` if truncated.
3. The script must call `fig.write_html('/tmp/chart_output.html', include_plotlyjs='cdn')`.
4. Run the script with Bash.
5. Read the HTML file content from `/tmp/chart_output.html`.
6. Call `save_chart` with filename and the HTML content — `save_chart` returns the persistent path.
7. Call `view_content` with the EXACT path returned by `save_chart` (NOT /tmp).
NEVER call view_content with a /tmp path. NEVER use Write tool for chart files. ALWAYS use save_chart.
Chart style: template='plotly_dark', paper_bgcolor='#1a1a2e', plot_bgcolor='#16213e'.
Filename convention: <descriptive-name>-chart-<YYYY-MM-DD>.html

REPORTS: Same as charts but use save_report instead. Wrap plotly chart divs in HTML with metric cards, tables, and takeaways. save_report returns the persistent path — pass it to view_content. For PNG export, use fig.write_image() via kaleido.

Be helpful, accurate, and enthusiastic about Austin's civic data!"""

VIEWABLE_EXTENSIONS = {
    ".html": "html",
    ".htm": "html",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".tsx": "tsx",
    ".png": "png",
    ".csv": "csv",
}
VIEWABLE_ROOTS = (Path("/tmp").resolve(), Path(_volume_mount).resolve())
MAX_VIEWABLE_BYTES = 200_000


def _normalize_path(path_value: str) -> str:
    return path_value.strip().strip('"').strip("'")


def _load_viewable_file(path_value: str) -> dict:
    normalized = _normalize_path(path_value)
    if not normalized:
        raise ValueError("`path` is required.")

    try:
        file_path = Path(normalized).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"File not found: {normalized}") from exc

    if not file_path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    ext = file_path.suffix.lower()
    if ext not in VIEWABLE_EXTENSIONS:
        allowed = ", ".join(sorted(VIEWABLE_EXTENSIONS))
        raise ValueError(f"Unsupported file type for preview. Allowed: {allowed}")

    if not any(file_path.is_relative_to(root) for root in VIEWABLE_ROOTS):
        allowed_roots = ", ".join(str(root) for root in VIEWABLE_ROOTS)
        raise PermissionError(
            f"Path is outside allowed roots ({allowed_roots}): {file_path}"
        )

    size_bytes = file_path.stat().st_size
    if size_bytes > MAX_VIEWABLE_BYTES:
        raise ValueError(
            f"File is too large for preview ({size_bytes} bytes > {MAX_VIEWABLE_BYTES})."
        )

    result = {
        "path": str(file_path),
        "language": VIEWABLE_EXTENSIONS[ext],
        "sizeBytes": size_bytes,
    }

    if ext == ".png":
        result["content"] = base64.b64encode(file_path.read_bytes()).decode("ascii")
        result["encoding"] = "base64"
    else:
        result["content"] = file_path.read_text(encoding="utf-8", errors="replace")

    return result


@tool(
    "view_content",
    "Prepare local HTML/JS content for host-side artifact preview.",
    {"path": str},
)
async def view_content(args: dict):
    path_value = str(args.get("path", "")).strip()
    try:
        file_info = _load_viewable_file(path_value)
        text = (
            f"Prepared artifact preview content for {file_info['path']} "
            f"({file_info['language']}, {file_info['sizeBytes']} bytes)."
        )
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        text = f"Unable to prepare preview: {exc}"

    return {"content": [{"type": "text", "text": text}]}


ALLOWED_REPORT_EXTENSIONS = {".html", ".png", ".csv", ".pdf"}
ALLOWED_UPLOAD_EXTENSIONS = {".html", ".pdf"}


@tool(
    "save_report",
    "Save an agent-generated report (HTML, PNG, or CSV) to the reports directory for user access.",
    {"filename": str, "content": str, "encoding": str},
)
async def save_report(args: dict):
    filename = str(args.get("filename", "")).strip()
    content = str(args.get("content", ""))
    encoding = str(args.get("encoding", "text")).strip().lower()

    if not filename:
        return {"content": [{"type": "text", "text": "Error: filename is required."}]}

    # Sanitize: no path traversal
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename or ".." in filename or "/" in filename:
        return {"content": [{"type": "text", "text": f"Error: invalid filename '{filename}'. Use a simple name like 'report.html'."}]}

    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_REPORT_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_REPORT_EXTENSIONS))
        return {"content": [{"type": "text", "text": f"Error: unsupported extension '{ext}'. Allowed: {allowed}"}]}

    file_path = REPORTS_DIR / safe_name

    if encoding == "base64":
        data = base64.b64decode(content)
        file_path.write_bytes(data)
    else:
        file_path.write_text(content, encoding="utf-8")

    size = file_path.stat().st_size
    return {"content": [{"type": "text", "text": f"Report saved ({size} bytes). Pass this path to view_content: {file_path}"}]}


ALLOWED_CHART_EXTENSIONS = {".html", ".png"}


@tool(
    "save_chart",
    "Save an agent-generated chart or visualization (HTML or PNG) to the persistent charts directory. Returns the saved file path for use with view_content.",
    {"filename": str, "content": str, "encoding": str},
)
async def save_chart(args: dict):
    filename = str(args.get("filename", "")).strip()
    content = str(args.get("content", ""))
    encoding = str(args.get("encoding", "text")).strip().lower()

    if not filename:
        return {"content": [{"type": "text", "text": "Error: filename is required."}]}

    safe_name = Path(filename).name
    if not safe_name or safe_name != filename or ".." in filename or "/" in filename:
        return {"content": [{"type": "text", "text": f"Error: invalid filename '{filename}'. Use a simple name like 'chart.html'."}]}

    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_CHART_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_CHART_EXTENSIONS))
        return {"content": [{"type": "text", "text": f"Error: unsupported extension '{ext}'. Allowed: {allowed}"}]}

    file_path = CHARTS_DIR / safe_name

    if encoding == "base64":
        data = base64.b64decode(content)
        file_path.write_bytes(data)
    else:
        file_path.write_text(content, encoding="utf-8")

    size = file_path.stat().st_size
    return {"content": [{"type": "text", "text": f"Chart saved ({size} bytes). Pass this path to view_content: {file_path}"}]}


def _query_json_default(v):
    import decimal
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return str(v)


def _cleanup_old_parquets() -> None:
    """Delete /tmp/q_*.parquet older than QUERY_PARQUET_TTL_SECS. Best-effort."""
    import time
    cutoff = time.time() - QUERY_PARQUET_TTL_SECS
    for p in Path("/tmp").glob("q_*.parquet"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


@tool(
    "query_duckdb",
    (
        "Run a read-only SQL query against the 311 DuckDB and return rows as JSON. "
        "Returns up to max_rows inline (default 1000, server-capped at 5000). "
        "If the result has more rows, the response also includes a parquet file path "
        "containing the full result — load it with pd.read_parquet(path). "
        "Errors out for results larger than 1M rows; refine your query in that case."
    ),
    {"sql": str, "max_rows": int},
)
async def query_duckdb(args: dict):
    import time
    import duckdb

    sql = str(args.get("sql", "")).strip()
    if not sql:
        return {"content": [{"type": "text", "text": json.dumps({"error": "sql is required"})}]}
    if sql.endswith(";"):
        sql = sql.rstrip(";").rstrip()

    raw_max = args.get("max_rows")
    max_rows = QUERY_DEFAULT_ROWS if raw_max is None else int(raw_max)
    max_rows = max(1, min(max_rows, QUERY_INLINE_MAX))

    _cleanup_old_parquets()
    started = time.monotonic()

    def _run() -> dict:
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        try:
            columns = con.execute(
                f"SELECT * FROM ({sql}) LIMIT 0"
            ).description
            colnames = [c[0] for c in columns] if columns else []

            probe = con.execute(
                f"SELECT * FROM ({sql}) LIMIT {max_rows + 1}"
            ).fetchall()
            truncated = len(probe) > max_rows
            inline_rows = [list(r) for r in probe[:max_rows]]

            if not truncated:
                return {
                    "row_count": len(inline_rows),
                    "columns": colnames,
                    "rows": inline_rows,
                    "truncated": False,
                }

            row_count = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
            if row_count > QUERY_HARD_CAP_ROWS:
                return {
                    "error": (
                        f"result too large ({row_count:,} rows > {QUERY_HARD_CAP_ROWS:,} cap); "
                        f"add LIMIT, GROUP BY, or a WHERE filter"
                    ),
                    "row_count": row_count,
                }

            path = Path("/tmp") / f"q_{uuid.uuid4().hex[:8]}.parquet"
            con.execute(
                f"COPY ({sql}) TO '{path}' (FORMAT PARQUET, COMPRESSION SNAPPY)"
            )
            return {
                "row_count": row_count,
                "columns": colnames,
                "rows": inline_rows,
                "truncated": True,
                "path": str(path),
                "size_bytes": path.stat().st_size,
            }
        finally:
            con.close()

    try:
        result = await asyncio.to_thread(_run)
    except duckdb.Error as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        logger.exception("[query_duckdb] unexpected failure")
        result = {"error": f"{type(exc).__name__}: {exc}"}

    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    logger.info(
        "[query_duckdb] %s elapsed=%dms %s",
        "ERROR" if "error" in result else (f"row_count={result.get('row_count')}"),
        result["elapsed_ms"],
        ("truncated" if result.get("truncated") else "inline") if "error" not in result else "",
    )

    payload = json.dumps(result, default=_query_json_default, ensure_ascii=False)
    return {"content": [{"type": "text", "text": payload}]}


agent311_host_tools = create_sdk_mcp_server(
    name="agent311_host",
    tools=[view_content, save_report, save_chart, query_duckdb],
)


def _extract_text(msg: dict) -> str:
    """Extract text from a message, supporting both formats:
    - Old format: {"role": "user", "content": "hello"}
    - AI SDK v6:  {"role": "user", "parts": [{"type": "text", "text": "hello"}]}
    """
    content = msg.get("content")
    if isinstance(content, str) and content:
        return content

    parts = msg.get("parts", [])
    if parts:
        texts = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts)

    return ""


async def _stream_chat(messages: list, session_id: str | None, user_msg_id: str | None, assistant_msg_id: str | None):
    """Stream chat responses using Claude Agent SDK. Persists messages to DB."""
    msg_id = str(uuid.uuid4())

    # Extract the last user message as the prompt
    prompt = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            prompt = _extract_text(msg)
            break

    # Build conversation context from earlier messages, skipping error placeholders
    context = ""
    for msg in messages[:-1]:
        role = msg.get("role", "")
        content = _extract_text(msg)
        if role and content and not content.startswith("Error:"):
            context += f"<{role}>\n{content}\n</{role}>\n\n"

    system_prompt = SYSTEM_PROMPT
    if context:
        system_prompt += f"\n\nConversation history:\n{context}"
    logger.info(f"=== prompt: {prompt[:200]} ===")
    logger.info(f"=== context length: {len(context)} chars, messages[:-1] count: {len(messages[:-1])} ===")

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=str(Path(__file__).parent),
        setting_sources=["project"],
        mcp_servers={"agent311_host": agent311_host_tools},
        allowed_tools=[
            "Skill",
            "Read",
            "Write",
            "Edit",
            "Bash",
            "Task",
            "WebSearch",
            "WebFetch",
            "mcp__agent311_host__view_content",
            "mcp__agent311_host__save_report",
            "mcp__agent311_host__save_chart",
            "mcp__agent311_host__query_duckdb",
        ],
        permission_mode="acceptEdits",
        max_turns=60,
        stderr=lambda line: logger.warning(f"[claude-cli stderr] {line}"),
    )

    # SSE stream
    yield f"data: {json.dumps({'type': 'start', 'messageId': msg_id})}\n\n"
    yield f"data: {json.dumps({'type': 'text-start', 'id': msg_id})}\n\n"

    # Queue for events produced by the agent coroutine
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _run_agent():
        try:
            logger.info("[agent] creating ClaudeSDKClient...")
            async with ClaudeSDKClient(options=options) as client:
                logger.info("[agent] sending query...")
                await client.query(prompt)
                logger.info("[agent] waiting for response...")
                msg_count = 0
                async for message in client.receive_response():
                    msg_count += 1
                    logger.info(f"[agent] message #{msg_count}: type={type(message).__name__} isinstance_assistant={isinstance(message, AssistantMessage)}")
                    if hasattr(message, 'content'):
                        logger.info(f"[agent] message #{msg_count} content types: {[type(b).__name__ for b in message.content]}")
                    else:
                        logger.info(f"[agent] message #{msg_count} attrs: {list(vars(message).keys()) if hasattr(message, '__dict__') else repr(message)[:200]}")
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                await queue.put(f"data: {json.dumps({'type': 'text-delta', 'id': msg_id, 'delta': block.text})}\n\n")
                            elif isinstance(block, ToolUseBlock):
                                if block.name == "mcp__agent311_host__view_content":
                                    block_input = block.input if isinstance(block.input, dict) else {}
                                    path_value = block_input.get("path")
                                    if isinstance(path_value, str) and path_value.strip():
                                        marker = f"[Using tool: view_content {path_value}]\\n"
                                        await queue.put(f"data: {json.dumps({'type': 'text-delta', 'id': msg_id, 'delta': marker})}\n\n")
                                        continue
                                if block.name == "mcp__agent311_host__save_report":
                                    block_input = block.input if isinstance(block.input, dict) else {}
                                    fname = block_input.get("filename", "")
                                    if isinstance(fname, str) and fname.strip():
                                        marker = f"[Using tool: save_report {fname}]\\n"
                                        await queue.put(f"data: {json.dumps({'type': 'text-delta', 'id': msg_id, 'delta': marker})}\n\n")
                                        continue
                                if block.name == "mcp__agent311_host__query_duckdb":
                                    block_input = block.input if isinstance(block.input, dict) else {}
                                    sql_value = block_input.get("sql", "")
                                    if isinstance(sql_value, str) and sql_value.strip():
                                        snippet = " ".join(sql_value.split())
                                        if len(snippet) > 120:
                                            snippet = snippet[:117] + "..."
                                        marker = f"[Using tool: query_duckdb `{snippet}`]\\n"
                                        await queue.put(f"data: {json.dumps({'type': 'text-delta', 'id': msg_id, 'delta': marker})}\n\n")
                                        continue
                                tool_marker = f"[Using tool: {block.name}]\\n"
                                await queue.put(f"data: {json.dumps({'type': 'text-delta', 'id': msg_id, 'delta': tool_marker})}\n\n")
                logger.info(f"[agent] loop finished. total messages: {msg_count}")
        except Exception as e:
            logger.error(f"[agent] exception: {type(e).__name__}: {e}")
            error_text = f"Error: {str(e)}"
            await queue.put(f"data: {json.dumps({'type': 'text-delta', 'id': msg_id, 'delta': error_text})}\n\n")
        finally:
            logger.info("[agent] done")
            await queue.put(None)  # sentinel: agent done

    agent_task = asyncio.create_task(_run_agent())

    full_text = ""
    KEEPALIVE_INTERVAL = 20  # seconds

    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL)
        except asyncio.TimeoutError:
            # Send SSE comment to keep the connection alive through Railway's idle timeout
            yield ": keepalive\n\n"
            continue

        if item is None:
            break  # agent finished

        # Accumulate full_text from text-delta events
        try:
            parsed = json.loads(item[6:])  # strip "data: "
            if parsed.get("type") == "text-delta":
                full_text += parsed.get("delta", "")
        except Exception:
            pass

        yield item

    await agent_task  # ensure any exceptions are propagated

    yield f"data: {json.dumps({'type': 'text-end', 'id': msg_id})}\n\n"
    yield f"data: {json.dumps({'type': 'finish'})}\n\n"
    yield "data: [DONE]\n\n"

    # Persist assistant message to DB after streaming completes
    if session_id and full_text:
        try:
            async with get_async_session()() as db:
                db_msg = Message(
                    id=assistant_msg_id or str(uuid.uuid4()),
                    session_id=session_id,
                    role="assistant",
                    content=full_text,
                )
                db.add(db_msg)
                # Update session timestamp
                result = await db.execute(select(Session).where(Session.id == session_id))
                sess = result.scalar_one_or_none()
                if sess:
                    sess.updated_at = datetime.now(timezone.utc)
                await db.commit()
        except Exception:
            logger.exception("Failed to save assistant message to DB")


# ─── Auth endpoint ───────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
async def login(body: LoginRequest):
    if not verify_credentials(body.email, body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(body.email)
    return {"token": token}


# ─── Data status endpoint ───────────────────────────────────────────────────


@app.get("/api/data/status")
async def data_status(user: str = Depends(get_current_user)):
    """Row count + date range for the 311 DuckDB. Lets the UI show loading state."""
    import duckdb

    def _stat() -> dict:
        if not DUCKDB_PATH.exists():
            return {"rows": 0, "min_date": None, "max_date": None, "ready": False}
        try:
            con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        except Exception:
            return {"rows": 0, "min_date": None, "max_date": None, "ready": False}
        try:
            row = con.execute(
                "SELECT count(*), MIN(sr_created_date), MAX(sr_created_date) "
                "FROM service_requests"
            ).fetchone()
            return {
                "rows": row[0],
                "min_date": str(row[1])[:10] if row[1] else None,
                "max_date": str(row[2])[:10] if row[2] else None,
                "ready": row[0] > 0,
            }
        except duckdb.CatalogException:
            return {"rows": 0, "min_date": None, "max_date": None, "ready": False}
        finally:
            con.close()

    return await asyncio.to_thread(_stat)


# ─── Session CRUD endpoints ─────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    id: str
    title: str = "New Chat"


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    is_favorite: bool | None = None


@app.get("/api/sessions")
async def list_sessions(
    user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session).order_by(Session.is_favorite.desc(), Session.updated_at.desc())
    )
    sessions = result.scalars().all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "createdAt": s.created_at.isoformat() if s.created_at else None,
            "updatedAt": s.updated_at.isoformat() if s.updated_at else None,
            "isFavorite": s.is_favorite,
        }
        for s in sessions
    ]


@app.get("/api/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session)
        .options(selectinload(Session.messages))
        .where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": session.id,
        "title": session.title,
        "createdAt": session.created_at.isoformat() if session.created_at else None,
        "updatedAt": session.updated_at.isoformat() if session.updated_at else None,
        "isFavorite": session.is_favorite,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "createdAt": m.created_at.isoformat() if m.created_at else None,
            }
            for m in session.messages
        ],
    }


@app.post("/api/sessions")
async def create_session_endpoint(
    body: CreateSessionRequest,
    user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = Session(id=body.id, title=body.title)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {
        "id": session.id,
        "title": session.title,
        "createdAt": session.created_at.isoformat() if session.created_at else None,
        "updatedAt": session.updated_at.isoformat() if session.updated_at else None,
        "isFavorite": session.is_favorite,
    }


@app.patch("/api/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if body.title is not None:
        session.title = body.title
    if body.is_favorite is not None:
        session.is_favorite = body.is_favorite
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@app.patch("/api/messages/{message_id}")
async def update_message(
    message_id: str,
    body: dict,
    user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if "content" in body:
        message.content = body["content"]
    await db.commit()
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await db.delete(session)
    await db.commit()
    return {"ok": True}


# ─── Reports endpoint ───────────────────────────────────────────────────────


@app.get("/api/reports")
async def list_reports(user: str = Depends(get_current_user)):
    if not REPORTS_DIR.exists():
        return {"files": []}

    files = []
    for f in REPORTS_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in ALLOWED_REPORT_EXTENSIONS:
            stat = f.stat()
            files.append({
                "name": f.name,
                "path": str(f),
                "type": f.suffix.lstrip(".").lower(),
                "sizeBytes": stat.st_size,
                "modifiedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

    files.sort(key=lambda x: x["modifiedAt"], reverse=True)
    return {"files": files}


DOWNLOAD_MEDIA_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
}


@app.get("/api/reports/download")
async def download_report(
    path: str = Query(..., description="Absolute path to report file"),
    inline: bool = Query(False, description="Serve inline instead of as attachment"),
    user: str = Depends(get_current_user),
):
    try:
        file_path = Path(path).resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Not a file")

    if not file_path.is_relative_to(REPORTS_DIR.resolve()):
        raise HTTPException(status_code=403, detail="File is outside reports directory")

    ext = file_path.suffix.lower()
    if ext not in ALLOWED_REPORT_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    media_type = DOWNLOAD_MEDIA_TYPES.get(ext, "application/octet-stream")
    if inline:
        content = file_path.read_bytes()
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{file_path.name}"'},
        )
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_path.name,
    )


@app.patch("/api/reports/{filename}")
async def rename_report(
    filename: str,
    body: dict,
    user: str = Depends(get_current_user),
):
    new_name = body.get("name", "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name is required")

    # Validate old filename
    safe_old = Path(filename).name
    if not safe_old or safe_old != filename or ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")

    # Validate new filename
    safe_new = Path(new_name).name
    if not safe_new or safe_new != new_name or ".." in new_name or "/" in new_name:
        raise HTTPException(status_code=400, detail=f"Invalid new filename: {new_name}")

    # Must keep the same extension
    old_ext = Path(safe_old).suffix.lower()
    new_ext = Path(safe_new).suffix.lower()
    if old_ext != new_ext:
        raise HTTPException(status_code=400, detail=f"Extension must remain {old_ext}")

    old_path = (REPORTS_DIR / safe_old).resolve()
    if not old_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not old_path.is_relative_to(REPORTS_DIR.resolve()):
        raise HTTPException(status_code=403, detail="File is outside reports directory")

    new_path = (REPORTS_DIR / safe_new).resolve()
    if not new_path.is_relative_to(REPORTS_DIR.resolve()):
        raise HTTPException(status_code=403, detail="New path is outside reports directory")
    if new_path.exists():
        raise HTTPException(status_code=409, detail=f"File already exists: {new_name}")

    old_path.rename(new_path)
    return {"ok": True, "name": safe_new}


@app.delete("/api/reports/{filename}")
async def delete_report(
    filename: str,
    user: str = Depends(get_current_user),
):
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename or ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")

    file_path = (REPORTS_DIR / safe_name).resolve()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not file_path.is_relative_to(REPORTS_DIR.resolve()):
        raise HTTPException(status_code=403, detail="File is outside reports directory")

    file_path.unlink()
    return {"ok": True}


@app.post("/api/reports/upload")
async def upload_report(
    file: UploadFile = File(...),
    user: str = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    safe_name = Path(file.filename).name
    if not safe_name or safe_name != file.filename or ".." in file.filename or "/" in file.filename:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {file.filename}")

    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'. Allowed: {allowed}")

    file_path = REPORTS_DIR / safe_name
    content = await file.read()
    file_path.write_bytes(content)

    stat = file_path.stat()
    return {
        "name": safe_name,
        "path": str(file_path),
        "type": ext.lstrip("."),
        "sizeBytes": stat.st_size,
        "modifiedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


# ─── Existing endpoints ─────────────────────────────────────────────────────


@app.get("/")
async def hello():
    return {"message": "Hello, World!"}


@app.get("/api/fetch_file")
async def fetch_file(path: str = Query(..., description="Absolute path to a previewable file")):
    try:
        return _load_viewable_file(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/chat")
async def chat(
    request: Request,
    user: str = Depends(get_current_user),
):
    body = await request.json()
    messages = body.get("messages", [])
    session_id = body.get("session_id")
    user_msg_id = body.get("user_msg_id")
    assistant_msg_id = body.get("assistant_msg_id")

    logger.info(f"=== /api/chat received {len(messages)} messages, session_id={session_id} ===")
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        text = _extract_text(msg)
        logger.info(f"  msg[{i}] role={role} text={text[:200]}")

    # Persist user message and auto-create session if needed
    if session_id:
        try:
            async with get_async_session()() as db:
                # Auto-create session if it doesn't exist
                result = await db.execute(select(Session).where(Session.id == session_id))
                sess = result.scalar_one_or_none()
                if not sess:
                    sess = Session(id=session_id, title="New Chat")
                    db.add(sess)

                # Save user message
                if user_msg_id and messages:
                    last_user = None
                    for msg in reversed(messages):
                        if msg.get("role") == "user":
                            last_user = msg
                            break
                    if last_user:
                        db_msg = Message(
                            id=user_msg_id,
                            session_id=session_id,
                            role="user",
                            content=_extract_text(last_user),
                        )
                        db.add(db_msg)

                await db.commit()
        except Exception:
            logger.exception("Failed to save user message to DB")

    return StreamingResponse(
        _stream_chat(messages, session_id, user_msg_id, assistant_msg_id),
        media_type="text/event-stream",
        headers={
            "x-vercel-ai-ui-message-stream": "v1",
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
