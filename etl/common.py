"""Logging and run-log helpers shared by every pipeline step."""
import json
import logging
import time
from contextlib import contextmanager

import config as C

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(C.LOGS / "pipeline.log")],
)


def get_logger(name):
    return logging.getLogger(name)


@contextmanager
def timed(log, step):
    t0 = time.perf_counter()
    log.info("START %s", step)
    yield
    log.info("END %s (%.2f s)", step, time.perf_counter() - t0)


def save_json(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))
