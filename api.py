#!/usr/bin/env python3
"""Flask API server for cron agent control panel."""

import json
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, render_template

import cron_manager
import recorder
import storage_paths

BASE_DIR = Path(__file__).parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "web" / "templates"),
    static_folder=str(BASE_DIR / "web" / "static"),
)


def _public_task(task: dict) -> dict:
    """Strip internal fields from task payload."""
    return {k: v for k, v in task.items() if not k.startswith("_")}


def get_status() -> dict:
    """Get service status from cron manager backends."""
    backends_status = cron_manager.get_backends_status()
    return {
        "cron_manager": {
            "backends": backends_status,
        }
    }


def get_records(date: str | None = None, limit: int = 50) -> list[dict]:
    """Get activity records."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    storage_paths.migrate_legacy_data_once()
    records_dir = recorder.get_records_dir()
    record_file = records_dir / f"{date}.jsonl"

    records = []
    if record_file.exists():
        with open(record_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue

    records = list(reversed(records))
    return records[:limit]


def get_all_record_dates() -> list[str]:
    """Get all available record dates."""
    storage_paths.migrate_legacy_data_once()
    records_dir = recorder.get_records_dir()
    if not records_dir.exists():
        return []

    dates = [f.stem for f in records_dir.glob("*.jsonl")]
    return sorted(dates, reverse=True)


def get_journal_files(period: str = "daily") -> list[dict]:
    """Get journal files for a specific period."""
    storage_paths.migrate_legacy_data_once()
    journal_dir = recorder.get_journal_dir() / period
    if not journal_dir.exists():
        return []

    files = []
    output_root = storage_paths.get_output_root()
    for f in journal_dir.glob("*.md"):
        try:
            display_path = str(f.relative_to(output_root))
        except ValueError:
            display_path = str(f)
        files.append({"name": f.name, "date": f.stem, "path": display_path})

    return sorted(files, key=lambda x: x["date"], reverse=True)


def get_journal_content(period: str, filename: str) -> str | None:
    """Get content of a specific journal file."""
    storage_paths.migrate_legacy_data_once()
    journal_dir = recorder.get_journal_dir() / period
    filepath = journal_dir / filename

    if not filepath.exists():
        return None

    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


@app.route("/api/status")
def api_status():
    """Get overall service status from cron manager."""
    return jsonify(get_status())


@app.route("/api/records")
def api_records():
    """Get activity records."""
    date = request.args.get("date")
    limit = int(request.args.get("limit", 50))
    return jsonify(get_records(date, limit))


@app.route("/api/records/dates")
def api_record_dates():
    """Get all available record dates."""
    return jsonify(get_all_record_dates())


@app.route("/api/journal/<period>")
def api_journal_files(period):
    """Get journal files for a specific period."""
    if period not in ["daily", "weekly", "monthly", "period"]:
        return jsonify({"error": "Invalid period"}), 400
    return jsonify(get_journal_files(period))


@app.route("/api/journal/<period>/<path:filename>")
def api_journal_content(period, filename):
    """Get content of a specific journal file."""
    if period not in ["daily", "weekly", "monthly", "period"]:
        return jsonify({"error": "Invalid period"}), 400
    content = get_journal_content(period, filename)
    if content is None:
        return jsonify({"error": "File not found"}), 404
    return jsonify({"content": content})


@app.route("/api/logs")
def api_logs():
    """Get application logs."""
    records = get_records(limit=100)
    return jsonify(records)


@app.route("/api/messages")
def api_messages():
    """Get message list."""
    limit = int(request.args.get("limit", 100))
    messages = recorder.read_messages(limit=limit)
    return jsonify(messages)


@app.route("/api/tasks", methods=["GET"])
def api_tasks_list():
    """List cron manager tasks."""
    return jsonify(cron_manager.api_list_tasks())


@app.route("/api/tasks/<task_id>", methods=["GET"])
def api_task_get(task_id):
    """Get a task by id."""
    task = cron_manager.get_task(task_id)
    if not task:
        return jsonify({"success": False, "error": "task not found"}), 404
    safe = _public_task(task)
    safe["_valid"] = task.get("_valid", False)
    safe["_errors"] = task.get("_errors", [])
    return jsonify(safe)


@app.route("/api/tasks", methods=["POST"])
def api_task_create():
    """Create a task from payload."""
    try:
        payload = request.json or {}
        task = cron_manager.task_from_api_payload(payload)
        saved = cron_manager.save_task(task)
        return jsonify({"success": True, "task": _public_task(saved)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/tasks/<task_id>", methods=["PUT"])
def api_task_update(task_id):
    """Update task by id."""
    try:
        payload = request.json or {}
        task = cron_manager.task_from_api_payload(payload, task_id=task_id)
        saved = cron_manager.save_task(task)
        return jsonify({"success": True, "task": _public_task(saved)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def api_task_delete(task_id):
    """Delete task by id."""
    result = cron_manager.delete_task(task_id)
    status = 200 if result.get("success") else 404
    return jsonify(result), status


@app.route("/api/tasks/<task_id>/settings", methods=["GET"])
def api_task_settings_get(task_id):
    """Get task-level settings payload."""
    settings = cron_manager.get_task_settings(task_id)
    if settings is None:
        return jsonify({"success": False, "error": "task not found"}), 404
    return jsonify({"success": True, "task_id": task_id, "settings": settings})


@app.route("/api/tasks/<task_id>/settings", methods=["PUT"])
def api_task_settings_put(task_id):
    """Update task-level settings payload."""
    payload = request.json or {}
    result = cron_manager.update_task_settings(task_id, payload)
    status = 200 if result.get("success") else 400
    return jsonify(result), status


@app.route("/api/tasks/<task_id>/pause", methods=["POST"])
def api_task_pause(task_id):
    """Pause task by id."""
    result = cron_manager.pause_task(task_id)
    status = 200 if result.get("success") else 404
    return jsonify(result), status


@app.route("/api/tasks/<task_id>/resume", methods=["POST"])
def api_task_resume(task_id):
    """Resume task by id."""
    result = cron_manager.resume_task(task_id)
    status = 200 if result.get("success") else 404
    return jsonify(result), status


@app.route("/api/tasks/<task_id>/run", methods=["POST"])
def api_task_run(task_id):
    """Run task immediately."""
    result = cron_manager.run_task(task_id, trigger="api")
    status = 200 if result.get("success") else 400
    return jsonify(result), status


@app.route("/api/tasks/<task_id>/status", methods=["GET"])
def api_task_status(task_id):
    """Get task runtime status."""
    result = cron_manager.get_task_status(task_id)
    status = 200 if result.get("found") else 404
    return jsonify(result), status


@app.route("/api/tasks/sync", methods=["POST"])
def api_task_sync():
    """Sync tasks to backends."""
    result = cron_manager.sync_all_tasks()
    status = 200 if result.get("success") else 500
    return jsonify(result), status


@app.route("/api/backends/status", methods=["GET"])
def api_backends_status():
    """Get tmux/cron backend status."""
    return jsonify(cron_manager.get_backends_status())


@app.route("/api/backends/sync", methods=["POST"])
def api_backends_sync():
    """Sync all tasks to both backends."""
    result = cron_manager.sync_all_tasks()
    status = 200 if result.get("success") else 500
    return jsonify(result), status


@app.route("/api/runs", methods=["GET"])
def api_runs():
    """Deprecated endpoint."""
    return jsonify({"error": "deprecated endpoint", "message": "run event logs have been sunset; use raw trace files"}), 410


@app.route("/api/runs/<run_id>/events", methods=["GET"])
def api_run_events(run_id):
    """Deprecated endpoint."""
    _ = run_id
    return jsonify({"error": "deprecated endpoint", "message": "run event logs have been sunset; use raw trace files"}), 410


@app.route("/")
def index():
    """Main dashboard page."""
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=18001)
