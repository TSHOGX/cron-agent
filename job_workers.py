#!/usr/bin/env python3
"""Task worker entrypoints for cron_manager-managed jobs."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import analyzer
import recorder
import summarizer


BASE_DIR = Path(__file__).parent


def load_config() -> dict:
    config_path = BASE_DIR / "config.json"
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def _resolve_temp_dir() -> Path:
    config = load_config()
    configured = config.get("temp_dir", "cron_agent")
    p = Path(configured)
    if p.is_absolute():
        return p
    return Path(tempfile.gettempdir()) / configured


def capture_multi_screens() -> list[str]:
    temp_dir = _resolve_temp_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output: list[str] = []
    errors: list[str] = []

    for idx in range(1, 5):
        screenshot_path = temp_dir / f"screenshot_{ts}_screen{idx}.png"
        cmd = ["screencapture", "-x", "-t", "png", "-D", str(idx), str(screenshot_path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and screenshot_path.exists():
                output.append(str(screenshot_path))
            elif result.stderr:
                errors.append(f"-D {idx}: {result.stderr.strip()}")
        except Exception as e:
            errors.append(f"-D {idx}: {e}")

    if not output:
        screenshot_path = temp_dir / f"screenshot_{ts}_all.png"
        cmd = ["screencapture", "-x", "-t", "png", str(screenshot_path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and screenshot_path.exists():
                output.append(str(screenshot_path))
            elif result.stderr:
                errors.append(f"all-screens: {result.stderr.strip()}")
        except Exception as e:
            errors.append(f"all-screens: {e}")

    if not output and errors:
        print("screencapture failed details:")
        for line in errors[:8]:
            print(f"  {line}")

    return output


def cleanup_temp(max_age_hours: int = 24) -> None:
    temp_dir = _resolve_temp_dir()
    if not temp_dir.exists():
        return
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    for pattern in ("screenshot_*.png", "screenshot_*.webp"):
        for file in temp_dir.glob(pattern):
            if file.stat().st_mtime < cutoff:
                try:
                    file.unlink()
                except Exception:
                    pass


def run_capture_analyze() -> bool:
    print(f"[{datetime.now().isoformat()}] Starting capture cycle...")
    screenshot_paths = capture_multi_screens()
    if not screenshot_paths:
        print("Failed to capture any screenshot")
        return False
    print(f"Screenshots captured: {len(screenshot_paths)}")

    result = analyzer.analyze_screenshots(screenshot_paths)
    if not result:
        print("Failed to analyze screenshots")
        return False

    description = result.get("description", "")
    timestamp = datetime.now().isoformat()
    recorder.append_record(timestamp, description, screenshot_paths)
    print(f"Record saved: {timestamp}")
    return True


def run_summary(period: str) -> bool:
    if period in ("morning", "afternoon", "evening"):
        path = summarizer.generate_and_save_time_of_day(period)
    else:
        path = summarizer.generate_and_save(period)
    return path is not None


def main() -> int:
    import sys

    if len(sys.argv) < 2:
        print("Usage: python job_workers.py [capture-analyze|summary|cleanup] ...")
        return 1

    cmd = sys.argv[1]
    if cmd == "capture-analyze":
        return 0 if run_capture_analyze() else 2
    if cmd == "summary":
        period = sys.argv[2] if len(sys.argv) > 2 else "daily"
        return 0 if run_summary(period) else 3
    if cmd == "cleanup":
        cleanup_temp()
        return 0

    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
