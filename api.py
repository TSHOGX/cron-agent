#!/usr/bin/env python3
"""Flask API server for cron agent control panel."""

import json
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, render_template

BASE_DIR = Path(__file__).parent

app = Flask(__name__, template_folder=str(BASE_DIR / 'web' / 'templates'), static_folder=str(BASE_DIR / 'web' / 'static'))


def load_config():
    """Load configuration from config.json."""
    config_path = BASE_DIR / "config.json"
    with open(config_path) as f:
        return json.load(f)


def save_config(config):
    """Save configuration to config.json."""
    config_path = BASE_DIR / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


import recorder
import cron_manager


def _public_task(task: dict) -> dict:
    """Strip internal fields from task payload."""
    return {k: v for k, v in task.items() if not k.startswith("_")}


def get_status():
    """Get service status from cron manager backends."""
    backends_status = cron_manager.get_backends_status()

    return {
        "cron_manager": {
            "backends": backends_status,
        }
    }


def get_records(date=None, limit=50):
    """Get activity records."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    records_dir = BASE_DIR / "records"
    record_file = records_dir / f"{date}.jsonl"

    records = []
    if record_file.exists():
        with open(record_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    # Reverse to show newest first
    records = list(reversed(records))
    return records[:limit]


def get_all_record_dates():
    """Get all available record dates."""
    records_dir = BASE_DIR / "records"
    if not records_dir.exists():
        return []

    dates = []
    for f in records_dir.glob("*.jsonl"):
        dates.append(f.stem)

    return sorted(dates, reverse=True)


def get_journal_files(period: str = "daily"):
    """Get journal files for a specific period."""
    journal_dir = BASE_DIR / "journal" / period
    if not journal_dir.exists():
        return []

    files = []
    for f in journal_dir.glob("*.md"):
        files.append({
            "name": f.name,
            "date": f.stem,
            "path": str(f.relative_to(BASE_DIR))
        })

    return sorted(files, key=lambda x: x["date"], reverse=True)


def get_journal_content(period: str, filename: str):
    """Get content of a specific journal file."""
    journal_dir = BASE_DIR / "journal" / period
    filepath = journal_dir / filename

    if not filepath.exists():
        return None

    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


# API Routes
@app.route('/api/status')
def api_status():
    """Get overall service status from cron manager."""
    return jsonify(get_status())


@app.route('/api/config')
def api_config():
    """Get current configuration."""
    config = load_config()
    # Mask API key for security
    if "api" in config and "auth_token" in config["api"]:
        config["api"]["auth_token"] = config["api"]["auth_token"][:8] + "****" if len(config["api"]["auth_token"]) > 8 else "****"
    return jsonify(config)


@app.route('/api/config', methods=['POST'])
def api_config_update():
    """Update configuration."""
    try:
        new_config = request.json
        current_config = load_config()

        # Merge updates
        current_config.update(new_config)

        # Handle nested api config
        if "api" in new_config:
            if "api" not in current_config:
                current_config["api"] = {}
            current_config["api"].update(new_config["api"])

        save_config(current_config)
        return jsonify({"success": True, "message": "Configuration saved"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/record_prompt')
def api_record_prompt():
    """Get current record prompt template."""
    config = load_config()
    prompt = config.get("record_prompt", {
        "system": "你是 Kimi，由 Moonshot AI 提供的人工智能助手。请先思考，然后简洁回答。",
        "user": "根据这些截图，直接回答用户正在做什么。只回答1-2句话，越简洁越好。"
    })
    return jsonify(prompt)


@app.route('/api/record_prompt', methods=['POST'])
def api_record_prompt_update():
    """Update record prompt template."""
    try:
        new_prompt = request.json
        config = load_config()
        config["record_prompt"] = new_prompt
        save_config(config)
        return jsonify({"success": True, "message": "Record prompt updated"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/summary_prompt')
def api_summary_prompt():
    """Get current summary prompt template."""
    config = load_config()
    prompt = config.get("summary_prompt", {
        "system": "你是 Kimi，由 Moonshot AI 提供的人工智能助手。请简洁回答。",
        "daily": "请帮我总结今天（{date}）的工作活动记录。\n\n活动记录：\n{records}\n\n请用简洁的语言总结：\n1. 主要做了什么工作\n2. 花费时间最多的活动是什么\n3. 有哪些值得注意的内容\n\n请用中文回复，控制在200字以内。直接回答，不要思考过程。",
        "weekly": "请帮我根据以下每日总结，汇总本周（{date_range}）的工作情况。\n\n每日总结：\n{notes}\n\n请用简洁的语言总结：\n1. 本周的主要工作方向\n2. 花费时间最多的工作内容\n3. 有哪些值得注意的内容或成果\n\n请用中文回复，控制在300字以内。直接回答，不要思考过程。",
        "monthly": "请帮我根据以下每日总结，汇总本月（{date_range}）的工作情况。\n\n每日总结：\n{notes}\n\n请用简洁的语言总结：\n1. 本月的主要工作方向\n2. 花费时间最多的工作内容\n3. 有哪些值得注意的内容或成果\n\n请用中文回复，控制在300字以内。直接回答，不要思考过程。",
        "time_of_day": "请简要总结今天{label}的工作内容。\n\n活动记录：\n{records}\n\n请用2-3句话总结。直接回答。"
    })
    return jsonify(prompt)


@app.route('/api/summary_prompt', methods=['POST'])
def api_summary_prompt_update():
    """Update summary prompt template."""
    try:
        new_prompt = request.json
        config = load_config()
        config["summary_prompt"] = new_prompt
        save_config(config)
        return jsonify({"success": True, "message": "Summary prompt updated"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/records')
def api_records():
    """Get activity records."""
    date = request.args.get('date')
    limit = int(request.args.get('limit', 50))
    return jsonify(get_records(date, limit))


@app.route('/api/records/dates')
def api_record_dates():
    """Get all available record dates."""
    return jsonify(get_all_record_dates())


@app.route('/api/journal/<period>')
def api_journal_files(period):
    """Get journal files for a specific period."""
    if period not in ["daily", "weekly", "monthly", "period"]:
        return jsonify({"error": "Invalid period"}), 400
    return jsonify(get_journal_files(period))


@app.route('/api/journal/<period>/<path:filename>')
def api_journal_content(period, filename):
    """Get content of a specific journal file."""
    if period not in ["daily", "weekly", "monthly", "period"]:
        return jsonify({"error": "Invalid period"}), 400
    content = get_journal_content(period, filename)
    if content is None:
        return jsonify({"error": "File not found"}), 404
    return jsonify({"content": content})


@app.route('/api/logs')
def api_logs():
    """Get application logs."""
    # This would typically read from a log file
    # For now, return recent records as "logs"
    records = get_records(limit=100)
    return jsonify(records)


# Messages API
@app.route('/api/messages')
def api_messages():
    """Get message list."""
    limit = int(request.args.get('limit', 100))
    messages = recorder.read_messages(limit=limit)
    return jsonify(messages)


@app.route('/api/tasks', methods=['GET'])
def api_tasks_list():
    """List cron manager tasks."""
    return jsonify(cron_manager.api_list_tasks())


@app.route('/api/tasks/<task_id>', methods=['GET'])
def api_task_get(task_id):
    """Get a task by id."""
    task = cron_manager.get_task(task_id)
    if not task:
        return jsonify({"success": False, "error": "task not found"}), 404
    safe = _public_task(task)
    safe["_valid"] = task.get("_valid", False)
    safe["_errors"] = task.get("_errors", [])
    return jsonify(safe)


@app.route('/api/tasks', methods=['POST'])
def api_task_create():
    """Create a task from payload."""
    try:
        payload = request.json or {}
        task = cron_manager.task_from_api_payload(payload)
        saved = cron_manager.save_task(task)
        return jsonify({"success": True, "task": _public_task(saved)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route('/api/tasks/<task_id>', methods=['PUT'])
def api_task_update(task_id):
    """Update task by id."""
    try:
        payload = request.json or {}
        task = cron_manager.task_from_api_payload(payload, task_id=task_id)
        saved = cron_manager.save_task(task)
        return jsonify({"success": True, "task": _public_task(saved)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def api_task_delete(task_id):
    """Delete task by id."""
    result = cron_manager.delete_task(task_id)
    status = 200 if result.get("success") else 404
    return jsonify(result), status


@app.route('/api/tasks/<task_id>/pause', methods=['POST'])
def api_task_pause(task_id):
    """Pause task by id."""
    result = cron_manager.pause_task(task_id)
    status = 200 if result.get("success") else 404
    return jsonify(result), status


@app.route('/api/tasks/<task_id>/resume', methods=['POST'])
def api_task_resume(task_id):
    """Resume task by id."""
    result = cron_manager.resume_task(task_id)
    status = 200 if result.get("success") else 404
    return jsonify(result), status


@app.route('/api/tasks/<task_id>/run', methods=['POST'])
def api_task_run(task_id):
    """Run task immediately."""
    result = cron_manager.run_task(task_id, trigger="api")
    status = 200 if result.get("success") else 400
    return jsonify(result), status


@app.route('/api/tasks/<task_id>/status', methods=['GET'])
def api_task_status(task_id):
    """Get task runtime status."""
    result = cron_manager.get_task_status(task_id)
    status = 200 if result.get("found") else 404
    return jsonify(result), status


@app.route('/api/tasks/sync', methods=['POST'])
def api_task_sync():
    """Sync tasks to crontab."""
    result = cron_manager.sync_all_tasks()
    status = 200 if result.get("success") else 500
    return jsonify(result), status


@app.route('/api/backends/status', methods=['GET'])
def api_backends_status():
    """Get tmux/cron backend status."""
    return jsonify(cron_manager.get_backends_status())


@app.route('/api/backends/sync', methods=['POST'])
def api_backends_sync():
    """Sync all tasks to both backends."""
    result = cron_manager.sync_all_tasks()
    status = 200 if result.get("success") else 500
    return jsonify(result), status


@app.route('/api/runs', methods=['GET'])
def api_runs():
    """List run summaries."""
    task_id = request.args.get("task_id")
    limit = int(request.args.get("limit", 100))
    return jsonify(cron_manager.list_runs(task_id=task_id, limit=limit))


@app.route('/api/runs/<run_id>/events', methods=['GET'])
def api_run_events(run_id):
    """Get all events for a run."""
    return jsonify(cron_manager.get_run_events(run_id))


@app.route('/')
def index():
    """Main dashboard page."""
    return render_template('index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=18001)
