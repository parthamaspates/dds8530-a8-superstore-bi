"""Post-run checks: fail loudly (non-zero exit) if the outputs are not fit for the dashboard."""
import json
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import create_engine, text

import config as C
from etl.common import get_logger

log = get_logger("validate")


def main():
    problems = []
    with create_engine(C.WAREHOUSE_DB_URL).connect() as con:
        n_fact = con.execute(text("SELECT COUNT(*) FROM fact_order_lines")).scalar()
        dup = con.execute(text("SELECT COUNT(*) - COUNT(DISTINCT line_id) FROM fact_order_lines")).scalar()
        n_fc = con.execute(text("SELECT COUNT(*) FROM sales_forecast WHERE period LIKE 'Forecast%'")).scalar()
        neg = con.execute(text("SELECT COUNT(*) FROM sales_forecast WHERE predicted < 0")).scalar()
    if n_fact < 9000:
        problems.append(f"fact table too small: {n_fact}")
    if dup:
        problems.append(f"duplicate line_id: {dup}")
    if n_fc != 12 * C.FORECAST_HORIZON:
        problems.append(f"expected {12 * C.FORECAST_HORIZON} forecast rows, found {n_fc}")
    if neg:
        problems.append(f"negative predictions: {neg}")
    man = json.loads((C.TABLEAU_DIR / "manifest.json").read_text())
    age = datetime.now(timezone.utc) - datetime.strptime(man["refreshed_at"], "%Y-%m-%d %H:%M UTC").replace(
        tzinfo=timezone.utc)
    if age > timedelta(hours=1):
        problems.append(f"Tableau files are stale ({age})")
    xl = pd.ExcelFile(C.TABLEAU_DIR / "superstore_bi.xlsx")
    if set(xl.sheet_names) != {"Orders", "Forecast", "States", "Model_Metrics"}:
        problems.append(f"unexpected sheets {xl.sheet_names}")
    status = {"fact_rows": n_fact, "forecast_rows": n_fc, "tableau_refreshed_at": man["refreshed_at"],
              "problems": problems}
    (C.RESULTS / "validation.json").write_text(json.dumps(status, indent=2))
    if problems:
        log.error("VALIDATION FAILED: %s", problems)
        sys.exit(1)
    log.info("validation passed: %s", status)
    return status


if __name__ == "__main__":
    main()
