"""Background refresh of the Austin 311 DuckDB store.

Two-phase model:
  - catch_up_recent(): fetch rows newer than MAX(sr_created_date) and insert.
  - extend_history(): fetch rows older than MIN(sr_created_date) until we hit
    the dataset's floor (2014).

On a brand-new database both phases work the same way as the daily delta —
the watermark just covers more ground. start.sh seeds the DB with the last
week so the API is useful from second one; this module fills in the rest in
the background.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)

DUCKDB_PATH = Path(
    os.environ.get(
        "RAILWAY_VOLUME_MOUNT_PATH",
        str(Path(__file__).resolve().parent.parent / "data"),
    )
) / "311.duckdb"

SOCRATA_CSV = "https://data.austintexas.gov/resource/xwdj-i9he.csv"
EPOCH = "2014-01-01T00:00:00"
PAGE_LIMIT = 100_000
HTTP_TIMEOUT_SECS = 60
HTTP_RETRIES = 3
DEFAULT_INITIAL_WINDOW_DAYS = 7  # matches start.sh seed


def _socrata_url(*, where: str, order: str, limit: int = PAGE_LIMIT) -> str:
    return (
        f"{SOCRATA_CSV}"
        f"?$where={urllib.parse.quote(where)}"
        f"&$order={urllib.parse.quote(order)}"
        f"&$limit={limit}"
    )


def _fetch_csv(url: str) -> str:
    last_exc: Optional[BaseException] = None
    for attempt in range(HTTP_RETRIES):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "agent-austin/1.0"}
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECS) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_exc = exc
            backoff = 2**attempt
            logger.warning(
                "fetch_csv attempt %d failed (%s); sleeping %ds",
                attempt + 1,
                exc,
                backoff,
            )
            time.sleep(backoff)
    assert last_exc is not None
    raise last_exc


def _ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    """Create service_requests from a one-row probe so we don't hard-code the schema."""
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS service_requests AS
        SELECT * FROM read_csv_auto('{SOCRATA_CSV}?$limit=1', all_varchar=true)
        WHERE 1=0
        """
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_number ON service_requests(sr_number)"
    )


def _insert_page_from_csv(con: duckdb.DuckDBPyConnection, body: str) -> tuple[int, int]:
    """Insert a CSV page, deduping by sr_number. Returns (rows_in_page, rows_added)."""
    tmp = Path("/tmp/311_page.csv")
    tmp.write_text(body)
    rows_in_page = con.execute(
        f"SELECT count(*) FROM read_csv_auto('{tmp}', all_varchar=true)"
    ).fetchone()[0]
    if rows_in_page == 0:
        return 0, 0
    before = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
    con.execute(
        f"""
        INSERT INTO service_requests
        SELECT * FROM read_csv_auto('{tmp}', all_varchar=true) src
        WHERE NOT EXISTS (
            SELECT 1 FROM service_requests t WHERE t.sr_number = src.sr_number
        )
        """
    )
    after = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
    return rows_in_page, after - before


def catch_up_recent(
    con: duckdb.DuckDBPyConnection,
    *,
    initial_window_days: int = DEFAULT_INITIAL_WINDOW_DAYS,
) -> int:
    """Pull rows newer than MAX(sr_created_date), forward in time."""
    row = con.execute("SELECT MAX(sr_created_date) FROM service_requests").fetchone()
    if row and row[0]:
        watermark = str(row[0])[:19]
    else:
        watermark = (
            datetime.now(timezone.utc) - timedelta(days=initial_window_days)
        ).strftime("%Y-%m-%dT%H:%M:%S")

    added_total = 0
    while True:
        url = _socrata_url(
            where=f"sr_created_date >= '{watermark}'",
            order="sr_created_date",
        )
        body = _fetch_csv(url)
        rows_in_page, added = _insert_page_from_csv(con, body)
        added_total += added

        new_max = con.execute(
            "SELECT MAX(sr_created_date) FROM service_requests"
        ).fetchone()[0]
        new_watermark = str(new_max)[:19] if new_max else watermark
        if rows_in_page < PAGE_LIMIT or new_watermark == watermark:
            break
        watermark = new_watermark

    return added_total


def extend_history(con: duckdb.DuckDBPyConnection) -> int:
    """Pull older rows until we hit the dataset floor (EPOCH)."""
    row = con.execute("SELECT MIN(sr_created_date) FROM service_requests").fetchone()
    if not (row and row[0]):
        return 0
    floor = str(row[0])[:19]
    if floor <= EPOCH:
        return 0

    added_total = 0
    while floor > EPOCH:
        url = _socrata_url(
            where=f"sr_created_date < '{floor}'",
            order="sr_created_date DESC",
        )
        body = _fetch_csv(url)
        rows_in_page, added = _insert_page_from_csv(con, body)
        added_total += added

        new_min = con.execute(
            "SELECT MIN(sr_created_date) FROM service_requests"
        ).fetchone()[0]
        new_floor = str(new_min)[:19] if new_min else floor
        if rows_in_page < PAGE_LIMIT or new_floor == floor:
            break
        floor = new_floor

    return added_total


def main(*, initial_window_days: int = DEFAULT_INITIAL_WINDOW_DAYS) -> dict:
    """Run a full refresh cycle. Safe to call repeatedly."""
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    con = duckdb.connect(str(DUCKDB_PATH))
    try:
        _ensure_table(con)
        recent_added = catch_up_recent(con, initial_window_days=initial_window_days)
        history_added = extend_history(con)
        total = con.execute("SELECT count(*) FROM service_requests").fetchone()[0]
        date_range = con.execute(
            "SELECT MIN(sr_created_date), MAX(sr_created_date) FROM service_requests"
        ).fetchone()
    finally:
        con.close()
    return {
        "recent_added": recent_added,
        "history_added": history_added,
        "total_rows": total,
        "min_date": str(date_range[0])[:10] if date_range and date_range[0] else None,
        "max_date": str(date_range[1])[:10] if date_range and date_range[1] else None,
        "elapsed_secs": round(time.time() - started, 1),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(main())
