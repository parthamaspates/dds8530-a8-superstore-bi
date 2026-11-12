"""Airflow DAG: refresh the Superstore BI data and forecast every day at 06:00.

  extract_csv ┐
  extract_sql ┼─> transform ─> load_warehouse ─> train_forecast ─> publish_tableau ─> validate
  extract_api ┘
Each task runs one module of the project with BashOperator in the project's own
virtual environment, so the DAG uses exactly the same code as a manual run. Failed tasks retry twice, five minutes apart.
"""
import os
from datetime import datetime, timedelta

from airflow import DAG

try:                                    # Airflow 3 location
    from airflow.providers.standard.operators.bash import BashOperator
except ImportError:                     # Airflow 2
    from airflow.operators.bash import BashOperator

PROJECT = os.environ.get("BI_ROOT", "/opt/project")
PYTHON = os.environ.get("BI_PYTHON", "python")   # project venv inside the container


def step(task_id, module):
    return BashOperator(task_id=task_id, bash_command=f"cd {PROJECT} && {PYTHON} -m {module}",
                        env={"BI_ROOT": PROJECT, "PYTHONPATH": PROJECT}, append_env=True)


with DAG(
    dag_id="superstore_bi_pipeline",
    description="ETL (CSV + SQL + APIs) -> warehouse -> sales forecast -> Tableau sources",
    schedule="0 6 * * *",
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "dds8530", "retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["dds8530", "tableau", "etl", "ml"],
) as dag:
    extracts = [step("extract_csv", "etl.extract csv"), step("extract_sql", "etl.extract sql"),
                step("extract_api", "etl.extract api")]
    extracts >> step("transform", "etl.transform") >> step("load_warehouse", "etl.load") \
        >> step("train_forecast", "ml.forecast") >> step("publish_tableau", "etl.publish") \
        >> step("validate", "scripts.validate_run")
