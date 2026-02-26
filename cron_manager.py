#!/usr/bin/env python3
"""Cron Manager: YAML task registry + dual-mode executors + JSONL event logs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

BASE_DIR = Path(__file__).parent
TASKS_DIR = BASE_DIR / "tasks"
RUNTIME_DIR = BASE_DIR / "runtime"
LOGS_DIR = BASE_DIR / "logs"
RUNS_DIR = LOGS_DIR / "runs"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
STATE_FILE = RUNTIME_DIR / "state.json"
MARKER_BEGIN = "# >>> CRON_AGENT_MANAGED BEGIN >>>"
MARKER_END = "# <<< CRON_AGENT_MANAGED END <<<"
DEFAULT_TIMEZONE = "Asia/Shanghai"
TMUX_SESSION_PREFIX = "cronmgr_"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ensure_dirs() -> None:
    for d in [TASKS_DIR, RUNTIME_DIR, LOGS_DIR, RUNS_DIR, ARTIFACTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _yaml_load(path: Path) -> dict:
    if yaml is not None:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    ruby_cmd = [
        "ruby",
        "-ryaml",
        "-rjson",
        "-e",
        "puts JSON.generate(YAML.load_file(ARGV[0]))",
        str(path),
    ]
    result = subprocess.run(ruby_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"YAML parser unavailable. Install PyYAML. {result.stderr.strip()}")
    return json.loads(result.stdout or "{}")


def _yaml_dump(path: Path, data: dict) -> None:
    if yaml is not None:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        return

    # Fallback to JSON text with .yaml suffix when PyYAML is unavailable.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_state() -> dict:
    return _load_json_file(STATE_FILE, {"tasks": {}, "runs": {}})


def _save_state(state: dict) -> None:
    _save_json_file(STATE_FILE, state)


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-")
    return s.lower() or "task"


def _validate_cron_expr(expr: str) -> bool:
    parts = expr.strip().split()
    if len(parts) != 5:
        return False
    token_re = re.compile(r"^[\d\*/,\-]+$|^\*$")
    return all(bool(token_re.match(p)) for p in parts)


def _fill_defaults(task: dict) -> dict:
    task.setdefault("apiVersion", "cron-agent/v1")
    task.setdefault("kind", "CronTask")
    task.setdefault("metadata", {})
    task["metadata"].setdefault("enabled", True)
    task["metadata"].setdefault("name", task["metadata"].get("id", "Unnamed Task"))
    task.setdefault("spec", {})
    spec = task["spec"]
    spec.setdefault("mode", "llm")
    spec.setdefault("paused", False)
    schedule_seed = spec.get("schedule", {}) if isinstance(spec.get("schedule"), dict) else {}
    default_backend = "tmux" if "intervalSeconds" in schedule_seed else "cron"
    spec.setdefault("runBackend", default_backend)

    schedule = spec.setdefault("schedule", {})
    schedule.setdefault("timezone", DEFAULT_TIMEZONE)
    schedule.setdefault("jitterSeconds", 0)
    schedule.setdefault("maxConcurrency", 1)
    schedule.setdefault("misfirePolicy", "run_once")

    spec.setdefault("input", {})
    spec["input"].setdefault("prompt", "")
    spec["input"].setdefault("contextFiles", [])
    spec["input"].setdefault("variables", {})

    execution = spec.setdefault("execution", {})
    execution.setdefault("timeoutSeconds", 600)
    execution.setdefault("workingDirectory", ".")
    retry = execution.setdefault("retry", {})
    retry.setdefault("maxAttempts", 1)
    retry.setdefault("backoffSeconds", 0)

    mode_cfg = spec.setdefault("modeConfig", {})
    agent_cfg = mode_cfg.setdefault("agent", {})
    agent_cfg.setdefault("provider", "cloud_code_cli")
    agent_cfg.setdefault("model", "sonnet")
    agent_cfg.setdefault("allowImagePathInPrompt", True)
    agent_cfg.setdefault("commandTemplate", "claude -p --output-format json -- {prompt}")

    llm_cfg = mode_cfg.setdefault("llm", {})
    llm_cfg.setdefault("provider", "kimi_openai_compat")
    llm_cfg.setdefault("model", "kimi-k2.5")
    llm_cfg.setdefault("temperature", 0.2)
    llm_cfg.setdefault("maxTokens", 4000)
    llm_cfg.setdefault("apiBase", "https://api.moonshot.cn/v1")
    llm_cfg.setdefault("authRef", "env:KIMI_API_KEY")

    output_cfg = spec.setdefault("output", {})
    output_cfg.setdefault("sink", "file")
    output_cfg.setdefault("pathTemplate", "artifacts/{task_id}/{run_id}/result.md")
    output_cfg.setdefault("format", "markdown")

    logging_cfg = spec.setdefault("logging", {})
    logging_cfg.setdefault("eventJsonlPath", "logs/runs/{date}.jsonl")
    logging_cfg.setdefault("savePrompt", True)
    logging_cfg.setdefault("saveToolCalls", True)
    logging_cfg.setdefault("saveStdout", True)
    logging_cfg.setdefault("saveStderr", True)

    return task


def validate_task(task: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(task, dict):
        return ["Task must be a mapping."]

    if task.get("apiVersion") not in (None, "cron-agent/v1"):
        errors.append("apiVersion must be cron-agent/v1")
    if task.get("kind") not in (None, "CronTask"):
        errors.append("kind must be CronTask")

    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("metadata is required and must be an object")
    else:
        task_id = metadata.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            errors.append("metadata.id is required")
        elif _slug(task_id) != task_id:
            errors.append("metadata.id must match [a-zA-Z0-9_-] and be slug-like")

    spec = task.get("spec")
    if not isinstance(spec, dict):
        errors.append("spec is required and must be an object")
    else:
        mode = spec.get("mode")
        if mode not in ("agent", "llm"):
            errors.append("spec.mode must be agent or llm")
        run_backend = spec.get("runBackend")
        if run_backend not in ("tmux", "cron"):
            errors.append("spec.runBackend must be tmux or cron")

        schedule = spec.get("schedule")
        if not isinstance(schedule, dict):
            errors.append("spec.schedule is required")
        else:
            if run_backend == "cron":
                cron_expr = schedule.get("cron")
                if not isinstance(cron_expr, str) or not cron_expr.strip():
                    errors.append("spec.schedule.cron is required when runBackend=cron")
                elif not _validate_cron_expr(cron_expr):
                    errors.append("spec.schedule.cron is invalid")
            elif run_backend == "tmux":
                interval = schedule.get("intervalSeconds")
                if not isinstance(interval, int) or interval <= 0:
                    errors.append("spec.schedule.intervalSeconds must be positive int when runBackend=tmux")

            if schedule.get("misfirePolicy", "run_once") not in ("run_once", "skip"):
                errors.append("spec.schedule.misfirePolicy must be run_once or skip")

    return errors


def load_task_from_file(path: Path) -> dict:
    data = _yaml_load(path)
    data = _fill_defaults(data)
    data["_file"] = str(path)
    return data


def list_tasks(include_invalid: bool = True) -> list[dict]:
    _ensure_dirs()
    items: list[dict] = []
    for path in sorted(TASKS_DIR.glob("*.yaml")):
        try:
            task = load_task_from_file(path)
            errors = validate_task(task)
            if errors:
                task["_valid"] = False
                task["_errors"] = errors
                if include_invalid:
                    items.append(task)
                continue
            task["_valid"] = True
            task["_errors"] = []
            items.append(task)
        except Exception as e:
            if include_invalid:
                items.append(
                    {
                        "metadata": {"id": path.stem, "name": path.stem, "enabled": False},
                        "spec": {},
                        "_file": str(path),
                        "_valid": False,
                        "_errors": [str(e)],
                    }
                )
    # Enforce unique metadata.id across task files.
    seen: dict[str, int] = {}
    for i, task in enumerate(items):
        task_id = task.get("metadata", {}).get("id")
        if not isinstance(task_id, str):
            continue
        if task_id in seen:
            first_idx = seen[task_id]
            for idx in [first_idx, i]:
                items[idx]["_valid"] = False
                errors = items[idx].setdefault("_errors", [])
                if "duplicate metadata.id across task files" not in errors:
                    errors.append("duplicate metadata.id across task files")
        else:
            seen[task_id] = i

    if include_invalid:
        return items
    return [item for item in items if item.get("_valid")]


def get_task(task_id: str) -> dict | None:
    for task in list_tasks(include_invalid=True):
        if task.get("metadata", {}).get("id") == task_id:
            return task
    return None


def _task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.yaml"


def _is_task_enabled(task: dict) -> bool:
    metadata = task.get("metadata", {})
    spec = task.get("spec", {})
    return bool(metadata.get("enabled", True)) and not bool(spec.get("paused", False))


def _task_backend(task: dict) -> str:
    return task.get("spec", {}).get("runBackend", "cron")


def _build_cron_block(tasks: list[dict]) -> str:
    python_path = BASE_DIR / ".venv" / "bin" / "python"
    python_exec = str(python_path) if python_path.exists() else sys.executable or "python3"
    lines: list[str] = [MARKER_BEGIN]

    for task in tasks:
        if not task.get("_valid") or not _is_task_enabled(task) or _task_backend(task) != "cron":
            continue
        task_id = task["metadata"]["id"]
        cron_expr = task["spec"]["schedule"]["cron"]
        timezone = task["spec"]["schedule"].get("timezone", DEFAULT_TIMEZONE)
        cmd = (
            f"cd {shlex.quote(str(BASE_DIR))} && {shlex.quote(str(python_exec))} "
            f"{shlex.quote(str(BASE_DIR / 'cron_manager.py'))} run-task {shlex.quote(task_id)} --trigger cron"
        )
        lines.append(f"# cron-agent task={task_id}")
        lines.append(f"CRON_TZ={timezone}")
        lines.append(f"{cron_expr} {cmd}")

    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def _read_current_crontab() -> str:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
    except Exception:
        return ""
    return ""


def _strip_managed_block(content: str) -> str:
    if MARKER_BEGIN not in content:
        return content.strip() + ("\n" if content.strip() else "")
    start = content.find(MARKER_BEGIN)
    end = content.find(MARKER_END)
    if end == -1:
        return content[:start].strip() + "\n"
    end += len(MARKER_END)
    merged = (content[:start] + content[end:]).strip()
    return merged + ("\n" if merged else "")


def sync_cron_tasks() -> dict:
    tasks = list_tasks(include_invalid=False)
    current = _read_current_crontab()
    unmanaged = _strip_managed_block(current)
    managed = _build_cron_block(tasks)
    new_content = unmanaged + managed

    with tempfile.NamedTemporaryFile(mode="w", suffix=".cron", delete=False) as f:
        f.write(new_content)
        temp_path = f.name

    try:
        result = subprocess.run(["crontab", temp_path], capture_output=True, text=True)
    except Exception as e:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return {"success": False, "error": str(e)}
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    if result.returncode != 0:
        return {"success": False, "error": result.stderr.strip() or "failed to install crontab", "backend": "cron"}
    count = len([t for t in tasks if _is_task_enabled(t) and _task_backend(t) == "cron"])
    return {"success": True, "task_count": count, "backend": "cron"}


def get_cron_backend_status() -> dict:
    current = _read_current_crontab()
    jobs: list[str] = []
    in_block = False
    for line in current.splitlines():
        if line.strip() == MARKER_BEGIN:
            in_block = True
            continue
        if line.strip() == MARKER_END:
            in_block = False
            continue
        if in_block and line.strip() and not line.strip().startswith("#") and not line.startswith("CRON_TZ"):
            jobs.append(line.strip())
    return {"installed": MARKER_BEGIN in current, "jobs": jobs, "count": len(jobs), "backend": "cron"}


def _tmux_session_name(task_id: str) -> str:
    safe = _slug(task_id).replace("-", "_")
    return f"{TMUX_SESSION_PREFIX}{safe}"[:100]


def _list_tmux_sessions() -> list[str]:
    try:
        result = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def _kill_tmux_session(session_name: str) -> None:
    try:
        subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True, text=True)
    except Exception:
        pass


def _start_tmux_task(task: dict) -> dict:
    task_id = task["metadata"]["id"]
    session = _tmux_session_name(task_id)
    interval = int(task["spec"]["schedule"].get("intervalSeconds", 900))
    python_path = BASE_DIR / ".venv" / "bin" / "python"
    python_exec = str(python_path) if python_path.exists() else sys.executable or "python3"
    cmd = (
        f"while true; do cd {shlex.quote(str(BASE_DIR))} && "
        f"{shlex.quote(str(python_exec))} {shlex.quote(str(BASE_DIR / 'cron_manager.py'))} "
        f"run-task {shlex.quote(task_id)} --trigger tmux; sleep {interval}; done"
    )
    _kill_tmux_session(session)
    try:
        result = subprocess.run(["tmux", "new-session", "-d", "-s", session, cmd], capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "session": session, "error": result.stderr.strip() or "tmux start failed"}
        return {"success": True, "session": session}
    except Exception as e:
        return {"success": False, "session": session, "error": str(e)}


def sync_tmux_tasks() -> dict:
    tasks = list_tasks(include_invalid=False)
    desired_tasks = [t for t in tasks if _is_task_enabled(t) and _task_backend(t) == "tmux"]
    desired_sessions = {_tmux_session_name(t["metadata"]["id"]): t for t in desired_tasks}

    existing = _list_tmux_sessions()
    managed_existing = [s for s in existing if s.startswith(TMUX_SESSION_PREFIX)]

    # Remove stale managed sessions.
    for session in managed_existing:
        if session not in desired_sessions:
            _kill_tmux_session(session)

    errors: list[dict] = []
    started = 0
    for session, task in desired_sessions.items():
        res = _start_tmux_task(task)
        if res.get("success"):
            started += 1
        else:
            errors.append({"task_id": task["metadata"]["id"], "error": res.get("error", "unknown error")})

    return {
        "success": len(errors) == 0,
        "backend": "tmux",
        "task_count": len(desired_tasks),
        "started": started,
        "errors": errors,
    }


def get_tmux_backend_status() -> dict:
    sessions = _list_tmux_sessions()
    managed = [s for s in sessions if s.startswith(TMUX_SESSION_PREFIX)]
    return {"backend": "tmux", "running": len(managed) > 0, "sessions": managed, "count": len(managed)}


def sync_all_tasks() -> dict:
    cron_res = sync_cron_tasks()
    tmux_res = sync_tmux_tasks()
    return {"success": bool(cron_res.get("success")) and bool(tmux_res.get("success")), "cron": cron_res, "tmux": tmux_res}


def get_backends_status() -> dict:
    cron_status = get_cron_backend_status()
    tmux_status = get_tmux_backend_status()
    return {"cron": cron_status, "tmux": tmux_status}


def get_scheduler_status() -> dict:
    # Backward-compatible alias for previous API usage (cron-only summary).
    return get_cron_backend_status()


def _safe_task_for_api(task: dict) -> dict:
    return {k: v for k, v in task.items() if not k.startswith("_")}


def _format_template(template: str, task: dict, run_id: str) -> str:
    task_id = task["metadata"]["id"]
    return template.format(task_id=task_id, run_id=run_id, date=_today_str())


def _event_log_path(task: dict, run_id: str) -> Path:
    template = task["spec"]["logging"]["eventJsonlPath"]
    rel = _format_template(template, task, run_id)
    return BASE_DIR / rel


def _write_event(task: dict, run_id: str, event: str, attempt: int = 1, **payload: Any) -> None:
    path = _event_log_path(task, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": _now_iso(),
        "run_id": run_id,
        "task_id": task["metadata"]["id"],
        "event": event,
        "mode": task["spec"]["mode"],
        "attempt": attempt,
    }
    row.update(payload)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _get_secret_from_auth_ref(auth_ref: str | None) -> str | None:
    if not auth_ref:
        return None
    if auth_ref.startswith("env:"):
        return os.getenv(auth_ref.split(":", 1)[1])
    return auth_ref


def _prepare_prompt(task: dict) -> str:
    prompt = task["spec"]["input"].get("prompt", "")
    variables = task["spec"]["input"].get("variables", {}) or {}
    try:
        prompt = prompt.format(**variables)
    except Exception:
        pass

    context_files = task["spec"]["input"].get("contextFiles", []) or []
    snippets: list[str] = []
    for item in context_files:
        p = BASE_DIR / str(item)
        if p.exists() and p.is_file():
            try:
                text = p.read_text(encoding="utf-8")
                snippets.append(f"\n--- file:{item} ---\n{text[:8000]}")
            except Exception:
                snippets.append(f"\n--- file:{item} ---\n<unreadable>")

    if snippets:
        prompt = prompt + "\n\n[Context Files]" + "".join(snippets)
    return prompt


def _execute_llm(task: dict, prompt: str, timeout_seconds: int) -> tuple[bool, str, dict]:
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        return False, "", {"error": f"openai sdk missing: {e}"}

    llm_cfg = task["spec"]["modeConfig"].get("llm", {})
    api_key = _get_secret_from_auth_ref(llm_cfg.get("authRef"))
    if not api_key:
        return False, "", {"error": "Missing API key from spec.modeConfig.llm.authRef"}

    model = llm_cfg.get("model", "kimi-k2.5")
    client = OpenAI(api_key=api_key, base_url=llm_cfg.get("apiBase", "https://api.moonshot.cn/v1"))

    started = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=float(llm_cfg.get("temperature", 0.2)),
            max_tokens=int(llm_cfg.get("maxTokens", 4000)),
            stream=False,
        )
        elapsed = time.time() - started
        text = ""
        if resp.choices and resp.choices[0].message:
            text = resp.choices[0].message.content or ""
        if not text.strip():
            return False, "", {"error": "empty response", "elapsed": elapsed}
        return True, text, {"model": model, "elapsed": elapsed}
    except Exception as e:
        elapsed = time.time() - started
        return False, "", {"error": str(e), "elapsed": elapsed, "timeout": timeout_seconds}


def _execute_agent(task: dict, prompt: str, timeout_seconds: int) -> tuple[bool, str, dict]:
    cfg = task["spec"]["modeConfig"].get("agent", {})
    template = cfg.get("commandTemplate", "claude -p --output-format json -- {prompt}")
    # Avoid str.format so literal JSON braces in command templates do not break parsing.
    command = template.replace("{prompt}", shlex.quote(prompt)).replace("{task_id}", task["metadata"]["id"])

    started = time.time()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(BASE_DIR / task["spec"]["execution"].get("workingDirectory", ".")),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=os.environ.copy(),
        )
        elapsed = time.time() - started
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            return False, "", {
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "elapsed": elapsed,
            }

        text = stdout
        if stdout.startswith("{"):
            try:
                j = json.loads(stdout)
                text = j.get("result") or j.get("output") or stdout
            except Exception:
                pass
        return True, text, {"stdout": stdout, "stderr": stderr, "elapsed": elapsed}
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - started
        return False, "", {
            "error": f"agent timeout after {timeout_seconds}s",
            "stdout": (e.stdout or ""),
            "stderr": (e.stderr or ""),
            "elapsed": elapsed,
        }
    except Exception as e:
        elapsed = time.time() - started
        return False, "", {"error": str(e), "elapsed": elapsed}


def _write_output(task: dict, run_id: str, text: str) -> str:
    output_cfg = task["spec"].get("output", {})
    template = output_cfg.get("pathTemplate", "artifacts/{task_id}/{run_id}/result.md")
    path = BASE_DIR / _format_template(template, task, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return str(path.relative_to(BASE_DIR))


def _mark_task_running(task_id: str, run_id: str) -> bool:
    state = _load_state()
    info = state.setdefault("tasks", {}).setdefault(task_id, {})
    if info.get("running", False):
        return False
    info["running"] = True
    info["current_run_id"] = run_id
    info["started_at"] = _now_iso()
    _save_state(state)
    return True


def _mark_task_finished(task_id: str, status: str, run_id: str, error: str | None = None) -> None:
    state = _load_state()
    info = state.setdefault("tasks", {}).setdefault(task_id, {})
    info["running"] = False
    info["current_run_id"] = None
    info["last_run_id"] = run_id
    info["last_status"] = status
    info["last_finished_at"] = _now_iso()
    if error:
        info["last_error"] = error
    state.setdefault("runs", {})[run_id] = {
        "task_id": task_id,
        "status": status,
        "finished_at": _now_iso(),
        "error": error,
    }
    _save_state(state)


def save_task(task: dict) -> dict:
    _ensure_dirs()
    task = _fill_defaults(task)
    errors = validate_task(task)
    if errors:
        raise ValueError("; ".join(errors))

    task_id = task["metadata"]["id"]
    path = _task_path(task_id)
    data_to_dump = {k: v for k, v in task.items() if not k.startswith("_")}
    _yaml_dump(path, data_to_dump)

    sync_all_tasks()
    return load_task_from_file(path)


def delete_task(task_id: str) -> dict:
    path = _task_path(task_id)
    if not path.exists():
        return {"success": False, "error": f"task not found: {task_id}"}
    path.unlink()
    sync_all_tasks()
    return {"success": True}


def pause_task(task_id: str) -> dict:
    task = get_task(task_id)
    if not task or not task.get("_valid"):
        return {"success": False, "error": f"task not found or invalid: {task_id}"}
    task["spec"]["paused"] = True
    save_task(task)
    return {"success": True}


def resume_task(task_id: str) -> dict:
    task = get_task(task_id)
    if not task or not task.get("_valid"):
        return {"success": False, "error": f"task not found or invalid: {task_id}"}
    task["spec"]["paused"] = False
    save_task(task)
    return {"success": True}


def run_task(task_id: str, trigger: str = "manual") -> dict:
    _ensure_dirs()
    task = get_task(task_id)
    if not task:
        return {"success": False, "error": f"task not found: {task_id}"}
    if not task.get("_valid"):
        return {"success": False, "error": f"task invalid: {'; '.join(task.get('_errors', []))}"}
    if not _is_task_enabled(task):
        return {"success": False, "error": "task disabled or paused"}

    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    spec = task["spec"]
    max_concurrency = int(spec["schedule"].get("maxConcurrency", 1))
    lock_acquired = False
    if max_concurrency <= 1:
        lock_acquired = _mark_task_running(task_id, run_id)
        if not lock_acquired:
            _write_event(task, run_id, "run.skipped.concurrent", meta={"trigger": trigger})
            return {"success": False, "error": "task already running", "run_id": run_id}

    prompt = _prepare_prompt(task)
    if spec.get("logging", {}).get("savePrompt", True):
        _write_event(task, run_id, "input.prepared", meta={"trigger": trigger, "prompt_preview": prompt[:500]})
    _write_event(task, run_id, "run.started", meta={"trigger": trigger, "mode": spec.get("mode")})

    timeout_seconds = int(spec["execution"].get("timeoutSeconds", 600))
    retry_cfg = spec["execution"].get("retry", {})
    max_attempts = max(1, int(retry_cfg.get("maxAttempts", 1)))
    backoff = max(0, int(retry_cfg.get("backoffSeconds", 0)))

    start = time.time()
    last_error = None
    final_text = ""
    final_meta: dict[str, Any] = {}

    try:
        for attempt in range(1, max_attempts + 1):
            _write_event(task, run_id, "executor.started", attempt=attempt)
            if spec.get("mode") == "agent":
                ok, text, meta = _execute_agent(task, prompt, timeout_seconds)
            else:
                ok, text, meta = _execute_llm(task, prompt, timeout_seconds)

            if spec.get("logging", {}).get("saveStdout", True) and meta.get("stdout"):
                _write_event(task, run_id, "executor.stdout", attempt=attempt, stdout=str(meta.get("stdout"))[:8000])
            if spec.get("logging", {}).get("saveStderr", True) and meta.get("stderr"):
                _write_event(task, run_id, "executor.stderr", attempt=attempt, stderr=str(meta.get("stderr"))[:8000])

            if ok:
                final_text = text
                final_meta = meta
                break

            last_error = meta.get("error") or meta.get("stderr") or "executor failed"
            _write_event(task, run_id, "run.failed.attempt", attempt=attempt, error=last_error, meta=meta)
            if attempt < max_attempts:
                _write_event(task, run_id, "run.retried", attempt=attempt, meta={"backoff_seconds": backoff})
                if backoff > 0:
                    time.sleep(backoff)
    except Exception as e:
        last_error = f"unexpected error: {e}"
        _write_event(task, run_id, "run.failed", error=last_error)
        _write_event(task, run_id, "run.summary", status="failed", error=last_error)
        _mark_task_finished(task_id, "failed", run_id, error=last_error)
        return {"success": False, "run_id": run_id, "error": last_error}

    duration = round(time.time() - start, 3)
    if final_text:
        output_path = _write_output(task, run_id, final_text)
        _write_event(task, run_id, "artifact.written", meta={"path": output_path})
        _write_event(task, run_id, "run.succeeded", meta={"duration_seconds": duration, **final_meta})
        _write_event(task, run_id, "run.summary", status="succeeded", duration_seconds=duration, output_path=output_path)
        _mark_task_finished(task_id, "succeeded", run_id)
        return {"success": True, "run_id": run_id, "output_path": output_path}

    _write_event(task, run_id, "run.failed", error=last_error, meta={"duration_seconds": duration})
    _write_event(task, run_id, "run.summary", status="failed", duration_seconds=duration, error=last_error)
    _mark_task_finished(task_id, "failed", run_id, error=str(last_error))
    return {"success": False, "run_id": run_id, "error": last_error}


def list_runs(task_id: str | None = None, limit: int = 100) -> list[dict]:
    _ensure_dirs()
    items: list[dict] = []
    files = sorted(RUNS_DIR.glob("*.jsonl"), reverse=True)
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("event") != "run.summary":
                        continue
                    if task_id and row.get("task_id") != task_id:
                        continue
                    items.append(row)
                    if len(items) >= limit:
                        return sorted(items, key=lambda x: x.get("ts", ""), reverse=True)
        except Exception:
            continue
    return sorted(items, key=lambda x: x.get("ts", ""), reverse=True)


def get_run_events(run_id: str) -> list[dict]:
    _ensure_dirs()
    items: list[dict] = []
    files = sorted(RUNS_DIR.glob("*.jsonl"), reverse=True)
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("run_id") == run_id:
                        items.append(row)
        except Exception:
            continue
    return sorted(items, key=lambda x: x.get("ts", ""))


def get_task_status(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        return {"found": False, "error": "task not found"}

    state = _load_state().get("tasks", {}).get(task_id, {})
    return {
        "found": True,
        "task_id": task_id,
        "enabled": bool(task.get("metadata", {}).get("enabled", True)),
        "paused": bool(task.get("spec", {}).get("paused", False)),
        "valid": bool(task.get("_valid", False)),
        "errors": task.get("_errors", []),
        "runtime": state,
    }


def task_from_api_payload(payload: dict, task_id: str | None = None) -> dict:
    payload = _fill_defaults(payload or {})
    if task_id:
        payload.setdefault("metadata", {})
        payload["metadata"]["id"] = task_id
    if not payload.get("metadata", {}).get("id"):
        name = payload.get("metadata", {}).get("name", "task")
        payload["metadata"]["id"] = _slug(name)
    return payload


def api_list_tasks() -> list[dict]:
    tasks = list_tasks(include_invalid=True)
    out = []
    for task in tasks:
        item = _safe_task_for_api(task)
        item["_valid"] = task.get("_valid", False)
        item["_errors"] = task.get("_errors", [])
        out.append(item)
    return out


def cli_main(argv: list[str] | None = None) -> int:
    _ensure_dirs()
    parser = argparse.ArgumentParser(description="Cron Manager")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list-tasks")

    v = sub.add_parser("validate")
    v.add_argument("yaml_path")

    a = sub.add_parser("apply")
    a.add_argument("yaml_path")

    r = sub.add_parser("run-task")
    r.add_argument("task_id")
    r.add_argument("--trigger", default="manual")

    p = sub.add_parser("pause")
    p.add_argument("task_id")

    rs = sub.add_parser("resume")
    rs.add_argument("task_id")

    d = sub.add_parser("delete")
    d.add_argument("task_id")

    st = sub.add_parser("status")
    st.add_argument("task_id")

    sub.add_parser("sync")
    sub.add_parser("scheduler-status")
    sub.add_parser("backends-status")

    args = parser.parse_args(argv)

    if args.cmd == "list-tasks":
        print(json.dumps(api_list_tasks(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "validate":
        task = load_task_from_file(Path(args.yaml_path))
        errs = validate_task(task)
        if errs:
            print(json.dumps({"valid": False, "errors": errs}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps({"valid": True}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "apply":
        task = load_task_from_file(Path(args.yaml_path))
        saved = save_task(task)
        print(json.dumps({"success": True, "task_id": saved["metadata"]["id"]}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "run-task":
        result = run_task(args.task_id, trigger=args.trigger)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 3
    if args.cmd == "pause":
        print(json.dumps(pause_task(args.task_id), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "resume":
        print(json.dumps(resume_task(args.task_id), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "delete":
        print(json.dumps(delete_task(args.task_id), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "status":
        print(json.dumps(get_task_status(args.task_id), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "sync":
        print(json.dumps(sync_all_tasks(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "scheduler-status":
        print(json.dumps(get_scheduler_status(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "backends-status":
        print(json.dumps(get_backends_status(), ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
