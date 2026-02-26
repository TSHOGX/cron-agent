#!/usr/bin/env python3
"""Screenshot capture module using macOS screencapture command."""

import subprocess
import json
import tempfile
from datetime import datetime
from pathlib import Path


def load_config():
    """Load configuration from config.json."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        return json.load(f)


def resolve_temp_path(temp_dir: str | None = None) -> Path:
    """Resolve screenshot temp directory under system temp by default."""
    config = load_config()
    configured = temp_dir if temp_dir is not None else config.get("temp_dir", "cron_agent")
    temp_base = Path(tempfile.gettempdir())

    candidate = Path(configured)
    return candidate if candidate.is_absolute() else temp_base / candidate


def capture_screenshot(temp_dir: str = None, screen_index: int = None) -> str | None:
    """
    Capture a screenshot of a specific screen or all screens.

    Args:
        temp_dir: Directory to save temporary screenshots
        screen_index: Specific screen index (0, 1, 2...), or None for all screens

    Returns:
        Path to the screenshot file, or None if capture failed
    """
    temp_path = resolve_temp_path(temp_dir)
    temp_path.mkdir(exist_ok=True, parents=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if screen_index is not None:
        # Capture specific screen
        filename = f"screenshot_{timestamp}_screen{screen_index}.png"
        screenshot_path = temp_path / filename

        cmd = [
            "screencapture",
            "-x",
            "-t", "png",
            "-D", str(screen_index + 1),  # -D is 1-indexed
            str(screenshot_path)
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and screenshot_path.exists():
                return str(screenshot_path)
            return None
        except Exception as e:
            print(f"Error: {e}")
            return None
    else:
        # Try to capture all screens first
        filename = f"screenshot_{timestamp}_all.png"
        screenshot_path = temp_path / filename

        cmd = [
            "screencapture",
            "-x",
            "-t", "png",
            str(screenshot_path)
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and screenshot_path.exists():
                return str(screenshot_path)
        except Exception:
            pass

        # If all screens failed, try each screen individually
        return capture_all_screens(temp_path, timestamp)


def capture_all_screens(temp_path: Path, timestamp: str) -> str | None:
    """
    Capture all available screens one by one.

    Returns:
        Path to the first successful screenshot, or None
    """
    # Try screens 1, 2, 3...
    for screen_index in range(1, 5):  # Try up to 4 screens
        filename = f"screenshot_{timestamp}_screen{screen_index}.png"
        screenshot_path = temp_path / filename

        cmd = [
            "screencapture",
            "-x",
            "-t", "png",
            "-D", str(screen_index),
            str(screenshot_path)
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and screenshot_path.exists():
                return str(screenshot_path)
        except Exception:
            continue

    return None


def capture_multi_screens(temp_dir: str = None) -> list[str]:
    """
    Capture all available screens and return list of screenshot paths.

    Args:
        temp_dir: Directory to save temporary screenshots

    Returns:
        List of screenshot file paths
    """
    temp_path = resolve_temp_path(temp_dir)
    temp_path.mkdir(exist_ok=True, parents=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_paths = []

    # First try screencapture (works in GUI session)
    for screen_index in range(1, 5):
        filename = f"screenshot_{timestamp}_screen{screen_index}.png"
        screenshot_path = temp_path / filename

        cmd = [
            "screencapture",
            "-x",
            "-t", "png",
            "-D", str(screen_index),
            str(screenshot_path)
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and screenshot_path.exists():
                screenshot_paths.append(str(screenshot_path))
        except Exception:
            continue

    return screenshot_paths


def cleanup_old_screenshots(max_age_hours: int = 24):
    """
    Remove old screenshots from temp directory.

    Args:
        max_age_hours: Maximum age in hours to keep screenshots
    """
    temp_path = resolve_temp_path()

    if not temp_path.exists():
        return

    now = datetime.now()
    cutoff = now.timestamp() - (max_age_hours * 3600)

    for pattern in ("screenshot_*.png", "screenshot_*.webp"):
        for file in temp_path.glob(pattern):
            if file.stat().st_mtime < cutoff:
                try:
                    file.unlink()
                except Exception as e:
                    print(f"Failed to delete {file}: {e}")


if __name__ == "__main__":
    path = capture_screenshot()
    if path:
        print(f"Screenshot saved to: {path}")
    else:
        print("Failed to capture screenshot")
