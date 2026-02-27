#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   BASE_URL=http://127.0.0.1:18001 ./scripts/create_draw_star_task.sh
# or
#   ./scripts/create_draw_star_task.sh http://127.0.0.1:18001

BASE_URL="${1:-${BASE_URL:-http://127.0.0.1:18001}}"

cat <<'JSON' | curl -sS -X POST "$BASE_URL/api/tasks" -H "Content-Type: application/json" -d @-
{
  "apiVersion": "cron-agent/v1",
  "kind": "CronTask",
  "metadata": {
    "id": "e2e-agent-draw-star",
    "name": "E2E Agent Draw Star",
    "enabled": true
  },
  "spec": {
    "mode": "agent",
    "schedule": {
      "cron": "*/30 * * * *",
      "timezone": "Asia/Shanghai"
    },
    "input": {
      "prompt": "在当前项目根目录创建文件 generated/draw_star.py。要求: 1) 仅用 Python 标准库; 2) 提供函数 draw_star(size=9) 返回字符串形式的五角星(ASCII); 3) 在 main 中打印星星并把同样内容写入 generated/star_output.txt; 4) 代码可直接 python3 generated/draw_star.py 运行。只进行文件修改并在最终回复中简短说明已创建文件。"
    },
    "execution": {
      "timeoutSeconds": 600,
      "retry": {
        "maxAttempts": 1,
        "backoffSeconds": 0
      }
    },
    "modeConfig": {
      "agent": {
        "provider": "codex_cli",
        "model": "gpt-5-codex",
        "sandboxMode": "workspace-write"
      }
    },
    "output": {
      "sink": "file",
      "pathTemplate": "artifacts/{task_id}/{run_id}/result.txt",
      "format": "text"
    },
    "logging": {
      "eventJsonlPath": "logs/runs/{date}.jsonl"
    }
  }
}
JSON

echo
echo "Task create request sent to: $BASE_URL/api/tasks"
