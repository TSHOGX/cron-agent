#!/usr/bin/env python3
"""Cron job scheduler and main entry point using tmux."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import capture
import analyzer
import recorder
import summarizer


BASE_DIR = Path(__file__).parent
PYTHON_PATH = BASE_DIR / ".venv" / "bin" / "python"
SESSION_NAME = "cron_agent_capture"


def load_config():
    """Load configuration from config.json."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        return json.load(f)


def run_capture_and_record():
    """Main function to capture all screens, analyze together, and record."""
    print(f"[{datetime.now().isoformat()}] Starting capture cycle...")

    # Capture all screens
    screenshot_paths = capture.capture_multi_screens()
    if not screenshot_paths:
        print("Failed to capture any screenshot")
        return False

    print(f"Screenshots captured: {len(screenshot_paths)}")

    # Analyze all screens together
    result = analyzer.analyze_screenshots(screenshot_paths)
    if not result:
        print("Failed to analyze screenshots")
        return False

    description = result.get("description", "")
    print(f"Analysis: {description}")

    # Record to JSONL (single record with combined screenshots)
    timestamp = datetime.now().isoformat()
    recorder.append_record(timestamp, description, screenshot_paths)

    print(f"Record saved: {timestamp}")
    return True


def run_summary(period: str):
    """Run summary generation."""
    print(f"[{datetime.now().isoformat()}] Generating {period} summary...")

    # Check and fill missing summaries before generating new one
    filled = check_and_fill_missing_summaries()

    # Save messages for filled summaries
    for item in filled:
        msg = {
            "type": item["period"],
            "period": item["date"],
            "filled": True,
            "timestamp": item["timestamp"]
        }
        recorder.save_message(msg)

    # Generate the current period summary
    summarizer.generate_and_save(period)


# ========== Summary Fill-in / Catch-up Logic ==========

def get_latest_summary_date(period: str) -> datetime | None:
    """
    Get the date of the latest existing summary for a period.

    Args:
        period: 'daily', 'weekly', or 'monthly'

    Returns:
        datetime of the latest summary, or None if no summaries exist
    """
    journal_dir = recorder.get_journal_dir()
    period_dir = journal_dir / period

    if not period_dir.exists():
        return None

    config = load_config()
    now = datetime.now()

    # Get the date range for the period
    if period == "daily":
        # Look for daily files in the last 30 days
        dates = []
        for i in range(30):
            d = now - timedelta(days=i)
            dates.append(d)

        for d in dates:
            filename = d.strftime("%Y-%m-%d.md")
            if (period_dir / filename).exists():
                return d
        return None

    elif period == "weekly":
        # Look for weekly files
        for i in range(52):
            d = now - timedelta(weeks=i)
            week_num = d.isocalendar()[1]
            filename = f"{d.year}-W{week_num:02d}.md"
            if (period_dir / filename).exists():
                return d
        return None

    elif period == "monthly":
        # Look for monthly files
        for i in range(12):
            if now.month - i > 0:
                d = now.replace(month=now.month - i, day=1)
            else:
                d = now.replace(year=now.year - 1, month=now.month - i + 12, day=1)

            filename = d.strftime("%Y-%m.md")
            if (period_dir / filename).exists():
                return d
        return None

    return None


def get_expected_summary_dates(period: str, from_date: datetime, to_date: datetime) -> list:
    """
    Get list of expected summary dates between from_date and to_date.

    Args:
        period: 'daily', 'weekly', or 'monthly'
        from_date: Start date
        to_date: End date

    Returns:
        List of datetime objects representing expected summary dates
    """
    dates = []
    current = from_date

    if period == "daily":
        while current <= to_date:
            dates.append(current)
            current += timedelta(days=1)

    elif period == "weekly":
        config = load_config()
        weekly_day = config.get("weekly_summary_day", "sunday")
        day_map = {
            "sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
            "thursday": 4, "friday": 5, "saturday": 6
        }
        target_weekday = day_map.get(weekly_day.lower(), 0)

        # Find first target weekday on or after from_date
        days_until_target = (target_weekday - from_date.weekday()) % 7
        current = from_date + timedelta(days=days_until_target)

        while current <= to_date:
            dates.append(current)
            current += timedelta(weeks=1)

    elif period == "monthly":
        config = load_config()
        monthly_day = config.get("monthly_summary_day", 1)

        # Find first target day on or after from_date
        if from_date.day <= monthly_day:
            current = from_date.replace(day=monthly_day)
        else:
            if from_date.month == 12:
                current = from_date.replace(year=from_date.year + 1, month=1, day=monthly_day)
            else:
                current = from_date.replace(month=from_date.month + 1, day=monthly_day)

        while current <= to_date:
            dates.append(current)
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

    return dates


