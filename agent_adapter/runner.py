from __future__ import annotations

import json
import os
import pty
import select
import shlex
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import storage_paths

_AGENT_PROVIDERS = {"claude", "codex", "gemini", "opencode", "pi"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _trace_path(task_id: str, run_id: str) -> Path:
    return storage_paths.get_data_dir("logs") / "traces" / task_id / f"{run_id}.jsonl"


def _append_trace(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_cmd(cfg: dict[str, Any], final_prompt: str) -> list[str]:
    provider = str(cfg.get("provider", "codex")).strip().lower()
    model = str(cfg.get("model", "")).strip()
    sandbox_mode = str(cfg.get("sandboxMode", "workspace-write")).strip() or "workspace-write"

    explicit = cfg.get("cliCommand")
    cli_args = cfg.get("cliArgs", []) or []
    if isinstance(explicit, str) and explicit.strip():
        base = shlex.split(explicit)
        if isinstance(cli_args, list):
            base.extend([str(x) for x in cli_args])
        base.append(final_prompt)
        return base

    if provider == "codex":
        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            sandbox_mode,
        ]
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


def _decode_chunk(buf: bytes) -> str:
    if not buf:
        return ""
    return buf.decode("utf-8", errors="replace")


def _truncate_value(value: Any, max_bytes: int) -> tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return value, False
    trimmed = raw[:max_bytes].decode("utf-8", errors="ignore")
    return trimmed, True


def run_agent(task: dict[str, Any], run_id: str, prompt: str, timeout_seconds: int) -> tuple[bool, str, dict[str, Any]]:
    cfg = task.get("spec", {}).get("modeConfig", {}).get("agent", {}) or {}
    provider = str(cfg.get("provider", "codex")).strip().lower()
    if provider not in _AGENT_PROVIDERS:
        return False, "", {"error": f"unsupported agent provider: {provider}"}
    trace_cfg = cfg.get("trace", {}) if isinstance(cfg.get("trace"), dict) else {}
    try:
        max_event_bytes = max(1024, int(trace_cfg.get("maxEventBytes", 262144)))
    except Exception:
        max_event_bytes = 262144

    system_prompt = cfg.get("systemPrompt", "")
    final_prompt = prompt
    if isinstance(system_prompt, str) and system_prompt.strip():
        final_prompt = f"[System Instruction]\n{system_prompt.strip()}\n\n[Task]\n{prompt}"

    task_id = task.get("metadata", {}).get("id", "unknown")
    trace_path = _trace_path(task_id, run_id)
    start = time.time()
    seq = 0

    def write_event(**payload: Any) -> None:
        nonlocal seq
        seq += 1
        payload_copy = dict(payload)
        truncated_fields: list[str] = []
        for field in ("content", "error"):
            if field in payload_copy:
                payload_copy[field], truncated = _truncate_value(payload_copy[field], max_event_bytes)
                if truncated:
                    truncated_fields.append(field)

        row = {
            "seq": seq,
            "ts": _now_iso(),
            "run_id": run_id,
            "task_id": task_id,
            "provider": provider,
        }
        row.update(payload_copy)
        if truncated_fields:
            row["truncated_fields"] = truncated_fields
            row["max_event_bytes"] = max_event_bytes
        _append_trace(trace_path, row)

    # Input is currently passed as argv prompt to provider CLI; preserve it as stdin channel
    # trace with explicit transport marker rather than semantic message labels.
    write_event(channel="stdin", io="write", transport="argv_prompt", content=final_prompt)

    cmd = _build_cmd(cfg, final_prompt)
    working_dir = Path(task.get("spec", {}).get("execution", {}).get("workingDirectory", "."))
    cwd = storage_paths.get_repo_root() / working_dir
    env = os.environ.copy()
    extra_env = cfg.get("env", {})
    if isinstance(extra_env, dict):
        env.update({str(k): str(v) for k, v in extra_env.items()})

    write_event(channel="process", event="start", command=cmd, cwd=str(cwd))

    master_fd = -1
    slave_fd = -1
    stderr_fd = -1
    proc: subprocess.Popen[bytes] | None = None
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_bytes = 0
    stderr_bytes = 0

    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=os.setsid,
        )
        if proc.stderr is not None:
            stderr_fd = proc.stderr.fileno()
        os.close(slave_fd)
        slave_fd = -1

        deadline = time.time() + float(timeout_seconds)
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
                elapsed = round(time.time() - start, 3)
                write_event(channel="process", event="timeout", elapsed_seconds=elapsed)
                return False, "", {
                    "error": f"agent timeout after {timeout_seconds}s",
                    "elapsed": elapsed,
                    "trace_path": str(trace_path),
                }

            wait_timeout = max(0.05, min(0.25, deadline - time.time()))
            read_fds = [master_fd]
            if stderr_fd >= 0:
                read_fds.append(stderr_fd)
            ready, _, _ = select.select(read_fds, [], [], wait_timeout)
            if ready:
                if master_fd in ready:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        chunk = b""
                    if chunk:
                        text = _decode_chunk(chunk)
                        stdout_chunks.append(text)
                        stdout_bytes += len(chunk)
                        write_event(channel="stdout", io="chunk", content=text, bytes=len(chunk))
                if stderr_fd in ready:
                    try:
                        chunk = os.read(stderr_fd, 4096)
                    except OSError:
                        chunk = b""
                    if chunk:
                        text = _decode_chunk(chunk)
                        stderr_chunks.append(text)
                        stderr_bytes += len(chunk)
                        write_event(channel="stderr", io="chunk", content=text, bytes=len(chunk))

            rc = proc.poll()
            if rc is not None:
                # Drain any remaining output from stdout and stderr.
                while True:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    text = _decode_chunk(chunk)
                    stdout_chunks.append(text)
                    stdout_bytes += len(chunk)
                    write_event(channel="stdout", io="chunk", content=text, bytes=len(chunk))
                if stderr_fd >= 0:
                    while True:
                        try:
                            chunk = os.read(stderr_fd, 4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        text = _decode_chunk(chunk)
                        stderr_chunks.append(text)
                        stderr_bytes += len(chunk)
                        write_event(channel="stderr", io="chunk", content=text, bytes=len(chunk))
                break

        elapsed = round(time.time() - start, 3)
        returncode = proc.returncode if proc else -1
        stdout_text = "".join(stdout_chunks).strip()
        stderr_text = "".join(stderr_chunks).strip()
        has_meaningful_output = bool(stdout_text or stderr_text)
        if returncode != 0:
            write_event(channel="process", event="exit", returncode=returncode, elapsed_seconds=elapsed)
            return False, "", {
                "error": "agent process exited non-zero",
                "returncode": returncode,
                "elapsed": elapsed,
                "trace_path": str(trace_path),
            }

        if not has_meaningful_output:
            write_event(channel="process", event="exit", returncode=returncode, elapsed_seconds=elapsed)
            return False, "", {
                "error": "empty agent response",
                "returncode": returncode,
                "elapsed": elapsed,
                "trace_path": str(trace_path),
            }

        final_text = stdout_text if stdout_text else stderr_text
        final_channel = "stdout" if stdout_text else "stderr"
        write_event(channel="process", event="exit", returncode=returncode, elapsed_seconds=elapsed)
        return True, final_text, {
            "elapsed": elapsed,
            "returncode": returncode,
            "trace_path": str(trace_path),
            "provider": provider,
            "event_count": seq,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "final_channel": final_channel,
        }
    except FileNotFoundError:
        elapsed = round(time.time() - start, 3)
        write_event(channel="process", event="error", error=f"provider cli not found: {provider}", elapsed_seconds=elapsed)
        return False, "", {"error": f"provider cli not found: {provider}", "elapsed": elapsed, "trace_path": str(trace_path)}
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        write_event(channel="process", event="error", error=str(e), elapsed_seconds=elapsed)
        return False, "", {"error": str(e), "elapsed": elapsed, "trace_path": str(trace_path)}
    finally:
        if master_fd >= 0:
            try:
                os.close(master_fd)
            except OSError:
                pass
        if slave_fd >= 0:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        if proc is not None and proc.stderr is not None:
            try:
                proc.stderr.close()
            except Exception:
                pass
