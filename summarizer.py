#!/usr/bin/env python3
"""Summary generation module using OpenAI client with Moonshot/Kimi API."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import recorder


def load_config():
    """Load configuration from config.json."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        return json.load(f)


def _require_prompt_key(prompt_config: dict, key: str) -> str:
    """Get required summary prompt key from config."""
    value = prompt_config.get(key)
    if not value:
        raise ValueError(f"Missing config.summary_prompt.{key}")
    return value


def _delta_to_text(delta_content) -> str:
    """Convert streaming delta content to plain text."""
    if delta_content is None:
        return ""
    if isinstance(delta_content, str):
        return delta_content
    if isinstance(delta_content, list):
        parts = []
        for item in delta_content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(text)
        return "".join(parts)
    return str(delta_content)


def _extract_message_text(message_content) -> str:
    """Extract plain text from non-stream message content."""
    return _delta_to_text(message_content).strip()


def _request_once(client, model: str, messages: list, max_tokens: int) -> tuple[str, str | None, bool, bool]:
    """Send one non-stream completion request and return parsed metadata."""
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        stream=False
    )
    if not resp.choices:
        return "", None, False, False

    choice = resp.choices[0]
    msg = choice.message
    if not msg:
        return "", getattr(choice, "finish_reason", None), False, False

    text = _extract_message_text(getattr(msg, "content", None))
    finish_reason = getattr(choice, "finish_reason", None)
    has_reasoning = bool(getattr(msg, "reasoning_content", None))
    has_refusal = bool(getattr(msg, "refusal", None))
    return text, finish_reason, has_reasoning, has_refusal


def _request_final_text(client, model: str, messages: list, max_tokens: int) -> str | None:
    """Request final answer text in non-stream mode."""
    text, finish_reason, has_reasoning, has_refusal = _request_once(client, model, messages, max_tokens)

    if text:
        return text

    print(
        "Non-stream completion has no final content "
        f"(finish_reason={finish_reason}, has_reasoning={has_reasoning}, has_refusal={has_refusal})."
    )
    return None


def _build_messages(config: dict, user_prompt: str) -> list:
    """Build chat messages for summary generation."""
    prompt_config = config.get("summary_prompt", {})
    system_prompt = _require_prompt_key(prompt_config, "system")
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]


def generate_summary(period: str = "daily", specific_date: datetime = None, filename_date: datetime = None) -> str | None:
    """
    Generate a summary for the specified period using OpenAI client.

    Args:
        period: 'daily', 'weekly', or 'monthly'
        specific_date: Optional date for calculating record time range
        filename_date: Optional date for the filename (only for daily with yesterday/today logic)

    Returns:
        Summary text or None if failed
    """
    from openai import OpenAI

    config = load_config()
    api_config = config.get("api", {})
    api_key = api_config.get("auth_token")
    base_url = api_config.get("base_url", "https://api.moonshot.cn/v1")
    model = config.get("model", "kimi-k2.5")

    if not api_key:
        print("API key not configured")
        return None

    # Use specific_date if provided, otherwise use now
    # Use filename_date for display text, specific_date for record range
    now = specific_date if specific_date else datetime.now()
    display_date = filename_date if filename_date else now

    prompt_config = config.get("summary_prompt", {})

    if period == "daily":
        # Daily: input = records, time = HH:MM, prompt needs date
        # For "yesterday" logic (hour < 12): filename_date 02/25 → record range 02/25 05:00 to 02/26 05:00
        #   So we need to pass (filename_date + 1 day) to get_summary_date_range
        # For "today" logic (hour >= 12): filename_date 02/26 → record range 02/26 00:00 to 02/27 00:00
        #   So we need to pass filename_date to get_summary_date_range
        daily_summary_time = config.get("daily_summary_time", "05:00")
        daily_hour = int(daily_summary_time.split(":")[0])
        is_yesterday_logic = daily_hour < 12

        if is_yesterday_logic and filename_date:
            # For "yesterday" logic, use filename_date + 1 day for record range
            record_date = filename_date + timedelta(days=1)
        else:
            record_date = now

        start_date, end_date = recorder.get_summary_date_range(period, reference_date=record_date)
        records = recorder.read_records(start_date, end_date)

        if not records:
            print(f"No records found for {period}")
            return None

        # Format records: HH:MM
        record_texts = []
        for r in records:
            time = datetime.fromisoformat(r["timestamp"]).strftime("%H:%M")
            desc = r["description"]
            record_texts.append(f"- {time}: {desc}")

        records_str = "\n".join(record_texts)
        date_str = display_date.strftime("%Y-%m-%d")

        user_template = _require_prompt_key(prompt_config, "daily")
        prompt = user_template.format(date=date_str, records=records_str)

    else:
        # Weekly/Monthly: input = daily notes, time = YYYY-MM-DD
        start_date, end_date = recorder.get_summary_date_range(period, reference_date=now)
        daily_notes = recorder.read_daily_notes(start_date, end_date)

        if not daily_notes:
            print(f"No daily notes found for {period}")
            return None

        # Format daily notes: YYYY-MM-DD
        note_texts = []
        for note in daily_notes:
            date = note["date"]
            content = note["content"]
            note_texts.append(f"- {date}: {content}")

        notes_str = "\n".join(note_texts)

        if period == "weekly":
            date_range = f"{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"
        else:
            date_range = f"{start_date.strftime('%Y-%m')}月"

        user_template = _require_prompt_key(prompt_config, period)
        prompt = user_template.format(date_range=date_range, notes=notes_str)

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)

        messages = _build_messages(config, prompt)
        summary = _request_final_text(client, model, messages, max_tokens=200000)
        return summary if summary else None

    except Exception as e:
        print(f"Error generating summary: {e}")
        return None


