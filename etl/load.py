"""LOAD: write the clean tables to the analytics warehouse with SQLAlchemy.

Each table is written in one transaction (replace), so a failed run never leaves a
half-loaded warehouse, and every run is recorded in the etl_runs audit table.
The warehouse is SQLite by default; set WAREHOUSE_DB_URL to use PostgreSQL.
"""
import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, text

import config as C
from etl.common import get_logger, timed
from etl.transform import CLEAN

log = get_logger("load")
TABLES = ["fact_order_lines", "dim_product", "dim_customer", "monthly_sales", "state_summary",
          "holidays", "rejects"]


def main():
    with timed(log, "load warehouse"):
        eng = create_engine(C.WAREHOUSE_DB_URL)
        counts = {}
        with eng.begin() as con:                       # all tables or none
            for t in TABLES:
                df = pd.read_parquet(CLEAN / f"{t}.parquet")
                df.to_sql(t, con, if_exists="replace", index=False, chunksize=1000)
                counts[t] = len(df)
            con.execute(text("""CREATE TABLE IF NOT EXISTS etl_runs (
                run_at TEXT, table_counts TEXT, quality TEXT)"""))
            con.execute(text("INSERT INTO etl_runs VALUES (:a, :b, :c)"),
                        {"a": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "b": json.dumps(counts),
                         "c": (C.RESULTS / "data_quality.json").read_text()})
        with eng.connect() as con:                     # verify what was written
            for t, n in counts.items():
                got = con.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                assert got == n, f"{t}: wrote {n}, found {got}"
            runs = con.execute(text("SELECT COUNT(*) FROM etl_runs")).scalar()
        log.info("warehouse %s: %s (etl_runs=%d)", C.WAREHOUSE_DB_URL, counts, runs)
        return counts


if __name__ == "__main__":
    main()
