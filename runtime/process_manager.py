from __future__ import annotations

import json
import os
import pty
import select
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime import storage_paths

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - optional dependency at runtime
    OpenAI = None  # type: ignore

_LOCK = threading.RLock()
_SESSIONS: dict[str, "ProcessSession"] = {}
_RECOVERED = False


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _state_path() -> Path:
    return storage_paths.get_data_dir("runtime") / "state.json"


def _log_path(process_id: str) -> Path:
    return storage_paths.get_data_dir("logs") / "process" / f"{process_id}.jsonl"


def _base_state() -> dict[str, Any]:
    return {
        "tasks": {},
        "runs": {},
        "processes": {},
        "run_to_process": {},
        "task_to_active_process": {},
    }


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _base_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _base_state()
        out = _base_state()
        out.update(data)
        return out
    except Exception:
        return _base_state()


def _save_state(data: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _set_state_process(session: "ProcessSession") -> None:
    with _LOCK:
        state = _load_state()
        state.setdefault("processes", {})
        state.setdefault("run_to_process", {})
        state.setdefault("task_to_active_process", {})
        state.setdefault("tasks", {})
        state.setdefault("runs", {})
        state["processes"][session.process_id] = session.to_state()
        if session.run_id:
            state["run_to_process"][session.run_id] = session.process_id
        if session.task_id:
            task_info = state["tasks"].setdefault(session.task_id, {})
            if session.status in ("running", "starting"):
                state["task_to_active_process"][session.task_id] = session.process_id
                task_info["running"] = True
                task_info["current_run_id"] = session.run_id
                task_info["current_process_id"] = session.process_id
                task_info["started_at"] = task_info.get("started_at") or _now_iso()
            elif state["task_to_active_process"].get(session.task_id) == session.process_id:
                state["task_to_active_process"][session.task_id] = None
                if task_info.get("current_process_id") == session.process_id:
                    task_info["running"] = False
                    task_info["current_process_id"] = None
                    task_info["last_run_id"] = session.run_id
                    if session.status == "succeeded":
                        task_info["last_status"] = "succeeded"
                        task_info.pop("last_error", None)
                    else:
                        task_info["last_status"] = "failed"
                        if session.error:
                            task_info["last_error"] = session.error
                    task_info["last_finished_at"] = _now_iso()
            state["runs"][session.run_id] = {
                "task_id": session.task_id,
                "process_id": session.process_id,
                "status": "succeeded" if session.status == "succeeded" else ("running" if session.status in ("running", "starting") else "failed"),
                "finished_at": _now_iso() if session.status not in ("running", "starting") else None,
                "error": None if session.status == "succeeded" else session.error,
            }
        _save_state(state)


def _mark_lost_running_processes_once() -> None:
    global _RECOVERED
    with _LOCK:
        if _RECOVERED:
            return
        state = _load_state()
        changed = False
        processes = state.setdefault("processes", {})
        for pid, meta in list(processes.items()):
            if not isinstance(meta, dict):
                continue
            status = str(meta.get("status", ""))
            if status in ("running", "starting"):
                meta["status"] = "failed"
                meta["error"] = "process lost after service restart"
                meta["ended_at"] = _now_iso()
                meta["updated_at"] = _now_iso()
                processes[pid] = meta
                task_id = meta.get("task_id")
                if isinstance(task_id, str):
                    tasks = state.setdefault("tasks", {})
                    task_info = tasks.setdefault(task_id, {})
                    if task_info.get("current_process_id") == pid:
                        task_info["current_process_id"] = None
                    if task_info.get("current_run_id") == meta.get("run_id"):
                        task_info["running"] = False
                        task_info["last_status"] = "failed"
                        task_info["last_error"] = "process lost after service restart"
                        task_info["last_finished_at"] = _now_iso()
                changed = True
        if changed:
            _save_state(state)
        _RECOVERED = True


def _append_log(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _get_secret_from_auth_ref(auth_ref: str | None) -> str | None:
    if not auth_ref:
        return None
    if auth_ref.startswith("env:"):
        return os.getenv(auth_ref.split(":", 1)[1])
    return auth_ref


def _build_agent_cmd(cfg: dict[str, Any], final_prompt: str) -> list[str]:
    provider = str(cfg.get("provider", "codex")).strip().lower()
    model = str(cfg.get("model", "")).strip()
    sandbox_mode = str(cfg.get("sandboxMode", "workspace-write")).strip() or "workspace-write"
    cli_args = cfg.get("cliArgs", []) or []

    explicit = cfg.get("cliCommand")
    if isinstance(explicit, str) and explicit.strip():
        base = shlex.split(explicit)
        if isinstance(cli_args, list):
            base.extend([str(x) for x in cli_args])
        base.append(final_prompt)
        return base

    if provider == "codex":
        cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", sandbox_mode]
        if model:
            cmd.extend(["--model", model])
        if isinstance(cli_args, list):
            cmd.extend([str(x) for x in cli_args])
        cmd.append(final_prompt)
        return cmd

    if provider == "claude":
        cmd = ["claude", "-p", "--output-format", "text", "--permission-mode", "acceptEdits"]
        if model:
            cmd.extend(["--model", model])
        if isinstance(cli_args, list):
            cmd.extend([str(x) for x in cli_args])
        cmd.append(final_prompt)
        return cmd

    if provider == "gemini":
        cmd = ["gemini", "--approval-mode", "yolo"]
        if model:
            cmd.extend(["--model", model])
        if isinstance(cli_args, list):
            cmd.extend([str(x) for x in cli_args])
        cmd.extend(["-p", final_prompt])
        return cmd

    if provider == "opencode":
        cmd = ["opencode", "run"]
        if model:
            cmd.extend(["--model", model])
        if isinstance(cli_args, list):
            cmd.extend([str(x) for x in cli_args])
        cmd.append(final_prompt)
        return cmd

    if provider == "pi":
        cmd = ["pi", "-p", "--mode", "text"]
        if model:
            cmd.extend(["--model", model])
        if isinstance(cli_args, list):
            cmd.extend([str(x) for x in cli_args])
        cmd.append(final_prompt)
        return cmd

    cmd = [provider]
    if model:
        cmd.extend(["--model", model])
    if isinstance(cli_args, list):
        cmd.extend([str(x) for x in cli_args])
    cmd.append(final_prompt)
    return cmd


@dataclass
class ProcessSession:
    process_id: str
    task_id: str
    run_id: str
    mode: str
    provider: str
    model: str
    cwd: str
    timeout_seconds: int
    interactive: bool
    status: str = "starting"
    started_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    ended_at: str | None = None
    returncode: int | None = None
    error: str | None = None
    output_text: str = ""
    log_seq: int = 0
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    log_path: str = ""
    pid: int | None = None
    process: subprocess.Popen[bytes] | None = None
    thread: threading.Thread | None = None
    master_fd: int = -1
    stderr_fd: int = -1
    done_event: threading.Event = field(default_factory=threading.Event)

    def to_state(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "interactive": self.interactive,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "returncode": self.returncode,
            "error": self.error,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "log_path": self.log_path,
            "pid": self.pid,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "returncode": self.returncode,
            "error": self.error,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "pid": self.pid,
        }

    def write_log(self, **payload: Any) -> None:
        self.log_seq += 1
        row = {
            "seq": self.log_seq,
            "ts": _now_iso(),
            "process_id": self.process_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "provider": self.provider,
        }
        row.update(payload)
        _append_log(Path(self.log_path), row)

    def mark_update(self) -> None:
        self.updated_at = _now_iso()
        _set_state_process(self)


def _decode_chunk(buf: bytes) -> str:
    if not buf:
        return ""
    return buf.decode("utf-8", errors="replace")


def _finalize(session: ProcessSession, status: str, error: str | None = None, returncode: int | None = None) -> None:
    with _LOCK:
        session.status = status
        session.error = error
        session.returncode = returncode
        session.ended_at = _now_iso()
        session.mark_update()
        session.done_event.set()


def _run_agent_session(session: ProcessSession, cmd: list[str], env: dict[str, str], final_prompt: str) -> None:
    start_ts = time.time()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    slave_fd = -1
    try:
        session.write_log(channel="stdin", io="write", transport="argv_prompt", content=final_prompt)
        master_fd, slave_fd = pty.openpty()
        session.master_fd = master_fd
        proc = subprocess.Popen(
            cmd,
            cwd=session.cwd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=os.setsid,
        )
        if proc.stderr is not None:
            session.stderr_fd = proc.stderr.fileno()
        session.process = proc
        session.pid = proc.pid
        session.status = "running"
        session.mark_update()
        session.write_log(channel="process", event="start", command=cmd, cwd=session.cwd, pid=proc.pid)
        os.close(slave_fd)
        slave_fd = -1

        deadline = time.time() + float(session.timeout_seconds)
        while True:
            if time.time() > deadline:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    pass
                time.sleep(0.2)
                try:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
                elapsed = round(time.time() - start_ts, 3)
                session.write_log(channel="process", event="timeout", elapsed_seconds=elapsed)
                _finalize(session, "timeout", error=f"process timeout after {session.timeout_seconds}s")
                return

            wait_timeout = max(0.05, min(0.25, deadline - time.time()))
            read_fds = [session.master_fd]
            if session.stderr_fd >= 0:
                read_fds.append(session.stderr_fd)
            ready, _, _ = select.select(read_fds, [], [], wait_timeout)
            if ready:
                if session.master_fd in ready:
                    try:
                        chunk = os.read(session.master_fd, 4096)
                    except OSError:
                        chunk = b""
                    if chunk:
                        text = _decode_chunk(chunk)
                        stdout_chunks.append(text)
                        session.stdout_bytes += len(chunk)
                        session.write_log(channel="stdout", io="chunk", content=text, bytes=len(chunk))
                if session.stderr_fd in ready:
                    try:
                        chunk = os.read(session.stderr_fd, 4096)
                    except OSError:
                        chunk = b""
                    if chunk:
                        text = _decode_chunk(chunk)
                        stderr_chunks.append(text)
                        session.stderr_bytes += len(chunk)
                        session.write_log(channel="stderr", io="chunk", content=text, bytes=len(chunk))

            rc = proc.poll()
            if rc is None:
                continue

            while True:
                try:
                    chunk = os.read(session.master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                text = _decode_chunk(chunk)
                stdout_chunks.append(text)
                session.stdout_bytes += len(chunk)
                session.write_log(channel="stdout", io="chunk", content=text, bytes=len(chunk))
            if session.stderr_fd >= 0:
                while True:
                    try:
                        chunk = os.read(session.stderr_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    text = _decode_chunk(chunk)
                    stderr_chunks.append(text)
                    session.stderr_bytes += len(chunk)
                    session.write_log(channel="stderr", io="chunk", content=text, bytes=len(chunk))
            break

        elapsed = round(time.time() - start_ts, 3)
        out_text = "".join(stdout_chunks).strip()
        err_text = "".join(stderr_chunks).strip()
        session.output_text = out_text if out_text else err_text
        session.write_log(channel="process", event="exit", returncode=proc.returncode, elapsed_seconds=elapsed)
        if proc.returncode != 0:
            _finalize(session, "failed", error="agent process exited non-zero", returncode=proc.returncode)
            return
        if not session.output_text:
            _finalize(session, "failed", error="empty agent response", returncode=proc.returncode)
            return
        _finalize(session, "succeeded", returncode=proc.returncode)
    except FileNotFoundError:
        session.write_log(channel="process", event="error", error=f"provider cli not found: {session.provider}")
        _finalize(session, "failed", error=f"provider cli not found: {session.provider}")
    except Exception as e:
        session.write_log(channel="process", event="error", error=str(e))
        _finalize(session, "failed", error=str(e))
    finally:
        if session.master_fd >= 0:
            try:
                os.close(session.master_fd)
            except OSError:
                pass
            session.master_fd = -1
        if slave_fd >= 0:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        if session.process is not None and session.process.stderr is not None:
            try:
                session.process.stderr.close()
            except Exception:
                pass
        session.process = None
        session.mark_update()


def _run_llm_session(session: ProcessSession, llm_cfg: dict[str, Any], prompt: str) -> None:
    start_ts = time.time()
    session.status = "running"
    session.mark_update()
    session.write_log(channel="process", event="start", mode="llm")
    try:
        if OpenAI is None:
            _finalize(session, "failed", error="openai sdk missing")
            return
        api_key = _get_secret_from_auth_ref(llm_cfg.get("authRef"))
        if not api_key:
            _finalize(session, "failed", error="Missing API key from spec.modeConfig.llm.authRef")
            return
        model = llm_cfg.get("model", "gpt-4o-mini")
        client = OpenAI(api_key=api_key, base_url=llm_cfg.get("apiBase", "https://api.openai.com/v1"))
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=float(llm_cfg.get("temperature", 0.2)),
            max_tokens=int(llm_cfg.get("maxTokens", 4000)),
            stream=False,
            timeout=float(session.timeout_seconds),
        )
        text = ""
        if resp.choices and resp.choices[0].message:
            text = resp.choices[0].message.content or ""
        session.output_text = text.strip()
        if not session.output_text:
            _finalize(session, "failed", error="empty response")
            return
        elapsed = round(time.time() - start_ts, 3)
        session.write_log(channel="stdout", io="chunk", content=session.output_text, bytes=len(session.output_text.encode("utf-8")))
        session.write_log(channel="process", event="exit", returncode=0, elapsed_seconds=elapsed)
        _finalize(session, "succeeded", returncode=0)
    except Exception as e:
        elapsed = round(time.time() - start_ts, 3)
        session.write_log(channel="process", event="error", error=str(e), elapsed_seconds=elapsed)
        _finalize(session, "failed", error=str(e))


def start_agent_process(
    *,
    task_id: str,
    run_id: str,
    cfg: dict[str, Any],
    prompt: str,
    cwd: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    _mark_lost_running_processes_once()
    provider = str(cfg.get("provider", "codex")).strip().lower()
    model = str(cfg.get("model", "")).strip()
    system_prompt = cfg.get("systemPrompt", "")
    final_prompt = prompt
    if isinstance(system_prompt, str) and system_prompt.strip():
        final_prompt = f"[System Instruction]\n{system_prompt.strip()}\n\n[Task]\n{prompt}"
    cmd = _build_agent_cmd(cfg, final_prompt)

    process_id = f"proc_{uuid.uuid4().hex[:12]}"
    session = ProcessSession(
        process_id=process_id,
        task_id=task_id,
        run_id=run_id,
        mode="agent",
        provider=provider,
        model=model,
        cwd=cwd,
        timeout_seconds=max(1, int(timeout_seconds)),
        interactive=True,
        log_path=str(_log_path(process_id)),
    )
    with _LOCK:
        _SESSIONS[process_id] = session
        _set_state_process(session)
    env = os.environ.copy()
    extra_env = cfg.get("env", {})
    if isinstance(extra_env, dict):
        env.update({str(k): str(v) for k, v in extra_env.items()})

    t = threading.Thread(target=_run_agent_session, args=(session, cmd, env, final_prompt), daemon=True)
    session.thread = t
    t.start()
    return {"success": True, "process_id": process_id, "run_id": run_id}


def start_llm_process(
    *,
    task_id: str,
    run_id: str,
    llm_cfg: dict[str, Any],
    prompt: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    _mark_lost_running_processes_once()
    provider = str(llm_cfg.get("provider", "openai_compatible")).strip()
    model = str(llm_cfg.get("model", "gpt-4o-mini")).strip()
    process_id = f"proc_{uuid.uuid4().hex[:12]}"
    session = ProcessSession(
        process_id=process_id,
        task_id=task_id,
        run_id=run_id,
        mode="llm",
        provider=provider,
        model=model,
        cwd="",
        timeout_seconds=max(1, int(timeout_seconds)),
        interactive=False,
        log_path=str(_log_path(process_id)),
    )
    with _LOCK:
        _SESSIONS[process_id] = session
        _set_state_process(session)
    t = threading.Thread(target=_run_llm_session, args=(session, llm_cfg, prompt), daemon=True)
    session.thread = t
    t.start()
    return {"success": True, "process_id": process_id, "run_id": run_id}


def list_processes(task_id: str | None = None, run_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
    _mark_lost_running_processes_once()
    with _LOCK:
        rows = [s.summary() for s in _SESSIONS.values()]
    state = _load_state()
    persisted = state.get("processes", {}) if isinstance(state, dict) else {}
    if isinstance(persisted, dict):
        for pid, meta in persisted.items():
            if not isinstance(meta, dict):
                continue
            if pid in {r.get("process_id") for r in rows}:
                continue
            rows.append(
                {
                    "process_id": pid,
                    "task_id": meta.get("task_id"),
                    "run_id": meta.get("run_id"),
                    "mode": meta.get("mode"),
                    "provider": meta.get("provider"),
                    "model": meta.get("model"),
                    "status": meta.get("status"),
                    "started_at": meta.get("started_at"),
                    "updated_at": meta.get("updated_at"),
                    "ended_at": meta.get("ended_at"),
                    "returncode": meta.get("returncode"),
                    "error": meta.get("error"),
                    "stdout_bytes": meta.get("stdout_bytes", 0),
                    "stderr_bytes": meta.get("stderr_bytes", 0),
                    "pid": meta.get("pid"),
                }
            )
    rows.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    if task_id:
        rows = [r for r in rows if r.get("task_id") == task_id]
    if run_id:
        rows = [r for r in rows if r.get("run_id") == run_id]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows[: max(1, int(limit))]


def get_process(process_id: str) -> dict[str, Any] | None:
    _mark_lost_running_processes_once()
    with _LOCK:
        s = _SESSIONS.get(process_id)
        if s is None:
            state = _load_state()
            persisted = state.get("processes", {}) if isinstance(state, dict) else {}
            meta = persisted.get(process_id) if isinstance(persisted, dict) else None
            if not isinstance(meta, dict):
                return None
            return {
                "process_id": process_id,
                "task_id": meta.get("task_id"),
                "run_id": meta.get("run_id"),
                "mode": meta.get("mode"),
                "provider": meta.get("provider"),
                "model": meta.get("model"),
                "status": meta.get("status"),
                "started_at": meta.get("started_at"),
                "updated_at": meta.get("updated_at"),
                "ended_at": meta.get("ended_at"),
                "returncode": meta.get("returncode"),
                "error": meta.get("error"),
                "stdout_bytes": meta.get("stdout_bytes", 0),
                "stderr_bytes": meta.get("stderr_bytes", 0),
                "pid": meta.get("pid"),
                "interactive": bool(meta.get("interactive", False)),
                "timeout_seconds": meta.get("timeout_seconds"),
                "log_path": meta.get("log_path"),
                "recovered": True,
            }
        out = s.summary()
        out["interactive"] = s.interactive
        out["timeout_seconds"] = s.timeout_seconds
        out["log_path"] = s.log_path
        return out


def wait_process(process_id: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    with _LOCK:
        session = _SESSIONS.get(process_id)
    if session is None:
        return {"found": False, "error": "process not found"}
    timeout = None if timeout_seconds is None else max(0.1, float(timeout_seconds))
    completed = session.done_event.wait(timeout=timeout)
    out = session.summary()
    out["found"] = True
    out["done"] = completed or session.done_event.is_set()
    return out


def poll_process(process_id: str) -> dict[str, Any]:
    _mark_lost_running_processes_once()
    out = get_process(process_id)
    if out is None:
        return {"found": False, "error": "process not found"}
    out["found"] = True
    if out.get("started_at") and out.get("ended_at"):
        try:
            started = datetime.fromisoformat(str(out["started_at"]))
            ended = datetime.fromisoformat(str(out["ended_at"]))
            out["elapsed_seconds"] = round((ended - started).total_seconds(), 3)
        except Exception:
            pass
    return out


def get_process_output(process_id: str) -> str | None:
    with _LOCK:
        session = _SESSIONS.get(process_id)
        if session is None:
            return None
        return session.output_text


def read_process_log(process_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    _mark_lost_running_processes_once()
    with _LOCK:
        session = _SESSIONS.get(process_id)
    if session is None:
        state = _load_state()
        persisted = state.get("processes", {}) if isinstance(state, dict) else {}
        meta = persisted.get(process_id) if isinstance(persisted, dict) else None
        if not isinstance(meta, dict):
            return {"found": False, "error": "process not found"}
        path = Path(str(meta.get("log_path") or ""))
    else:
        path = Path(session.log_path)
    if not path.exists():
        return {"found": True, "process_id": process_id, "items": [], "next_offset": max(0, offset), "eof": True}
    off = max(0, int(offset))
    lim = max(1, int(limit))
    items: list[dict[str, Any]] = []
    total = 0
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            total = idx + 1
            if idx < off:
                continue
            if len(items) >= lim:
                break
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                items.append({"raw": line})
    next_offset = off + len(items)
    eof = next_offset >= total
    return {"found": True, "process_id": process_id, "items": items, "next_offset": next_offset, "eof": eof}


def write_process(process_id: str, data: str, submit: bool = False) -> dict[str, Any]:
    _mark_lost_running_processes_once()
    with _LOCK:
        session = _SESSIONS.get(process_id)
    if session is None:
        return {"success": False, "error": "process not found"}
    if not session.interactive:
        return {"success": False, "error": "process is not interactive"}
    if session.status not in ("running", "starting"):
        return {"success": False, "error": f"process not writable in status={session.status}"}
    if session.master_fd < 0:
        return {"success": False, "error": "pty channel unavailable"}
    payload = data + ("\n" if submit else "")
    try:
        raw = payload.encode("utf-8", errors="replace")
        os.write(session.master_fd, raw)
        session.write_log(channel="stdin", io="write", transport="pty", content=payload, bytes=len(raw))
        return {"success": True, "process_id": process_id, "bytes": len(raw)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def kill_process(process_id: str, sig: str = "TERM") -> dict[str, Any]:
    _mark_lost_running_processes_once()
    with _LOCK:
        session = _SESSIONS.get(process_id)
    if session is None:
        return {"success": False, "error": "process not found"}
    proc = session.process
    if proc is None or proc.poll() is not None:
        return {"success": True, "process_id": process_id, "status": session.status}
    target_sig = signal.SIGKILL if str(sig).upper() == "KILL" else signal.SIGTERM
    try:
        os.killpg(proc.pid, target_sig)
        session.write_log(channel="process", event="kill", signal=target_sig)
        return {"success": True, "process_id": process_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