def save_summary(summary: str, period: str, time_period: str = None, specific_date: datetime = None):
    """
    Save summary to the journal directory.

    Args:
        summary: Summary text
        period: 'daily', 'weekly', 'monthly', or 'period'
        time_period: 'morning', 'afternoon', or 'evening' (only for period)
        specific_date: Optional specific date for historical summaries (for fill-ins)
    """
    config = load_config()
    journal_dir = recorder.get_journal_dir()
    period_dir = journal_dir / period

    # Use specific_date if provided, otherwise use now
    now = specific_date if specific_date else datetime.now()

    if period == "daily":
        filename = now.strftime("%Y-%m-%d.md")
    elif period == "weekly":
        week_num = now.isocalendar()[1]
        filename = f"{now.year}-W{week_num:02d}.md"
    elif period == "monthly":
        filename = now.strftime("%Y-%m.md")
    elif period == "period" and time_period:
        filename = now.strftime(f"%Y-%m-%d-{time_period}.md")
    else:
        filename = f"{period}_{now.strftime('%Y%m%d')}.md"

    filepath = period_dir / filename

    period_label = {
        "daily": f"日报 - {now.strftime('%Y年%m月%d日')}",
        "weekly": f"周报 - {now.strftime('%Y年第%W周')}",
        "monthly": f"月报 - {now.strftime('%Y年%m月')}",
        "period": f"时段总结 - {now.strftime('%Y年%m月%d日')}"
    }

    time_period_label = {
        "morning": "上午",
        "afternoon": "下午",
        "evening": "晚上"
    }

    if period == "period" and time_period:
        title = f"{time_period_label.get(time_period, '时段')}总结 - {now.strftime('%Y年%m月%d日 %H:%M')}"
    else:
        title = period_label.get(period, f"总结 - {now.strftime('%Y年%m月%d日')}")

    content = f"""# {title}

Generated at: {datetime.now().isoformat()}

---

{summary}
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Summary saved to: {filepath}")
    return filepath


def generate_and_save(period: str = "daily"):
    """
    Generate and save a summary for the specified period.

    Args:
        period: 'daily', 'weekly', 'monthly', or 'period'

    Returns:
        Path to saved summary or None
    """
    summary = generate_summary(period)
    if summary:
        return save_summary(summary, period)
    return None


def generate_and_save_for_period(period: str, target_date: datetime, filename_date: datetime = None) -> str | None:
    """
    Generate and save a summary for a specific historical period (for fill-in).

    Args:
        period: 'daily', 'weekly', or 'monthly'
        target_date: The date for calculating record time range
        filename_date: The date to use for the filename (optional, defaults to target_date)

    Returns:
        Path to saved summary or None
    """
    # Use filename_date for the filename, target_date for record range
    if filename_date is None:
        filename_date = target_date

    summary = generate_summary(period, specific_date=target_date, filename_date=filename_date)
    if summary:
        return save_summary(summary, period, specific_date=filename_date)
    return None


def load_time_periods():
    """Load configurable time periods from config."""
    config = load_config()
    return config.get("time_periods", {
        "morning": {"start": "06:00", "end": "12:00"},
        "afternoon": {"start": "12:00", "end": "18:00"},
        "evening": {"start": "18:00", "end": "24:00"}
    })


def parse_time(time_str: str) -> tuple:
    """Parse time string HH:MM to hour and minute."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def get_time_period_label(time_period: str) -> str:
    """Get label for time period."""
    labels = {
        "morning": "上午",
        "afternoon": "下午",
        "evening": "晚上"
    }
    return labels.get(time_period, "时段")