def summary_exists(period: str, date: datetime, time_period: str = None) -> bool:
    """
    Check if a summary exists for a specific period and date.

    Args:
        period: 'daily', 'weekly', 'monthly', or 'period'
        date: The date to check
        time_period: 'morning', 'afternoon', or 'evening' (only for period type)

    Returns:
        True if summary exists, False otherwise
    """
    journal_dir = recorder.get_journal_dir()
    period_dir = journal_dir / period

    if not period_dir.exists():
        return False

    if period == "daily":
        filename = date.strftime("%Y-%m-%d.md")
    elif period == "weekly":
        week_num = date.isocalendar()[1]
        filename = f"{date.year}-W{week_num:02d}.md"
    elif period == "monthly":
        filename = date.strftime("%Y-%m.md")
    elif period == "period" and time_period:
        filename = date.strftime(f"%Y-%m-%d-{time_period}.md")
    else:
        return False

    return (period_dir / filename).exists()


def has_records_for_date(date: datetime) -> bool:
    """
    Check if there are any activity records for a specific date.

    Args:
        date: The date to check

    Returns:
        True if records exist for the date, False otherwise
    """
    record_path = recorder.get_record_path(date)
    return record_path.exists() and record_path.stat().st_size > 0


def get_all_record_dates() -> list:
    """
    Get all dates that have activity records.

    Returns:
        List of datetime objects representing dates with records
    """
    records_dir = recorder.get_records_dir()
    dates = []

    if records_dir.exists():
        for file in records_dir.iterdir():
            if file.name.endswith('.jsonl'):
                # Parse date from filename like 2026-02-25.jsonl
                try:
                    date_str = file.name.replace('.jsonl', '')
                    date = datetime.strptime(date_str, "%Y-%m-%d")
                    # Check if file has content
                    if file.stat().st_size > 0:
                        dates.append(date)
                except ValueError:
                    continue

    return sorted(dates)


def fill_missing_summaries(period: str) -> list:
    """
    Fill in missing summaries for a period.

    Args:
        period: 'daily', 'weekly', or 'monthly'

    Returns:
        List of filled summary info dicts
    """
    config = load_config()
    now = datetime.now()

    # Get all dates that have records
    record_dates = get_all_record_dates()

    if not record_dates:
        print(f"[{datetime.now().isoformat()}] No records found, nothing to fill")
        return []

    missing_dates = []

    if period == "daily":
        # Get daily summary time
        daily_summary_time = config.get("daily_summary_time", "05:00")
        hour, minute = map(int, daily_summary_time.split(":"))
        is_yesterday_logic = hour < 12  # < 12:00 means "yesterday" logic

        # For each date with records, check if it needs to be filled
        for d in record_dates:
            # Determine the trigger date and filename based on time logic
            if is_yesterday_logic:
                # Trigger is next day at summary_time, filename is d (the record date)
                # e.g., records on 2月25日, trigger at 2月26日05:00, filename = 2026-02-25
                trigger_date = d + timedelta(days=1)
                filename_date = d  # "yesterday" - filename is the record date itself
            else:
                # Trigger is same day at summary_time, filename is d (the trigger date)
                # e.g., records on 2月26日, trigger at 2月26日14:00, filename = 2026-02-26
                trigger_date = d
                filename_date = d  # "today" - filename is the trigger date

            # Check if trigger time has passed
            trigger_time = trigger_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

            if now >= trigger_time:
                # Trigger time has passed, check if summary exists
                if not summary_exists(period, filename_date):
                    missing_dates.append({
                        "record_date": d,      # The date of the records
                        "filename_date": filename_date,  # The date for the filename
                        "trigger_time": trigger_time    # When the trigger should have fired
                    })
    else:
        # For weekly/monthly, use existing logic
        latest = get_latest_summary_date(period)

        if latest is None:
            from_date = record_dates[0] if record_dates else (now - timedelta(days=7))
        else:
            from_date = latest

        expected_dates = get_expected_summary_dates(period, from_date, now)
        for d in expected_dates:
            if not summary_exists(period, d):
                # Check if any records exist in the period
                if period == "weekly":
                    week_start = d - timedelta(days=d.weekday())
                    week_end = week_start + timedelta(days=6)
                    has_any = any(rd >= week_start and rd <= week_end for rd in record_dates)
                    if has_any:
                        missing_dates.append({"record_date": d, "filename_date": d, "trigger_time": now})
                elif period == "monthly":
                    month_start = d.replace(day=1)
                    if d.month == 12:
                        month_end = d.replace(year=d.year+1, month=1, day=1) - timedelta(days=1)
                    else:
                        month_end = d.replace(month=d.month+1, day=1) - timedelta(days=1)
                    has_any = any(rd >= month_start and rd <= month_end for rd in record_dates)
                    if has_any:
                        missing_dates.append({"record_date": d, "filename_date": d, "trigger_time": now})

    # Fill in missing summaries
    filled = []
    for item in missing_dates:
        record_date = item["record_date"]
        filename_date = item["filename_date"]

        print(f"[{datetime.now().isoformat()}] Filling missing {period} summary for {filename_date.strftime('%Y-%m-%d')}...")
        result = summarizer.generate_and_save_for_period(period, record_date, filename_date)
        if result:
            filled.append({
                "period": period,
                "date": filename_date.strftime("%Y-%m-%d"),
                "path": str(result),
                "timestamp": datetime.now().isoformat()
            })

    return filled


