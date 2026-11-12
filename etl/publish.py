"""PUBLISH: write the Tableau data sources.

Tableau Public connects to text and Excel files, so the dashboard reads
  tableau/data/superstore_bi.xlsx  (one workbook, one sheet per table) and the same
  tables as CSV files. The predictions are also written back to the warehouse
  (table sales_forecast) so every consumer sees the same numbers.
Tableau's Refresh Data Source (or re-opening the workbook) picks up each new run.
"""
import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine

import config as C
from etl.common import get_logger, save_json, timed
from etl.transform import CLEAN

log = get_logger("publish")


def model_metrics_table():
    m = json.loads((C.RESULTS / "forecast_metrics.json").read_text())
    rows = []
    for name, r in m["candidates"].items():
        rows.append({"model": name, "selected": name == m["selected_model"],
                     "cv_mae": r.get("cv_mae"), "wape_series_2017": r["wape_series"],
                     "wape_company_2017": r["wape_company_monthly"], "predicted_2017_total": r["predicted_total"]})
    return pd.DataFrame(rows)


def main():
    with timed(log, "publish tableau sources"):
        run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        orders = pd.read_parquet(CLEAN / "fact_order_lines.parquet")
        forecast = pd.read_parquet(CLEAN / "forecast.parquet")
        states = pd.read_parquet(CLEAN / "state_summary.parquet")
        metrics = model_metrics_table()
        sheets = {"Orders": orders, "Forecast": forecast, "States": states, "Model_Metrics": metrics}
        for df in sheets.values():
            df["refreshed_at"] = run_at

        with pd.ExcelWriter(C.TABLEAU_DIR / "superstore_bi.xlsx", engine="openpyxl") as xw:
            for name, df in sheets.items():
                df.to_excel(xw, sheet_name=name, index=False)
        for name, df in sheets.items():
            df.to_csv(C.TABLEAU_DIR / f"{name.lower()}.csv", index=False)

        with create_engine(C.WAREHOUSE_DB_URL).begin() as con:
            forecast.to_sql("sales_forecast", con, if_exists="replace", index=False)
            metrics.to_sql("model_metrics", con, if_exists="replace", index=False)

        manifest = {"refreshed_at": run_at, **{k: len(v) for k, v in sheets.items()}}
        save_json(manifest, C.TABLEAU_DIR / "manifest.json")
        log.info("tableau sources written to %s: %s", C.TABLEAU_DIR, manifest)
        return manifest


if __name__ == "__main__":
    main()
