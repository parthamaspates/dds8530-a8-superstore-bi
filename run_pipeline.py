"""Run the whole pipeline in one process (used by cron and for manual runs).

    python run_pipeline.py            extract -> transform -> load -> forecast -> publish -> validate
"""
import time

from etl import extract, load, publish, transform
from etl.common import get_logger
from ml import forecast
from scripts import validate_run

log = get_logger("run_pipeline")

if __name__ == "__main__":
    t0 = time.perf_counter()
    extract.main("all")
    transform.main()
    load.main()
    forecast.main()
    publish.main()
    validate_run.main()
    log.info("PIPELINE OK in %.1f s", time.perf_counter() - t0)
