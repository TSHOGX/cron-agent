#!/usr/bin/env python3
"""JSONL recording module for storing daily activity logs."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def load_config():
    """Load configuration from config.json."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        return json.load(f)


def get_records_dir() -> Path:
    """Get the records directory path."""
    config = load_config()
    base_dir = Path(__file__).parent
    records_dir = base_dir / config.get("records_dir", "records")
    records_dir.mkdir(exist_ok=True)
    return records_dir


def get_journal_dir() -> Path:
    """Get the journal directory path."""
    config = load_config()
    base_dir = Path(__file__).parent
    journal_dir = base_dir / config.get("journal_dir", "journal")
    journal_dir.mkdir(exist_ok=True)

    # Ensure subdirectories exist
    for subdir in ["daily", "weekly", "monthly", "period"]:
        (journal_dir / subdir).mkdir(exist_ok=True)

    return journal_dir


def get_today_filename() -> str:
    """Get filename for today's records."""
    return datetime.now().strftime("%Y-%m-%d.jsonl")


def get_record_path(date: datetime = None) -> Path:
    """
    Get the path to the records file for a specific date.

    Args:
        date: Date for the records file (default: today)

    Returns:
        Path to the JSONL file
    """
    if date is None:
        date = datetime.now()

    filename = date.strftime("%Y-%m-%d.jsonl")
    return get_records_dir() / filename


