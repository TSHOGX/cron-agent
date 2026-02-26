#!/usr/bin/env python3
"""
Image analysis module using OpenAI client with Moonshot/Kimi API.
Uses file upload with WebP compression, and streams final output text.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path


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
                parts.append(item.get("text", ""))
            else:
                parts.append(getattr(item, "text", "") or str(item))
        return "".join(parts)
    return str(delta_content)


def load_config():
    """Load configuration from config.json."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        return json.load(f)


def _require_prompt_key(prompt_config: dict, key: str) -> str:
    """Get required record prompt key from config."""
    value = prompt_config.get(key)
    if not value:
        raise ValueError(f"Missing config.record_prompt.{key}")
    return value


def get_recent_records(count: int = 3) -> list:
    """Get recent records for context."""
    try:
        import recorder
        records = recorder.read_today_records()
        # Get last N records (excluding the very recent ones if any)
        return records[-count:] if len(records) >= count else records
    except Exception:
        return []


def format_timestamp(ts: str) -> str:
    """Format ISO timestamp to YYYY-MM-dd HH:mm format."""
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def convert_to_webp(png_path: str, quality: int = 80) -> str | None:
    """
    Convert PNG to WebP format with compression.

    Args:
        png_path: Path to PNG file
        quality: Quality (1-100)

    Returns:
        Path to WebP file, or None if failed
    """
    webp_path = png_path.replace(".png", ".webp")

    try:
        from PIL import Image

        img = Image.open(png_path)
        # Convert RGBA to RGB if needed
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background

        img.save(webp_path, "WEBP", quality=quality, optimize=True)
        return webp_path

    except ImportError:
        # Fallback: use system cwebp if available
        result = subprocess.run(
            ["cwebp", "-q", str(quality), png_path, "-o", webp_path],
            capture_output=True
        )
        if result.returncode == 0 and Path(webp_path).exists():
            return webp_path

    return None


def analyze_screenshots(screenshot_paths: list[str]) -> dict | None:
    """
    Analyze multiple screenshots using OpenAI client with streaming.

    Args:
        screenshot_paths: List of paths to screenshot files

    Returns:
        Dictionary with analysis result or None if failed
    """
    from openai import OpenAI
    from pathlib import Path as FilePath

    config = load_config()
    api_config = config.get("api", {})
    api_key = api_config.get("auth_token")
    base_url = api_config.get("base_url", "https://api.moonshot.cn/v1")
    model = config.get("model", "kimi-k2.5")

    if not api_key:
        print("API key not configured")
        return None

    if not screenshot_paths:
        print("No screenshots provided")
        return None

    try:
        # Get current time and recent records for context
        now = datetime.now()
        current_time = now.strftime("%Y-%m-%d %H:%M")

        recent_records = get_recent_records(3)
        context_str = ""
        if recent_records:
            context_lines = []
            for r in recent_records:
                ts = format_timestamp(r.get("timestamp", ""))
                desc = r.get("description", "")[:50]  # Truncate long descriptions
                context_lines.append(f"- {ts}: {desc}")
            context_str = "\n用户近期的活动记录：\n" + "\n".join(context_lines)

        # Initialize client
        client = OpenAI(api_key=api_key, base_url=base_url)

        # Convert and upload each image
        file_ids = []
        for png_path in screenshot_paths:
            # Convert to WebP
            webp_path = convert_to_webp(png_path)
            if not webp_path:
                webp_path = png_path  # Use original if conversion fails

            # Upload file
            with open(webp_path, "rb") as f:
                file_object = client.files.create(
                    file=FilePath(webp_path),
                    purpose="video"  # Kimi uses video purpose for images
                )
            file_ids.append(file_object.id)

        # Build content with file references
        content = []
        for file_id in file_ids:
            content.append({
                "type": "video_url",
                "video_url": {
                    "url": f"ms://{file_id}"
                }
            })

        # Build prompt with context
        # Load prompt template from config
        prompt_config = config.get("record_prompt", {})
        system_prompt = _require_prompt_key(prompt_config, "system")
        user_template = _require_prompt_key(prompt_config, "user")

        # Replace placeholders
        user_prompt = user_template.format(
            time=current_time,
            context=context_str
        )

        content = []
        for file_id in file_ids:
            content.append({
                "type": "video_url",
                "video_url": {
                    "url": f"ms://{file_id}"
                }
            })

        content.append({
            "type": "text",
            "text": user_prompt
        })

        # Use non-stream completion and collect final assistant content only
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": content
                }
            ],
            max_tokens=200000,
            stream=False
        )

        if not resp.choices:
            print("No choices returned from model; skipping record")
            return None

        msg = resp.choices[0].message
        if not msg:
            print("No message returned from model; skipping record")
            return None

        # Use final message content only. Do not use reasoning output in records.
        final_content = _delta_to_text(getattr(msg, "content", None))

        # Use final content only
        description = final_content.strip()
        if not description:
            print("No final content returned from model; skipping record")
            return None

        # Clean up: take only first sentence, max 100 chars
        description = description.split('\n')[0][:100]

        return {
            "description": description,
            "model": model,
            "success": True
        }

    except Exception as e:
        print(f"Error: {e}")
        return None


# Backward compatibility
def analyze_screenshot(screenshot_path: str) -> dict | None:
    """Analyze a single screenshot (for backward compatibility)."""
    return analyze_screenshots([screenshot_path])


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python analyzer.py <screenshot_path> [screenshot_path2 ...]")
        sys.exit(1)

    paths = sys.argv[1:]
    result = analyze_screenshots(paths)
    if result:
        print(f"Description: {result['description']}")
    else:
        print("Analysis failed")
