"""EXTRACT: pull raw data from the three source types into the staging area.

  csv  - legacy order archive (pandas, read in chunks)
  sql  - operational database (SQLAlchemy): orders joined to customers, plus products
  api  - public REST API: US public holidays per year (Nager.Date, JSON). Responses
         are cached in staging/api so a run still works (with a warning) if the API is
         briefly unavailable.

Usage:  python -m etl.extract [csv|sql|api|all]
"""
import json
import sys
import urllib.request

import pandas as pd
from sqlalchemy import create_engine, text

import config as C
from etl.common import get_logger, save_json, timed

log = get_logger("extract")
API_DIR = C.STAGING / "api"


def extract_csv():
    with timed(log, "extract csv"):
        chunks = pd.read_csv(C.LEGACY_CSV, dtype=str, chunksize=2000)   # stream, never all at once
        parts = []
        for i, ch in enumerate(chunks):
            parts.append(ch)
            log.info("  csv chunk %d: %d rows", i, len(ch))
        df = pd.concat(parts, ignore_index=True)
        df.to_parquet(C.STAGING / "csv_orders.parquet", index=False)
        log.info("csv: %d rows -> staging/csv_orders.parquet", len(df))
        return len(df)


ORDERS_SQL = text("""
    SELECT o.*, c.customer_name, c.segment
    FROM orders o
    LEFT JOIN customers c ON c.customer_id = o.customer_id
""")


def extract_sql():
    with timed(log, "extract sql"):
        eng = create_engine(C.SOURCE_DB_URL)
        with eng.connect() as con:
            orders = pd.read_sql(ORDERS_SQL, con)
            products = pd.read_sql(text("SELECT * FROM products"), con)
            n_orders = con.execute(text("SELECT COUNT(*) FROM orders")).scalar()
        if len(orders) != n_orders:   # the customer join must not change the row count
            raise ValueError(f"join changed row count: {n_orders} -> {len(orders)}")
        orders.to_parquet(C.STAGING / "sql_orders.parquet", index=False)
        products.to_parquet(C.STAGING / "sql_products.parquet", index=False)
        log.info("sql: %d order lines, %d product rows -> staging", len(orders), len(products))
        return len(orders)


def _get_json(url, cache_name):
    """GET a URL; on failure fall back to the last cached response."""
    cache = API_DIR / cache_name
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dds8530-a8-bi-pipeline"})
        with urllib.request.urlopen(req, timeout=C.API_TIMEOUT) as r:
            data = json.loads(r.read().decode())
        save_json(data, cache)
        log.info("  api OK  %s", url)
        return data, "live"
    except Exception as e:  # noqa: BLE001
        if cache.exists():
            log.warning("  api FAILED (%s); using cached %s", e, cache.name)
            return json.loads(cache.read_text()), "cache"
        raise RuntimeError(f"API unavailable and no cache: {url}") from e


def extract_api():
    with timed(log, "extract api"):
        API_DIR.mkdir(parents=True, exist_ok=True)
        rows, status = [], {}
        for y in C.HOLIDAY_YEARS:
            data, st = _get_json(C.HOLIDAY_API.format(year=y), f"holidays_{y}.json")
            status[f"holidays_{y}"] = st
            rows += [{"date": h["date"], "name": h["name"], "global": h.get("global", True)} for h in data]
        hol = pd.DataFrame(rows)
        hol.to_parquet(C.STAGING / "api_holidays.parquet", index=False)

        save_json(status, C.STAGING / "api_status.json")
        log.info("api: %d holidays (%s)", len(hol), status)
        return len(hol)


def main(which="all"):
    if which in ("csv", "all"):
        extract_csv()
    if which in ("sql", "all"):
        extract_sql()
    if which in ("api", "all"):
        extract_api()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
