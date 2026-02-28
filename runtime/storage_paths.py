#!/usr/bin/env python3
"""Shared runtime/output path helpers for cron agent."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

DataKind = Literal["logs", "runtime", "artifacts", "records", "journal", "messages", "tasks"]

RUNTIME_DIR = Path(__file__).resolve().parent
REPO_ROOT = RUNTIME_DIR.parent
DATA_ROOT = REPO_ROOT / ".cron_agent_data"

_KIND_DEFAULTS: dict[DataKind, str] = {
    "logs": "logs",
    "runtime": "runtime",
    "artifacts": "artifacts",
    "records": "records",
    "journal": "journal",
    "messages": "messages",
    "tasks": "tasks",
}


def get_repo_root() -> Path:
    return REPO_ROOT


def get_output_root() -> Path:
    return DATA_ROOT


def get_data_dir(kind: DataKind) -> Path:
    return get_output_root() / _KIND_DEFAULTS[kind]


def resolve_data_path(path_str: str, default_base_kind: DataKind) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    if str(p).strip() == "":
        return get_data_dir(default_base_kind)
    return get_output_root() / p


def ensure_data_layout() -> None:
    get_output_root().mkdir(parents=True, exist_ok=True)
    for kind in _KIND_DEFAULTS:
        get_data_dir(kind).mkdir(parents=True, exist_ok=True)