def generate_time_of_day_summary(time_period: str = "morning") -> str | None:
    """
    Generate a summary for a specific time of day.

    Args:
        time_period: 'morning' (6-12), 'afternoon' (12-18), 'evening' (18-24)

    Returns:
        Summary text or None if failed
    """
    from openai import OpenAI

    config = load_config()
    api_config = config.get("api", {})
    api_key = api_config.get("auth_token")
    base_url = api_config.get("base_url", "https://api.moonshot.cn/v1")
    model = config.get("model", "kimi-k2.5")

    if not api_key:
        return None

    now = datetime.now()

    # Get configurable time periods
    time_periods = load_time_periods()
    period_config = time_periods.get(time_period, {"start": "06:00", "end": "12:00"})

    start_hour, start_min = parse_time(period_config["start"])
    end_hour, end_min = parse_time(period_config["end"])

    start = now.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
    label = get_time_period_label(time_period)

    if now < start:
        return None

    records = recorder.read_records(start, min(end, now))

    if not records:
        return None

    # Format records: HH:MM
    record_texts = []
    for r in records:
        time = datetime.fromisoformat(r["timestamp"]).strftime("%H:%M")
        record_texts.append(f"- {time}: {r['description']}")

    records_str = "\n".join(record_texts)

    prompt_config = config.get("summary_prompt", {})
    user_template = _require_prompt_key(prompt_config, "time_of_day")
    prompt = user_template.format(label=label, records=records_str)

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)

        messages = _build_messages(config, prompt)
        summary = _request_final_text(client, model, messages, max_tokens=200000)
        return summary if summary else None

    except Exception as e:
        print(f"Error: {e}")
        return None


def generate_and_save_time_of_day(time_period: str = "morning"):
    """
    Generate and save a summary for a specific time of day.

    Args:
        time_period: 'morning', 'afternoon', or 'evening'

    Returns:
        Path to saved summary or None
    """
    summary = generate_time_of_day_summary(time_period)
    if summary:
        return save_summary(summary, "period", time_period)
    return None


def generate_time_of_day_summary_for_date(time_period: str, target_date: datetime) -> str | None:
    """
    Generate a summary for a specific time of day on a specific date.

    Args:
        time_period: 'morning' (6-12), 'afternoon' (12-18), 'evening' (18-24)
        target_date: The target date for the summary

    Returns:
        Summary text or None if failed
    """
    from openai import OpenAI

    config = load_config()
    api_config = config.get("api", {})
    api_key = api_config.get("auth_token")
    base_url = api_config.get("base_url", "https://api.moonshot.cn/v1")
    model = config.get("model", "kimi-k2.5")

    if not api_key:
        return None

    # Get configurable time periods
    time_periods = load_time_periods()
    period_config = time_periods.get(time_period, {"start": "06:00", "end": "12:00"})

    start_hour, start_min = parse_time(period_config["start"])
    end_hour, end_min = parse_time(period_config["end"])

    # Use target_date instead of now
    start = target_date.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
    end = target_date.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
    label = get_time_period_label(time_period)

    records = recorder.read_records(start, end)

    if not records:
        print(f"No records found for {time_period} on {target_date.strftime('%Y-%m-%d')}")
        return None

    # Format records: HH:MM
    record_texts = []
    for r in records:
        time = datetime.fromisoformat(r["timestamp"]).strftime("%H:%M")
        record_texts.append(f"- {time}: {r['description']}")

    records_str = "\n".join(record_texts)

    prompt_config = config.get("summary_prompt", {})
    user_template = _require_prompt_key(prompt_config, "time_of_day")
    prompt = user_template.format(label=label, records=records_str)

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)

        messages = _build_messages(config, prompt)
        summary = _request_final_text(client, model, messages, max_tokens=200000)
        return summary if summary else None

    except Exception as e:
        print(f"Error: {e}")
        return None


def generate_and_save_time_of_day_for_date(time_period: str, target_date: datetime):
    """
    Generate and save a summary for a specific time of day on a specific date.

    Args:
        time_period: 'morning', 'afternoon', or 'evening'
        target_date: The target date for the summary

    Returns:
        Path to saved summary or None
    """
    summary = generate_time_of_day_summary_for_date(time_period, target_date)
    if summary:
        return save_summary(summary, "period", time_period, specific_date=target_date)
    return None


if __name__ == "__main__":
    import sys

    period = sys.argv[1] if len(sys.argv) > 1 else "daily"

    if period in ["daily", "weekly", "monthly"]:
        generate_and_save(period)
    elif period in ["morning", "afternoon", "evening"]:
        generate_and_save_time_of_day(period)
    else:
        print("Usage: python summarizer.py [daily|weekly|monthly|morning|afternoon|evening]")
