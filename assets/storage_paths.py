#!/usr/bin/env python3
"""Shared runtime/output path helpers for cron agent."""

import os
from pathlib import Path
from typing import Literal

DataKind = Literal["logs", "runtime", "artifacts", "tasks"]

BASE_DIR = Path(__file__).parent

# Support environment variable override for data directory
_DATA_ROOT = None

def _get_data_root() -> Path:
    global _DATA_ROOT
    if _DATA_ROOT is not None:
        return _DATA_ROOT

    # Priority: CRON_AGENT_DATA_DIR env var > default
    env_override = os.environ.get("CRON_AGENT_DATA_DIR")
    if env_override:
        _DATA_ROOT = Path(env_override)
    else:
        _DATA_ROOT = BASE_DIR / ".cron_agent_data"
    return _DATA_ROOT

DATA_ROOT = _get_data_root()

_KIND_DEFAULTS: dict[DataKind, str] = {
    "logs": "logs",
    "runtime": "runtime",
    "artifacts": "artifacts",
    "tasks": "tasks",
}


def get_repo_root() -> Path:
    return BASE_DIR


def get_output_root() -> Path:
    return _get_data_root()


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