def append_record(timestamp: str, description: str, screenshot_path: str | list = None):
    """
    Append a new record to today's JSONL file.

    Args:
        timestamp: ISO format timestamp
        description: Activity description from Claude
        screenshot_path: Kept only for backward compatibility, ignored
    """
    record = {
        "timestamp": timestamp,
        "description": description,
    }

    record_path = get_record_path()
    record_path.parent.mkdir(exist_ok=True, parents=True)

    with open(record_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_records(start_date: datetime = None, end_date: datetime = None) -> list:
    """
    Read records for a date range.

    Args:
        start_date: Start date (default: today)
        end_date: End date (default: today)

    Returns:
        List of record dictionaries
    """
    if start_date is None:
        start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if end_date is None:
        end_date = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)

    records = []
    current_date = start_date

    while current_date <= end_date:
        record_path = get_record_path(current_date)

        if record_path.exists():
            with open(record_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            # Parse timestamp and filter by range
                            record_time = datetime.fromisoformat(record["timestamp"])
                            if start_date <= record_time <= end_date:
                                records.append(record)
                        except json.JSONDecodeError:
                            continue
                        except (KeyError, ValueError):
                            continue

        current_date += timedelta(days=1)

    return records


def read_today_records() -> list:
    """Read all records for today."""
    return read_records()


def get_date_range(period: str) -> tuple:
    """
    Get date range for a period (legacy function).

    Args:
        period: 'daily', 'weekly', or 'monthly'

    Returns:
        Tuple of (start_date, end_date)
    """
    now = datetime.now()

    if period == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif period == "weekly":
        # Get start of week (Sunday)
        days_since_sunday = now.weekday() + 1
        start = (now - timedelta(days=days_since_sunday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif period == "monthly":
        # Get start of month
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # End of month
        if now.month == 12:
            end = now.replace(month=12, day=31, hour=23, minute=59, second=59)
        else:
            end = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0)
            end -= timedelta(seconds=1)
    else:
        raise ValueError(f"Unknown period: {period}")

    return start, end


def get_summary_date_range(period: str, summary_time: str = "00:00",
                           week_day: str = None, month_day: int = None,
                           reference_date: datetime = None) -> tuple:
    """
    Get summary time range based on user configuration.

    - daily: 设置04:00 → 昨天04:00 到 今天04:00
    - weekly: 设置周二 → 上周二 到 本周一 (7天日报的汇总)
    - monthly: 设置1号 → 上月1号 到 本月1日 (约30天日报的汇总)

    Args:
        period: 'daily', 'weekly', or 'monthly'
        summary_time: Daily summary time in HH:MM format (default: "00:00")
        week_day: Day of week for weekly summary (sunday-saturday)
        month_day: Day of month for monthly summary (1-28)
        reference_date: Reference date for calculating the range (default: now)

    Returns:
        Tuple of (start_date, end_date)
    """
    config = load_config()

    # Get configuration from config if not provided
    if summary_time == "00:00" or summary_time is None:
        summary_time = config.get("daily_summary_time", "00:00")
    if week_day is None:
        week_day = config.get("weekly_summary_day", "sunday")
    if month_day is None:
        month_day = config.get("monthly_summary_day", 1)

    # Parse summary time
    hour, minute = map(int, summary_time.split(":"))
    # Use reference_date if provided, otherwise use now
    now = reference_date if reference_date else datetime.now()

    # Day of week mapping
    day_map = {
        "sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
        "thursday": 4, "friday": 5, "saturday": 6
    }
    target_weekday = day_map.get(week_day.lower(), 0)

    if period == "daily":
        # For a specific date, the range should be:
        # (target_date - 1 day) HH:MM to target_date HH:MM
        # Example: Feb 26 report uses Feb 25 05:00 to Feb 26 05:00
        start = (reference_date - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        end = reference_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    elif period == "weekly":
        # Weekly: based on target weekday
        # Current week's target weekday
        current_weekday = now.weekday()
        days_since_target = (current_weekday - target_weekday) % 7

        # End of current period: last occurrence of target_weekday
        end = now - timedelta(days=days_since_target)
        end = end.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If we haven't reached the target day yet this week, end was last week's
        if days_since_target == 0 and now.time() < end.time():
            # Still waiting for today's trigger time
            end = (now - timedelta(days=7)).replace(hour=hour, minute=minute, second=0, microsecond=0)

        # Start of current period: 7 days before end
        start = end - timedelta(days=7)

    elif period == "monthly":
        # Monthly: based on target day of month
        target_day = month_day

        # End of current period: this month's target day
        if now.day >= target_day:
            # We're past this month's target day
            end = now.replace(day=target_day, hour=hour, minute=minute, second=0, microsecond=0)
            # If we haven't reached the target time yet today
            if now.day == target_day and now.time() < end.time():
                end = (now.replace(day=1) - timedelta(days=1)).replace(day=target_day, hour=hour, minute=minute, second=0, microsecond=0)
        else:
            # We're before this month's target day, use last month's
            if now.month == 1:
                end = now.replace(year=now.year - 1, month=12, day=target_day, hour=hour, minute=minute, second=0, microsecond=0)
            else:
                end = now.replace(month=now.month - 1, day=target_day, hour=hour, minute=minute, second=0, microsecond=0)

        # Start of current period: same day last month
        if end.month == 1:
            start = end.replace(year=end.year - 1, month=12)
        else:
            start = end.replace(month=end.month - 1)

    else:
        raise ValueError(f"Unknown period: {period}")

    return start, end


def read_daily_notes(start_date: datetime = None, end_date: datetime = None) -> list:
    """
    Read daily notes (generated summaries) for a date range.

    Args:
        start_date: Start date (default: today)
        end_date: End date (default: today)

    Returns:
        List of dicts with date and content
    """
    from pathlib import Path

    if start_date is None:
        start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if end_date is None:
        end_date = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)

    journal_dir = get_journal_dir()
    daily_dir = journal_dir / "daily"

    results = []
    current_date = start_date

    while current_date <= end_date:
        filename = current_date.strftime("%Y-%m-%d.md")
        filepath = daily_dir / filename

        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                # Extract summary between first "---" and second "---"
                lines = content.split("\n")
                summary_start = None
                summary_end = None
                for i, line in enumerate(lines):
                    if line.strip() == "---":
                        if summary_start is None:
                            summary_start = i
                        elif summary_end is None:
                            summary_end = i
                            break

                if summary_start is not None and summary_end is not None:
                    summary = "\n".join(lines[summary_start + 1:summary_end]).strip()
                else:
                    summary = content

                results.append({
                    "date": current_date.strftime("%Y-%m-%d"),
                    "content": summary
                })

        current_date += timedelta(days=1)

    return results


# ========== Message Storage ==========

def get_messages_dir() -> Path:
    """Get the messages directory path."""
    config = load_config()
    base_dir = Path(__file__).parent
    messages_dir = base_dir / config.get("messages_dir", "messages")
    messages_dir.mkdir(exist_ok=True)
    return messages_dir


def get_message_path(date: datetime = None) -> Path:
    """
    Get the path to the messages file for a specific date.

    Args:
        date: Date for the messages file (default: today)

    Returns:
        Path to the JSONL file
    """
    if date is None:
        date = datetime.now()

    filename = date.strftime("%Y-%m-%d.jsonl")
    return get_messages_dir() / filename


def save_message(msg: dict):
    """
    Save a message to the messages directory.

    Args:
        msg: Message dict with type, period, filled, timestamp, etc.
    """
    msg_path = get_message_path()
    msg_path.parent.mkdir(exist_ok=True, parents=True)

    with open(msg_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def read_messages(start_date: datetime = None, end_date: datetime = None, limit: int = 100) -> list:
    """
    Read messages for a date range.

    Args:
        start_date: Start date (default: 7 days ago)
        end_date: End date (default: today)
        limit: Maximum number of messages to return

    Returns:
        List of message dicts, newest first
    """
    if start_date is None:
        start_date = datetime.now() - timedelta(days=7)
    if end_date is None:
        end_date = datetime.now()

    messages = []
    current_date = start_date

    while current_date <= end_date:
        msg_path = get_message_path(current_date)

        if msg_path.exists():
            with open(msg_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            messages.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        current_date += timedelta(days=1)

    # Reverse to show newest first
    messages = list(reversed(messages))
    return messages[:limit]


if __name__ == "__main__":
    # Test: append a sample record
    now = datetime.now().isoformat()
    append_record(now, "Test activity description", "/path/to/screenshot.png")

    # Test: read today's records
    records = read_today_records()
    print(f"Found {len(records)} records today")
    for r in records:
        print(f"  {r['timestamp']}: {r['description']}")
