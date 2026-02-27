#!/usr/bin/env bash
set -uo pipefail

# Smoke test for migrated core tasks.
# FAST=1 mode only runs capture-analyze + summary-daily.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

TASK_FILES=(
  "tasks/capture_analyze.yaml"
  "tasks/summary_daily.yaml"
  "tasks/summary_weekly.yaml"
  "tasks/summary_monthly.yaml"
)

TASK_IDS=(
  "capture-analyze"
  "summary-daily"
  "summary-weekly"
  "summary-monthly"
)

if [[ "${FAST:-0}" == "1" ]]; then
  TASK_IDS=(
    "capture-analyze"
    "summary-daily"
  )
fi

echo "[1/3] validate migrated task yamls..."
for file in "${TASK_FILES[@]}"; do
  "$PYTHON_BIN" cron_manager.py validate "$file"
done

echo "[2/3] sync tasks to backends..."
"$PYTHON_BIN" cron_manager.py sync || true

echo "[3/3] run migrated tasks once..."
FAILED=0
for id in "${TASK_IDS[@]}"; do
  echo "running task: $id"
  OUTPUT="$("$PYTHON_BIN" cron_manager.py run-task "$id" --trigger migration-test || true)"
  echo "$OUTPUT"
  if echo "$OUTPUT" | grep -q '"success": true'; then
    continue
  fi
  # tmux backend task may already be running from sync; treat as expected pass.
  if [[ "$id" == "capture-analyze" ]] && echo "$OUTPUT" | grep -q "task already running"; then
    echo "capture-analyze already running in tmux backend, treated as pass."
    continue
  fi
  FAILED=1
done

if [[ "$FAILED" -eq 0 ]]; then
  echo "migrated task smoke test finished: all tasks succeeded."
else
  echo "migrated task smoke test finished: some tasks failed."
  exit 1
fi
