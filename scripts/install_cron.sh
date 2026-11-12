#!/usr/bin/env bash
# Alternative to Airflow: schedule the same pipeline with cron (daily 06:00).
# Usage: bash scripts/install_cron.sh        (remove with: crontab -l | grep -v superstore-bi | crontab -)
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python)"
LINE="0 6 * * * cd \"$DIR\" && \"$PY\" run_pipeline.py >> \"$DIR/logs/cron.log\" 2>&1  # superstore-bi"
( crontab -l 2>/dev/null | grep -v superstore-bi ; echo "$LINE" ) | crontab -
echo "Installed cron job:"; crontab -l | grep superstore-bi