def get_expected_period_dates(period: str, from_date: datetime, to_date: datetime) -> list:
    """
    Get list of expected period summary dates between from_date and to_date.

    Args:
        period: 'period' type
        from_date: Start date
        to_date: End date

    Returns:
        List of datetime objects with expected period summary dates
    """
    dates = []
    current = from_date.replace(hour=0, minute=0, second=0, microsecond=0)

    while current <= to_date:
        dates.append(current)
        current += timedelta(days=1)

    return dates


def fill_missing_period_summaries() -> list:
    """
    Fill in missing period summaries (morning/afternoon/evening).

    Returns:
        List of filled summary info dicts
    """
    config = load_config()
    now = datetime.now()

    # Get time periods from config
    time_periods = config.get("time_periods", {
        "morning": {"start": "06:00", "end": "12:00"},
        "afternoon": {"start": "12:00", "end": "18:00"},
        "evening": {"start": "18:00", "end": "24:00"}
    })

    # Check last 7 days for missing period summaries
    from_date = now - timedelta(days=7)
    expected_dates = get_expected_period_dates("period", from_date, now)

    # Get the latest existing period summary date
    latest = None
    for tp in ["morning", "afternoon", "evening"]:
        for d in reversed(expected_dates):
            if summary_exists("period", d, tp):
                latest = d
                break
        if latest:
            break

    if latest:
        from_date = latest

    expected_dates = get_expected_period_dates("period", from_date, now)

    # Find missing period summaries
    filled = []
    for d in expected_dates:
        # Check if records exist for this date
        if not has_records_for_date(d):
            continue

        for tp in ["morning", "afternoon", "evening"]:
            # Skip if already exists
            if summary_exists("period", d, tp):
                continue

            print(f"[{datetime.now().isoformat()}] Filling missing period summary for {d.strftime('%Y-%m-%d')} ({tp})...")

            # Generate period summary using summarizer
            result = summarizer.generate_and_save_time_of_day_for_date(tp, d)
            if result:
                filled.append({
                    "period": "period",
                    "time_period": tp,
                    "date": d.strftime("%Y-%m-%d"),
                    "path": str(result),
                    "timestamp": datetime.now().isoformat()
                })

    return filled


def check_and_fill_missing_summaries() -> list:
    """
    Check and fill missing daily/weekly/monthly/period summaries.
    Returns list of filled summaries.

    Returns:
        List of filled summary info dicts
    """
    print(f"[{datetime.now().isoformat()}] Checking for missing summaries...")

    filled = []

    # Check daily summaries
    filled.extend(fill_missing_summaries("daily"))

    # Check weekly summaries
    filled.extend(fill_missing_summaries("weekly"))

    # Check monthly summaries
    filled.extend(fill_missing_summaries("monthly"))

    # Check period summaries (morning/afternoon/evening)
    filled.extend(fill_missing_period_summaries())

    print(f"[{datetime.now().isoformat()}] Fill complete. {len(filled)} summaries filled.")
    return filled


