"""Central settings for the Superstore BI pipeline (DDS-8530 Assignment 8).

Every setting can be overridden with an environment variable, so the same code
runs on a laptop, under cron, or inside the Airflow container.
"""
import os
from pathlib import Path

ROOT = Path(os.environ.get("BI_ROOT", Path(__file__).resolve().parent))

RAW_FILE = ROOT / "data" / "raw" / "superstore.csv"            # original Sample Superstore export
SOURCES = ROOT / "data" / "sources"                             # simulated source systems
LEGACY_CSV = SOURCES / "orders_2014_2016.csv"                   # flat-file archive (CSV source)
SOURCE_DB_URL = os.environ.get("SOURCE_DB_URL", f"sqlite:///{SOURCES / 'operations.db'}")  # SQL source

STAGING = ROOT / "data" / "staging"                             # extract output (Parquet + API JSON)
WAREHOUSE_DB_URL = os.environ.get("WAREHOUSE_DB_URL",
                                  f"sqlite:///{ROOT / 'data' / 'warehouse' / 'superstore_bi.db'}")
TABLEAU_DIR = ROOT / "tableau" / "data"                         # files Tableau connects to
MODELS = ROOT / "models"
RESULTS = ROOT / "results"
LOGS = ROOT / "logs"

# REST API (no key needed)
HOLIDAY_API = "https://date.nager.at/api/v3/PublicHolidays/{year}/US"
HOLIDAY_YEARS = list(range(2014, 2019))      # data years + the forecast year
API_TIMEOUT = 20

FORECAST_HORIZON = 12                        # months ahead (Jan-Dec 2018)
SEED = 42

for p in (SOURCES, STAGING, TABLEAU_DIR, MODELS, RESULTS, LOGS, ROOT / "data" / "warehouse"):
    p.mkdir(parents=True, exist_ok=True)
