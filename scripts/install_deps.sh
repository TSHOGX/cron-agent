#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

cd "$SKILL_DIR"

echo "Installing Python dependencies..."
if [ -f "assets/requirements.txt" ]; then
    pip3 install -r assets/requirements.txt
else
    echo "Warning: requirements.txt not found"
fi

echo "Creating data directories..."
mkdir -p "$SKILL_DIR/.cron_agent_data"/{tasks,runtime,logs,artifacts}

echo "Done."
