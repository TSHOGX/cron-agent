#!/usr/bin/env python3
"""Cron Manager: YAML task registry + dual-mode executors + raw trace logs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
import shlex

import process_manager
import storage_paths

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

BASE_DIR = Path(__file__).parent
TASKS_DIR = storage_paths.get_data_dir("tasks")
TASK_TEMPLATES_DIR = BASE_DIR / "docs" / "task_templates"
MARKER_BEGIN = "# >>> CRON_AGENT_MANAGED BEGIN >>>"
MARKER_END = "# <<< CRON_AGENT_MANAGED END <<<"
DEFAULT_TIMEZONE = "Asia/Shanghai"
TMUX_SESSION_PREFIX = "cronmgr_"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ensure_dirs() -> None:
    storage_paths.migrate_legacy_data_once()
    storage_paths.ensure_data_layout()
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    _bootstrap_tasks_once()
    _migrate_agent_provider_values_once()


def _state_file_path() -> Path:
    return storage_paths.get_data_dir("runtime") / "state.json"


def _trace_index_path(date: datetime | None = None) -> Path:
    if date is None:
        date = datetime.now()
    return storage_paths.get_data_dir("logs") / "trace_index" / f"{date.strftime('%Y-%m-%d')}.jsonl"


def _bootstrap_tasks_once() -> None:
    """Populate local task directory from repo tasks/templates when empty."""
    sentinel = storage_paths.get_data_dir("runtime") / ".tasks_bootstrapped_v1"
    if sentinel.exists():
        return

    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    if any(TASKS_DIR.glob("*.yaml")):
        sentinel.write_text("done\n", encoding="utf-8")
        return

    copied = 0
    # Legacy in-repo task definitions.
    legacy_dir = BASE_DIR / "tasks"
    if legacy_dir.exists():
        for src in sorted(legacy_dir.glob("*.yaml")):
            dst = TASKS_DIR / src.name
            if dst.exists():
                continue
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            copied += 1

    # Tracked templates for clean installs.
    if copied == 0 and TASK_TEMPLATES_DIR.exists():
        for src in sorted(TASK_TEMPLATES_DIR.glob("*.yaml")):
            dst = TASKS_DIR / src.name
            if dst.exists():
                continue
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            copied += 1

    report = {
        "copied": copied,
        "task_dir": str(TASKS_DIR),
        "legacy_source": str(legacy_dir),
        "template_source": str(TASK_TEMPLATES_DIR),
    }
    with open(storage_paths.get_data_dir("runtime") / "tasks_bootstrap_report_v1.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    sentinel.write_text("done\n", encoding="utf-8")


def _migrate_agent_provider_values_once() -> None:
    """One-time migration from legacy agent providers and logging config."""
    sentinel = storage_paths.get_data_dir("runtime") / ".agent_provider_migrated_v1"
    if sentinel.exists():
        return

    provider_map = {
        "codex_cli": "codex",
        "claude_agent_sdk": "claude",
    }
    migrated = 0
    report: list[dict[str, Any]] = []

    for path in sorted(TASKS_DIR.glob("*.yaml")):
        try:
            data = _yaml_load(path)
            if not isinstance(data, dict):
                continue
            spec = data.get("spec")
            if not isinstance(spec, dict):
                continue
            changed = False

            mode_cfg = spec.get("modeConfig")
            if isinstance(mode_cfg, dict):
                agent_cfg = mode_cfg.get("agent")
                if isinstance(agent_cfg, dict):
                    raw_provider = agent_cfg.get("provider")
                    mapped = provider_map.get(str(raw_provider), None)
                    if mapped:
                        agent_cfg["provider"] = mapped
                        changed = True

            if "logging" in spec:
                spec.pop("logging", None)
                changed = True

            if changed:
                _yaml_dump(path, data)
                migrated += 1
                report.append({"task_file": str(path), "status": "migrated"})
        except Exception as e:
            report.append({"task_file": str(path), "status": "error", "error": str(e)})

    _save_json_file(
        storage_paths.get_data_dir("runtime") / "agent_provider_migration_v1.json",
        {"migrated": migrated, "report": report},
    )
    sentinel.write_text("done\n", encoding="utf-8")


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
    return _load_json_file(_state_file_path(), {"tasks": {}, "runs": {}})


def _save_state(state: dict) -> None:
    _save_json_file(_state_file_path(), state)


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
    agent_cfg.setdefault("provider", "codex")
    agent_cfg.setdefault("model", "gpt-5-codex")
    agent_cfg.setdefault("sandboxMode", "workspace-write")
    agent_cfg.setdefault("systemPrompt", "")
    fallback_cfg = agent_cfg.setdefault("fallback", {})
    fallback_cfg.setdefault("enabled", False)
    fallback_cfg.setdefault("order", ["codex", "claude", "gemini", "pi", "opencode"])
    fallback_cfg.setdefault("onErrors", ["rate_limit", "quota", "provider_unavailable"])
    trace_cfg = agent_cfg.setdefault("trace", {})
    trace_cfg.setdefault("enabled", True)
    trace_cfg.setdefault("maxEventBytes", 262144)

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
                # tmux backend no longer acts as a scheduler loop.
                # Keep task valid for compatibility; schedule fields are ignored.
                pass

            if schedule.get("misfirePolicy", "run_once") not in ("run_once", "skip"):
                errors.append("spec.schedule.misfirePolicy must be run_once or skip")

        if mode == "agent":
            mode_cfg = spec.get("modeConfig", {})
            agent_cfg = mode_cfg.get("agent", {}) if isinstance(mode_cfg, dict) else {}
            if not isinstance(agent_cfg, dict):
                errors.append("spec.modeConfig.agent must be an object when spec.mode=agent")
            else:
                provider = agent_cfg.get("provider", "codex")
                if provider not in ("claude", "codex", "gemini", "opencode", "pi"):
                    errors.append("spec.modeConfig.agent.provider must be one of claude|codex|gemini|opencode|pi")
                if agent_cfg.get("commandTemplate"):
                    errors.append("spec.modeConfig.agent.commandTemplate is deprecated; use cliCommand/cliArgs")

            prompt = spec.get("input", {}).get("prompt", "")
            if isinstance(prompt, str):
                prompt_lower = prompt.lower()
                if "scheduler.py capture" in prompt_lower or "scheduler.py summary" in prompt_lower:
                    errors.append("spec.input.prompt must not call scheduler.py capture|summary in mode=agent")

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


def sync_tmux_tasks() -> dict:
    existing = _list_tmux_sessions()
    managed_existing = [s for s in existing if s.startswith(TMUX_SESSION_PREFIX)]
    for session in managed_existing:
        _kill_tmux_session(session)
    tasks = list_tasks(include_invalid=False)
    desired_tasks = [t for t in tasks if _is_task_enabled(t) and _task_backend(t) == "tmux"]

    return {
        "success": True,
        "backend": "tmux",
        "task_count": len(desired_tasks),
        "started": 0,
        "cleaned_sessions": len(managed_existing),
        "note": "tmux backend is run-once only; scheduler loop has been removed",
        "errors": [],
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


def _safe_task_for_api(task: dict) -> dict:
    return {k: v for k, v in task.items() if not k.startswith("_")}


def _format_template(template: str, task: dict, run_id: str) -> str:
    task_id = task["metadata"]["id"]
    return template.format(task_id=task_id, run_id=run_id, date=_today_str())


def _display_path(path: Path) -> str:
    output_root = storage_paths.get_output_root()
    try:
        return str(path.relative_to(output_root))
    except ValueError:
        return str(path)


def _append_trace_index(row: dict[str, Any]) -> None:
    path = _trace_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _execute_via_process(task: dict, run_id: str, prompt: str, timeout_seconds: int) -> tuple[bool, str, dict]:
    spec = task.get("spec", {})
    task_id = task.get("metadata", {}).get("id", "unknown")
    mode = spec.get("mode")

    def start_and_wait_agent(agent_cfg: dict) -> tuple[bool, str, dict]:
        working_dir = Path(spec.get("execution", {}).get("workingDirectory", "."))
        cwd = storage_paths.get_repo_root() / working_dir
        start_res = process_manager.start_agent_process(
            task_id=task_id,
            run_id=run_id,
            cfg=agent_cfg if isinstance(agent_cfg, dict) else {},
            prompt=prompt,
            cwd=str(cwd),
            timeout_seconds=timeout_seconds,
        )
        if not start_res.get("success"):
            return False, "", {"error": start_res.get("error", "failed to start process"), "process_id": None}
        process_id = str(start_res["process_id"])
        wait_res = process_manager.wait_process(process_id, timeout_seconds=max(timeout_seconds + 10, 30))
        if not wait_res.get("found"):
            return False, "", {"error": wait_res.get("error", "process not found"), "process_id": process_id}
        if not wait_res.get("done"):
            return False, "", {"error": "process wait timeout", "process_id": process_id}
        process_meta = process_manager.poll_process(process_id)
        text = process_manager.get_process_output(process_id) or ""
        ok = process_meta.get("status") == "succeeded"
        meta = {
            "process_id": process_id,
            "status": process_meta.get("status"),
            "returncode": process_meta.get("returncode"),
            "error": process_meta.get("error"),
            "stdout_bytes": process_meta.get("stdout_bytes", 0),
            "stderr_bytes": process_meta.get("stderr_bytes", 0),
            "elapsed": process_meta.get("elapsed_seconds"),
            "provider": agent_cfg.get("provider"),
        }
        return ok, text, meta

    if mode != "agent":
        llm_cfg = spec.get("modeConfig", {}).get("llm", {}) if isinstance(spec.get("modeConfig"), dict) else {}
        start_res = process_manager.start_llm_process(
            task_id=task_id,
            run_id=run_id,
            llm_cfg=llm_cfg if isinstance(llm_cfg, dict) else {},
            prompt=prompt,
            timeout_seconds=timeout_seconds,
        )
        if not start_res.get("success"):
            return False, "", {"error": start_res.get("error", "failed to start process"), "process_id": None}
        process_id = str(start_res["process_id"])
        wait_res = process_manager.wait_process(process_id, timeout_seconds=max(timeout_seconds + 10, 30))
        if not wait_res.get("found"):
            return False, "", {"error": wait_res.get("error", "process not found"), "process_id": process_id}
        if not wait_res.get("done"):
            return False, "", {"error": "process wait timeout", "process_id": process_id}
        process_meta = process_manager.poll_process(process_id)
        text = process_manager.get_process_output(process_id) or ""
        ok = process_meta.get("status") == "succeeded"
        meta = {
            "process_id": process_id,
            "status": process_meta.get("status"),
            "returncode": process_meta.get("returncode"),
            "error": process_meta.get("error"),
            "stdout_bytes": process_meta.get("stdout_bytes", 0),
            "stderr_bytes": process_meta.get("stderr_bytes", 0),
            "elapsed": process_meta.get("elapsed_seconds"),
        }
        return ok, text, meta

    mode_cfg = spec.get("modeConfig", {})
    agent_cfg = mode_cfg.get("agent", {}) if isinstance(mode_cfg, dict) else {}
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}

    fallback_cfg = agent_cfg.get("fallback", {}) if isinstance(agent_cfg.get("fallback"), dict) else {}
    fallback_enabled = bool(fallback_cfg.get("enabled", False))
    base_provider = str(agent_cfg.get("provider", "codex"))
    fallback_order = fallback_cfg.get("order", ["codex", "claude", "gemini", "pi", "opencode"])
    on_errors = fallback_cfg.get("onErrors", ["rate_limit", "quota", "provider_unavailable"])
    providers = [base_provider]
    if fallback_enabled and isinstance(fallback_order, list):
        for p in fallback_order:
            ps = str(p).strip()
            if ps and ps not in providers:
                providers.append(ps)

    def classify_error(err: str) -> str:
        e = (err or "").lower()
        if "rate limit" in e or "429" in e:
            return "rate_limit"
        if "usage limit" in e or "quota" in e:
            return "quota"
        if "overloaded" in e or "unavailable" in e or "cli not found" in e:
            return "provider_unavailable"
        return "other"

    fallback_attempts: list[dict[str, Any]] = []
    for idx, provider in enumerate(providers):
        cfg_try = dict(agent_cfg)
        cfg_try["provider"] = provider
        ok, text, meta = start_and_wait_agent(cfg_try)
        meta["fallback_index"] = idx
        fallback_attempts.append({"provider": provider, "ok": ok, "process_id": meta.get("process_id"), "error": meta.get("error")})
        if ok:
            meta["fallback_attempts"] = fallback_attempts
            return True, text, meta
        if not fallback_enabled:
            meta["fallback_attempts"] = fallback_attempts
            return False, text, meta
        if idx >= len(providers) - 1:
            meta["fallback_attempts"] = fallback_attempts
            return False, text, meta
        error_type = classify_error(str(meta.get("error") or ""))
        allowed = set(str(x) for x in on_errors) if isinstance(on_errors, list) else set()
        if error_type not in allowed:
            meta["fallback_attempts"] = fallback_attempts
            return False, text, meta
    return False, "", {"error": "executor failed", "process_id": None, "fallback_attempts": fallback_attempts}


def _write_output(task: dict, run_id: str, text: str) -> str:
    output_cfg = task["spec"].get("output", {})
    template = output_cfg.get("pathTemplate", "artifacts/{task_id}/{run_id}/result.md")
    resolved = _format_template(template, task, run_id)
    path = storage_paths.resolve_data_path(resolved, default_base_kind="artifacts")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return _display_path(path)


def _mark_task_running(task_id: str, run_id: str) -> bool:
    state = _load_state()
    info = state.setdefault("tasks", {}).setdefault(task_id, {})
    if info.get("running", False):
        return False
    info["running"] = True
    info["current_run_id"] = run_id
    info["current_process_id"] = None
    info["started_at"] = _now_iso()
    _save_state(state)
    return True


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _clear_stale_running_lock(task_id: str, stale_error: str) -> None:
    state = _load_state()
    info = state.setdefault("tasks", {}).setdefault(task_id, {})
    info["running"] = False
    info["current_run_id"] = None
    info["current_process_id"] = None
    info["last_status"] = "failed"
    info["last_error"] = stale_error
    info["last_finished_at"] = _now_iso()
    _save_state(state)


def _mark_task_finished(task_id: str, status: str, run_id: str, error: str | None = None) -> None:
    state = _load_state()
    info = state.setdefault("tasks", {}).setdefault(task_id, {})
    info["running"] = False
    info["current_run_id"] = None
    info["current_process_id"] = None
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


def _mark_task_process(task_id: str, run_id: str, process_id: str | None) -> None:
    state = _load_state()
    info = state.setdefault("tasks", {}).setdefault(task_id, {})
    if info.get("current_run_id") != run_id:
        info["current_run_id"] = run_id
    info["current_process_id"] = process_id
    info["running"] = bool(process_id)
    info["started_at"] = info.get("started_at") or _now_iso()
    _save_state(state)


def _run_response(
    *,
    success: bool,
    task_id: str,
    run_id: str | None = None,
    process_id: str | None = None,
    status: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    output_path: str | None = None,
    trace_path: str | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "task_id": task_id,
        "run_id": run_id,
        "process_id": process_id,
        "status": status,
        "error": error,
        "error_code": error_code,
        "output_path": output_path,
        "trace_path": trace_path,
    }


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


def _prepare_run_context(task_id: str, run_id: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    _ensure_dirs()
    task = get_task(task_id)
    if not task:
        return None, _run_response(success=False, task_id=task_id, status="failed", error=f"task not found: {task_id}", error_code="task_not_found")
    if not task.get("_valid"):
        return None, _run_response(
            success=False,
            task_id=task_id,
            status="failed",
            error=f"task invalid: {'; '.join(task.get('_errors', []))}",
            error_code="task_invalid",
        )
    if not _is_task_enabled(task):
        return None, _run_response(success=False, task_id=task_id, status="failed", error="task disabled or paused", error_code="task_disabled")

    run_id = run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    spec = task["spec"]
    max_concurrency = int(spec["schedule"].get("maxConcurrency", 1))
    if max_concurrency <= 1:
        state = _load_state()
        info = state.setdefault("tasks", {}).setdefault(task_id, {})
        if info.get("running", False):
            started_at = _parse_iso(info.get("started_at"))
            timeout_seconds = int(spec["execution"].get("timeoutSeconds", 600))
            stale_after = max(60, timeout_seconds + 30)
            if started_at is None:
                _clear_stale_running_lock(task_id, "stale running lock recovered (invalid started_at)")
            else:
                age_seconds = (datetime.now().astimezone() - started_at).total_seconds()
                if age_seconds > stale_after:
                    _clear_stale_running_lock(task_id, f"stale running lock recovered (age={int(age_seconds)}s)")
        lock_acquired = _mark_task_running(task_id, run_id)
        if not lock_acquired:
            return None, _run_response(
                success=False,
                task_id=task_id,
                run_id=run_id,
                status="failed",
                error="task already running",
                error_code="task_running",
            )

    prompt = _prepare_prompt(task)
    timeout_seconds = int(spec["execution"].get("timeoutSeconds", 600))
    retry_cfg = spec["execution"].get("retry", {})
    max_attempts = max(1, int(retry_cfg.get("maxAttempts", 1)))
    backoff = max(0, int(retry_cfg.get("backoffSeconds", 0)))
    ctx = {
        "task": task,
        "task_id": task_id,
        "run_id": run_id,
        "spec": spec,
        "prompt": prompt,
        "timeout_seconds": timeout_seconds,
        "max_attempts": max_attempts,
        "backoff": backoff,
        "started_at": _now_iso(),
        "start_time": time.time(),
    }
    return ctx, None


def _execute_run_context(ctx: dict[str, Any], trigger: str) -> dict:
    task = ctx["task"]
    task_id = ctx["task_id"]
    run_id = ctx["run_id"]
    spec = ctx["spec"]
    prompt = ctx["prompt"]
    timeout_seconds = ctx["timeout_seconds"]
    max_attempts = ctx["max_attempts"]
    backoff = ctx["backoff"]
    started_at = ctx["started_at"]
    start = ctx["start_time"]

    last_error = None
    final_text = ""
    final_meta: dict[str, Any] = {}
    trace_path = ""
    process_id: str | None = None

    try:
        for attempt in range(1, max_attempts + 1):
            ok, text, meta = _execute_via_process(task, run_id, prompt, timeout_seconds)
            if meta.get("process_id"):
                process_id = str(meta.get("process_id"))
                _mark_task_process(task_id, run_id, process_id)
            trace_path = str(meta.get("trace_path") or trace_path)

            if ok:
                final_text = text
                final_meta = meta
                break

            last_error = meta.get("error") or meta.get("stderr") or "executor failed"
            if attempt < max_attempts and backoff > 0:
                time.sleep(backoff)
    except Exception as e:
        last_error = f"unexpected error: {e}"
        elapsed = round(time.time() - start, 3)
        _append_trace_index(
            {
                "ts": _now_iso(),
                "run_id": run_id,
                "task_id": task_id,
                "provider": spec.get("modeConfig", {}).get("agent", {}).get("provider"),
                "status": "failed",
                "trigger": trigger,
                "started_at": started_at,
                "finished_at": _now_iso(),
                "elapsed_seconds": elapsed,
                "process_id": process_id,
                "trace_path": trace_path,
                "output_path": None,
                "error": last_error,
            }
        )
        _mark_task_finished(task_id, "failed", run_id, error=last_error)
        return _run_response(
            success=False,
            task_id=task_id,
            run_id=run_id,
            process_id=process_id,
            status="failed",
            error=last_error,
            error_code="unexpected_error",
        )

    duration = round(time.time() - start, 3)
    if final_text:
        output_path = _write_output(task, run_id, final_text)
        _append_trace_index(
            {
                "ts": _now_iso(),
                "run_id": run_id,
                "task_id": task_id,
                "provider": spec.get("modeConfig", {}).get("agent", {}).get("provider") if spec.get("mode") == "agent" else "llm",
                "status": "succeeded",
                "trigger": trigger,
                "started_at": started_at,
                "finished_at": _now_iso(),
                "elapsed_seconds": duration,
                "process_id": process_id,
                "trace_path": trace_path,
                "output_path": output_path,
                "error": None,
                "meta": final_meta,
            }
        )
        _mark_task_finished(task_id, "succeeded", run_id)
        return _run_response(
            success=True,
            task_id=task_id,
            run_id=run_id,
            process_id=process_id,
            status="succeeded",
            output_path=output_path,
            trace_path=_display_path(Path(trace_path)) if trace_path else None,
        )

    _append_trace_index(
        {
            "ts": _now_iso(),
            "run_id": run_id,
            "task_id": task_id,
            "provider": spec.get("modeConfig", {}).get("agent", {}).get("provider") if spec.get("mode") == "agent" else "llm",
            "status": "failed",
            "trigger": trigger,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "elapsed_seconds": duration,
            "process_id": process_id,
            "trace_path": trace_path,
            "output_path": None,
            "error": last_error,
            "meta": final_meta,
        }
    )
    _mark_task_finished(task_id, "failed", run_id, error=str(last_error))
    return _run_response(
        success=False,
        task_id=task_id,
        run_id=run_id,
        process_id=process_id,
        status="failed",
        error=str(last_error),
        error_code="execution_failed",
        trace_path=_display_path(Path(trace_path)) if trace_path else None,
    )


def run_task(task_id: str, trigger: str = "manual") -> dict:
    ctx, err = _prepare_run_context(task_id)
    if err:
        return err
    return _execute_run_context(ctx, trigger)


def run_task_async(task_id: str, trigger: str = "api") -> dict:
    ctx, err = _prepare_run_context(task_id)
    if err:
        return err
    run_id = str(ctx["run_id"])
    t = threading.Thread(target=_execute_run_context, args=(ctx, trigger), daemon=True)
    try:
        t.start()
    except Exception as e:
        _mark_task_finished(task_id, "failed", run_id, error=f"failed to start async run thread: {e}")
        return _run_response(
            success=False,
            task_id=task_id,
            run_id=run_id,
            status="failed",
            error=f"failed to start async run thread: {e}",
            error_code="async_start_failed",
        )
    return _run_response(success=True, task_id=task_id, run_id=run_id, status="running")


def list_runs(task_id: str | None = None, limit: int = 100) -> list[dict]:
    _ = (task_id, limit)
    return []


def get_run_events(run_id: str) -> list[dict]:
    _ = run_id
    return []


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


def _deep_merge_dict(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge_dict(dst[k], v)
        else:
            dst[k] = v
    return dst


def get_task_settings(task_id: str) -> dict | None:
    task = get_task(task_id)
    if not task:
        return None
    spec = task.get("spec", {})
    return {
        "mode": spec.get("mode"),
        "runBackend": spec.get("runBackend"),
        "schedule": spec.get("schedule", {}),
        "input": {
            "prompt": spec.get("input", {}).get("prompt", ""),
        },
        "execution": spec.get("execution", {}),
        "modeConfig": spec.get("modeConfig", {}),
        "output": spec.get("output", {}),
    }


def update_task_settings(task_id: str, payload: dict) -> dict:
    task = get_task(task_id)
    if not task:
        return {"success": False, "error": "task not found"}
    if not isinstance(payload, dict):
        return {"success": False, "error": "payload must be object"}

    task_copy = {k: v for k, v in task.items() if not k.startswith("_")}
    spec = task_copy.setdefault("spec", {})

    updatable = ("mode", "runBackend", "schedule", "input", "execution", "modeConfig", "output")
    for key in updatable:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, dict):
            current = spec.setdefault(key, {})
            if not isinstance(current, dict):
                spec[key] = value
            else:
                _deep_merge_dict(current, value)
        else:
            spec[key] = value

    saved = save_task(task_copy)
    return {"success": True, "task": _safe_task_for_api(saved), "settings": get_task_settings(task_id)}


def api_process_start(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"success": False, "error": "payload must be object"}
    task_id = str(payload.get("task_id") or "").strip()
    if task_id:
        requested_run_id = str(payload.get("run_id") or "").strip() or None
        ctx, err = _prepare_run_context(task_id, run_id=requested_run_id)
        if err:
            return {"success": False, "error": err.get("error", "failed to prepare run"), "error_code": err.get("error_code")}
        task = ctx["task"]
        run_id = str(ctx["run_id"])
        prompt = str(payload.get("prompt") or ctx["prompt"])
        timeout_seconds = int(payload.get("timeout_seconds") or ctx["timeout_seconds"])
        mode = str(payload.get("mode") or task.get("spec", {}).get("mode", "agent"))
        if mode == "agent":
            cfg = task.get("spec", {}).get("modeConfig", {}).get("agent", {}) or {}
            working_dir = Path(task.get("spec", {}).get("execution", {}).get("workingDirectory", "."))
            cwd = storage_paths.get_repo_root() / working_dir
            res = process_manager.start_agent_process(
                task_id=task_id,
                run_id=run_id,
                cfg=cfg if isinstance(cfg, dict) else {},
                prompt=prompt,
                cwd=str(cwd),
                timeout_seconds=timeout_seconds,
            )
        else:
            llm_cfg = task.get("spec", {}).get("modeConfig", {}).get("llm", {}) or {}
            res = process_manager.start_llm_process(
                task_id=task_id,
                run_id=run_id,
                llm_cfg=llm_cfg if isinstance(llm_cfg, dict) else {},
                prompt=prompt,
                timeout_seconds=timeout_seconds,
            )
        if res.get("success"):
            _mark_task_process(task_id, run_id, str(res.get("process_id")))
        else:
            _mark_task_finished(task_id, "failed", run_id, error=str(res.get("error", "process start failed")))
        return res

    mode = str(payload.get("mode") or "agent")
    run_id = str(payload.get("run_id") or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}")
    prompt = str(payload.get("prompt") or "")
    timeout_seconds = int(payload.get("timeout_seconds") or 600)
    if mode == "agent":
        cfg = payload.get("agent", {}) if isinstance(payload.get("agent"), dict) else {}
        cwd = str(payload.get("workdir") or storage_paths.get_repo_root())
        return process_manager.start_agent_process(
            task_id=str(payload.get("task_id") or "adhoc"),
            run_id=run_id,
            cfg=cfg,
            prompt=prompt,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
    llm_cfg = payload.get("llm", {}) if isinstance(payload.get("llm"), dict) else {}
    return process_manager.start_llm_process(
        task_id=str(payload.get("task_id") or "adhoc"),
        run_id=run_id,
        llm_cfg=llm_cfg,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
    )


def api_process_list(task_id: str | None = None, run_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
    return process_manager.list_processes(task_id=task_id, run_id=run_id, status=status, limit=limit)


def api_process_poll(process_id: str) -> dict:
    return process_manager.poll_process(process_id)


def api_process_log(process_id: str, offset: int = 0, limit: int = 200) -> dict:
    return process_manager.read_process_log(process_id, offset=offset, limit=limit)


def api_process_write(process_id: str, data: str, submit: bool = False) -> dict:
    return process_manager.write_process(process_id, data, submit=submit)


def api_process_kill(process_id: str, sig: str = "TERM") -> dict:
    return process_manager.kill_process(process_id, sig=sig)


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
    sub.add_parser("backends-status")
    pl = sub.add_parser("process-list")
    pl.add_argument("--task-id", default=None)
    pl.add_argument("--run-id", default=None)
    pl.add_argument("--status", default=None)
    pl.add_argument("--limit", type=int, default=100)

    pp = sub.add_parser("process-poll")
    pp.add_argument("process_id")

    pg = sub.add_parser("process-log")
    pg.add_argument("process_id")
    pg.add_argument("--offset", type=int, default=0)
    pg.add_argument("--limit", type=int, default=200)

    pk = sub.add_parser("process-kill")
    pk.add_argument("process_id")
    pk.add_argument("--signal", default="TERM")

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
    if args.cmd == "backends-status":
        print(json.dumps(get_backends_status(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "process-list":
        print(json.dumps(api_process_list(task_id=args.task_id, run_id=args.run_id, status=args.status, limit=args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "process-poll":
        result = api_process_poll(args.process_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("found") else 3
    if args.cmd == "process-log":
        result = api_process_log(args.process_id, offset=args.offset, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("found") else 3
    if args.cmd == "process-kill":
        result = api_process_kill(args.process_id, sig=args.signal)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 3

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
