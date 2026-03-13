#!/usr/bin/env python3
"""Helper to create cron tasks from YAML files."""

import json
import sys
import urllib.request
import urllib.error

def create_task_from_yaml(yaml_file: str, api_url: str = "http://localhost:18001/api/tasks"):
    """Read YAML file and create task via API."""
    import yaml

    with open(yaml_file, 'r') as f:
        task = yaml.safe_load(f)

    # Convert to JSON and send to API
    data = json.dumps(task).encode('utf-8')

    req = urllib.request.Request(
        api_url,
        data=data,
        headers={'Content-Type': 'application/json'}
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return result.get('success', False)
    except urllib.error.HTTPError as e:
        error = json.loads(e.read().decode('utf-8'))
        print(json.dumps(error, indent=2, ensure_ascii=False))
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <yaml-file>")
        sys.exit(1)

    success = create_task_from_yaml(sys.argv[1])
    sys.exit(0 if success else 1)