def generate_cron_config() -> str:
    """
    Generate cron configuration for summarizer tasks only.
    Capture is handled by tmux.

    Returns:
        Cron configuration string
    """
    config = load_config()
    base_dir = str(Path(__file__).parent.absolute())
    python_path = str(PYTHON_PATH) if PYTHON_PATH.exists() else "python3"

    # Build cron jobs (summarizer only, no capture - that's handled by tmux)
    cron_lines = []

    # Daily summary at configured time
    daily_time = config.get("daily_summary_time", "12:00")
    hour, minute = daily_time.split(":")
    cron_lines.append(f"{minute} {hour} * * * cd {base_dir} && {python_path} scheduler.py summary daily")

    # Time period summaries based on time_periods config
    time_periods = config.get("time_periods", {
        "morning": {"start": "06:00", "end": "12:00"},
        "afternoon": {"start": "12:00", "end": "18:00"},
        "evening": {"start": "18:00", "end": "24:00"}
    })

    for period_name, period_config in time_periods.items():
        end_time = period_config.get("end", "12:00")
        hour, minute = end_time.split(":")
        # Handle midnight (24:00 -> 0:00)
        if hour == "24":
            hour = "0"
        cron_lines.append(f"{minute} {hour} * * * cd {base_dir} && {python_path} scheduler.py summary {period_name}")

    # Weekly summary on configured day
    weekly_day = config.get("weekly_summary_day", "sunday")
    day_map = {
        "sunday": "0",
        "monday": "1",
        "tuesday": "2",
        "wednesday": "3",
        "thursday": "4",
        "friday": "5",
        "saturday": "6"
    }
    day_num = day_map.get(weekly_day.lower(), "0")
    cron_lines.append(f"0 10 * * {day_num} cd {base_dir} && {python_path} scheduler.py summary weekly")

    # Monthly summary
    monthly_day = config.get("monthly_summary_day", 1)
    cron_lines.append(f"0 11 {monthly_day} * * cd {base_dir} && {python_path} scheduler.py summary monthly")

    return "\n".join(cron_lines)


# ========== Cron for Summarizer ==========

def get_cron_status() -> dict:
    """Get cron service status for summarizer tasks."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            jobs = [j.strip() for j in result.stdout.strip().split("\n") if j.strip()]
            return {
                "installed": True,
                "jobs": jobs,
                "description": "定时汇总服务（Cron）"
            }
        else:
            return {
                "installed": False,
                "jobs": [],
                "error": result.stderr.strip() if result.stderr else "No crontab installed"
            }
    except Exception as e:
        return {
            "installed": False,
            "jobs": [],
            "error": str(e)
        }


def install_cron_service() -> dict:
    """Install cron jobs for summarizer tasks."""
    try:
        cron_config = generate_cron_config()

        # Write to temp file and install
        import tempfile
        import os as _os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.cron', delete=False) as f:
            f.write(cron_config)
            temp_path = f.name

        try:
            result = subprocess.run(
                ["crontab", temp_path],
                capture_output=True,
                text=True,
                timeout=10
            )
        finally:
            _os.unlink(temp_path)

        if result.returncode == 0:
            return {"success": True, "message": "汇总服务已启动"}
        else:
            return {"success": False, "error": result.stderr.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def stop_cron_service() -> dict:
    """Stop cron service by removing crontab."""
    try:
        result = subprocess.run(
            ["crontab", "-r"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return {"success": True, "message": "汇总服务已停止"}
        else:
            return {"success": False, "error": "No crontab to remove"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    cron_lines.append(f"0 11 {monthly_day} * * cd {base_dir} && python3 scheduler.py summary monthly")

    return "\n".join(cron_lines)


def install_cron():
    """Install cron jobs."""
    cron_config = generate_cron_config()

    # Print the config for user to review
    print("=" * 60)
    print("Cron Configuration:")
    print("=" * 60)
    print(cron_config)
    print("=" * 60)

    # Get cron path
    cron_path = Path.home() / ".cron_agent_cron"

    with open(cron_path, "w") as f:
        f.write(cron_config)

    print(f"\nCron config saved to: {cron_path}")
    print("\nTo install, run:")
    print(f"  crontab {cron_path}")
    print("\nTo view current crontab:")
    print("  crontab -l")


def uninstall_cron():
    """Uninstall cron jobs."""
    print("To uninstall, run:")
    print("  crontab -r")
    print("\nOr edit crontab with:")
    print("  crontab -e")


def cleanup_temp():
    """Clean up old temporary screenshots."""
    capture.cleanup_old_screenshots()


# ========== Tmux-based Scheduling ==========

def get_tmux_status() -> dict:
    """Get tmux service status."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", SESSION_NAME],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            # Get session info
            info_result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}:#{session_windows}:#{session_created}"],
                capture_output=True,
                text=True
            )
            return {
                "running": True,
                "session": SESSION_NAME,
                "info": info_result.stdout.strip() if info_result.returncode == 0 else ""
            }
        return {"running": False, "session": SESSION_NAME}
    except Exception as e:
        return {"running": False, "session": SESSION_NAME, "error": str(e)}


