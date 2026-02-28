#!/usr/bin/env python3
"""Storage helpers for records, journals, and messages."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from runtime import storage_paths


def get_records_dir() -> Path:
    records_dir = storage_paths.get_data_dir("records")
    records_dir.mkdir(parents=True, exist_ok=True)
    return records_dir


def get_journal_dir() -> Path:
    journal_dir = storage_paths.get_data_dir("journal")
    journal_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("daily", "weekly", "monthly", "period"):
        (journal_dir / subdir).mkdir(parents=True, exist_ok=True)
    return journal_dir


def get_messages_dir() -> Path:
    messages_dir = storage_paths.get_data_dir("messages")
    messages_dir.mkdir(parents=True, exist_ok=True)
    return messages_dir


def _message_path(date: datetime | None = None) -> Path:
    dt = date or datetime.now()
    return get_messages_dir() / f"{dt.strftime('%Y-%m-%d')}.jsonl"


def read_messages(start_date: datetime | None = None, end_date: datetime | None = None, limit: int = 100) -> list[dict]:
    if start_date is None:
        start_date = datetime.now() - timedelta(days=7)
    if end_date is None:
        end_date = datetime.now()

    items: list[dict] = []
    current = start_date
    while current <= end_date:
        path = _message_path(current)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        items.append(parsed)
        current += timedelta(days=1)

    items.reverse()
    return items[: max(1, int(limit))]
