#!/usr/bin/env python3
"""Shared runtime/output path helpers for cron agent."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

DataKind = Literal["logs", "runtime", "artifacts", "records", "journal", "messages"]

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_OUTPUT_ROOT = ".cron_agent_data"
MIGRATION_VERSION = "v1"
MIGRATION_SENTINEL_NAME = f".migration_{MIGRATION_VERSION}_done"
MIGRATION_REPORT_NAME = f"migration_report_{MIGRATION_VERSION}.json"

_KIND_DEFAULTS: dict[DataKind, str] = {
    "logs": "logs",
    "runtime": "runtime",
    "artifacts": "artifacts",
    "records": "records",
    "journal": "journal",
    "messages": "messages",
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_repo_root() -> Path:
    return BASE_DIR


def get_output_root(config: dict | None = None) -> Path:
    cfg = config if config is not None else load_config()
    configured = cfg.get("output_root", DEFAULT_OUTPUT_ROOT)
    p = Path(str(configured))
    if p.is_absolute():
        return p
    return BASE_DIR / p


def get_data_dir(kind: DataKind, config: dict | None = None) -> Path:
    cfg = config if config is not None else load_config()
    output_root = get_output_root(cfg)

    if kind == "records":
        configured = cfg.get("records_dir", _KIND_DEFAULTS[kind])
    elif kind == "journal":
        configured = cfg.get("journal_dir", _KIND_DEFAULTS[kind])
    elif kind == "messages":
        configured = cfg.get("messages_dir", _KIND_DEFAULTS[kind])
    else:
        configured = _KIND_DEFAULTS[kind]

    p = Path(str(configured))
    if p.is_absolute():
        return p
    return output_root / p


def resolve_data_path(path_str: str, default_base_kind: DataKind, config: dict | None = None) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    if str(p).strip() == "":
        return get_data_dir(default_base_kind, config=config)
    return get_output_root(config=config) / p


def ensure_data_layout(config: dict | None = None) -> None:
    cfg = config if config is not None else load_config()
    get_output_root(cfg).mkdir(parents=True, exist_ok=True)
    for kind in _KIND_DEFAULTS:
        get_data_dir(kind, cfg).mkdir(parents=True, exist_ok=True)


def _merge_move(src: Path, dst: Path) -> tuple[int, int]:
    """Move src tree into dst without overwriting existing files."""
    moved = 0
    skipped = 0

    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            skipped += 1
            src.unlink(missing_ok=True)
            return moved, skipped
        shutil.move(str(src), str(dst))
        moved += 1
        return moved, skipped

    for item in src.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            skipped += 1
            item.unlink(missing_ok=True)
            continue
        shutil.move(str(item), str(target))
        moved += 1

    # Cleanup empty dirs in source tree.
    for p in sorted(src.rglob("*"), reverse=True):
        if p.is_dir():
            try:
                p.rmdir()
            except OSError:
                pass
    try:
        src.rmdir()
    except OSError:
        pass

    return moved, skipped


def migrate_legacy_data_once(config: dict | None = None) -> dict:
    cfg = config if config is not None else load_config()
    ensure_data_layout(cfg)

    runtime_dir = get_data_dir("runtime", cfg)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sentinel = runtime_dir / MIGRATION_SENTINEL_NAME
    report_path = runtime_dir / MIGRATION_REPORT_NAME

    if sentinel.exists():
        return {"migrated": False, "reason": "already_migrated", "report_path": str(report_path)}

    summary: dict[str, object] = {
        "migrated": True,
        "output_root": str(get_output_root(cfg)),
        "entries": [],
    }

    for kind in _KIND_DEFAULTS:
        legacy = BASE_DIR / _KIND_DEFAULTS[kind]
        target = get_data_dir(kind, cfg)
        entry: dict[str, object] = {
            "kind": kind,
            "legacy": str(legacy),
            "target": str(target),
            "moved": 0,
            "skipped": 0,
            "status": "noop",
        }

        try:
            if not legacy.exists() or legacy.resolve() == target.resolve():
                entry["status"] = "noop"
            else:
                target.mkdir(parents=True, exist_ok=True)
                moved, skipped = _merge_move(legacy, target)
                entry["moved"] = moved
                entry["skipped"] = skipped
                entry["status"] = "migrated"
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)

        summary["entries"].append(entry)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    sentinel.write_text("done\n", encoding="utf-8")

    return summary