def start_tmux_service() -> dict:
    """Start tmux service for periodic capture."""
    try:
        config = load_config()
        interval_seconds = config.get("capture_interval", 900)

        # Build the capture command
        python_path = str(PYTHON_PATH) if PYTHON_PATH.exists() else "python3"
        capture_cmd = f"while true; do {python_path} {BASE_DIR / 'scheduler.py'} capture; sleep {interval_seconds}; done"

        # Kill existing session if any
        subprocess.run(["tmux", "kill-session", "-t", SESSION_NAME],
                      capture_output=True)

        # Create new session (detached)
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", SESSION_NAME, capture_cmd],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            return {"success": True, "message": f"Tmux session '{SESSION_NAME}' started (interval: {interval_seconds}s)"}
        else:
            return {"success": False, "error": result.stderr}
    except Exception as e:
        return {"success": False, "error": str(e)}


def stop_tmux_service() -> dict:
    """Stop tmux service."""
    try:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME],
            capture_output=True,
            text=True
        )
        return {"success": True, "message": f"Tmux session '{SESSION_NAME}' stopped"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def restart_tmux_service() -> dict:
    """Restart tmux service."""
    stop_result = stop_tmux_service()
    return start_tmux_service()


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scheduler.py capture           - Capture and record activity")
        print("  python scheduler.py summary <type>     - Generate summary (daily/weekly/monthly)")
        print("  python scheduler.py tmux start        - Start tmux capture service")
        print("  python scheduler.py tmux stop         - Stop tmux capture service")
        print("  python scheduler.py tmux status        - Check tmux service status")
        print("  python scheduler.py cron start         - Start cron summarizer service")
        print("  python scheduler.py cron stop          - Stop cron summarizer service")
        print("  python scheduler.py cron status       - Check cron service status")
        print("  python scheduler.py cleanup           - Clean up old screenshots")
        print("  python scheduler.py test              - Test the system")
        sys.exit(1)

    command = sys.argv[1]

    if command == "capture":
        run_capture_and_record()

    elif command == "summary":
        period = sys.argv[2] if len(sys.argv) > 2 else "daily"
        if period in ["morning", "afternoon", "evening"]:
            # Time period summary
            summarizer.generate_and_save_time_of_day(period)
        else:
            run_summary(period)

    elif command == "tmux":
        if len(sys.argv) < 3:
            print("Usage: python scheduler.py tmux [start|stop|status]")
            sys.exit(1)
        tmux_cmd = sys.argv[2]
        if tmux_cmd == "start":
            result = start_tmux_service()
            print(result)
        elif tmux_cmd == "stop":
            result = stop_tmux_service()
            print(result)
        elif tmux_cmd == "status":
            result = get_tmux_status()
            print(result)
        else:
            print(f"Unknown tmux command: {tmux_cmd}")
            sys.exit(1)

    elif command == "cron":
        if len(sys.argv) < 3:
            print("Usage: python scheduler.py cron [start|stop|status]")
            sys.exit(1)
        cron_cmd = sys.argv[2]
        if cron_cmd == "start":
            result = install_cron_service()
            print(result)
        elif cron_cmd == "stop":
            result = stop_cron_service()
            print(result)
        elif cron_cmd == "status":
            result = get_cron_status()
            print(result)
        else:
            print(f"Unknown cron command: {cron_cmd}")
            sys.exit(1)

    elif command == "cleanup":
        cleanup_temp()

    elif command == "test":
        print("Testing multi-screen capture...")
        screenshot_paths = capture.capture_multi_screens()
        if screenshot_paths:
            print(f"✓ Screenshots captured: {len(screenshot_paths)}")
            for i, path in enumerate(screenshot_paths):
                print(f"  Screen {i+1}: {path}")

            print("\nTesting analysis (all screens together)...")
            result = analyzer.analyze_screenshots(screenshot_paths)
            if result:
                print(f"✓ Analysis: {result.get('description', '')}")

                print("\nTesting recording...")
                timestamp = datetime.now().isoformat()
                recorder.append_record(timestamp, result.get("description", ""), screenshot_paths)
                print("✓ Recording saved")
            else:
                print("✗ Analysis failed")
        else:
            print("✗ Capture failed")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
