# cron-agent-manager

`cron-agent-manager` is a self-contained skill and service for running cron-scheduled AI agent tasks with a local HTTP API.

It is designed to run under `pm2`, keep its runtime state inside the skill directory, and manage one active run per task at a time.

## What It Does

- Runs AI agent tasks on cron schedules
- Exposes task, process, and run management over HTTP
- Stores runtime state, logs, and artifacts locally in `.cron_agent_data/`
- Supports multiple agent CLIs, including `codex`, `claude`, `gemini`, `opencode`, and `pi`

## Directory Layout

```text
cron-agent-manager/
├── SKILL.md
├── README.md
├── ecosystem.config.js
├── assets/
│   ├── api.py
│   ├── cron_manager.py
│   ├── process_manager.py
│   ├── storage_paths.py
│   └── requirements.txt
├── scripts/
│   ├── install_deps.sh
│   └── create-task-from-yaml.py
├── references/
│   ├── api-contract.md
│   └── task-spec.md
└── .cron_agent_data/
    ├── tasks/
    ├── runtime/
    ├── logs/
    └── artifacts/
```

## Requirements

- macOS or Linux shell environment
- `python3`
- `pip3`
- `pm2`
- Agent CLI(s) you plan to use, such as `codex` or `claude`

## Install

```bash
cd /path/to/cron-agent-manager
./scripts/install_deps.sh
```

This installs Python dependencies from `assets/requirements.txt` and creates the local data directories.

## Start the Service

```bash
pm2 start ecosystem.config.js
pm2 status cron-agent
curl http://localhost:18001/api/status
```

Useful PM2 commands:

- `pm2 restart cron-agent`
- `pm2 stop cron-agent`
- `pm2 logs cron-agent`

## Create a Task

Write a task YAML file and submit it through the helper script:

```bash
./scripts/create-task-from-yaml.py /path/to/task.yaml
```

Minimal example:

```yaml
apiVersion: cron-agent/v1
kind: CronTask
metadata:
  id: summary-daily
  name: Summary Daily
  enabled: true
spec:
  mode: agent
  runBackend: cron
  schedule:
    cron: "0 5 * * *"
    timezone: Asia/Shanghai
  input:
    prompt: "Generate daily summary..."
  execution:
    timeoutSeconds: 180
    workingDirectory: "."
  modeConfig:
    agent:
      provider: codex
      model: gpt-5-codex
      sandboxMode: danger-full-access
  output:
    sink: file
    pathTemplate: artifacts/{task_id}/{run_id}/result.txt
    format: text
```

## `sandboxMode`

`sandboxMode` controls how provider CLIs are invoked.

- `danger-full-access`
  Keeps autonomous, non-interactive execution as the default.
- `workspace-write`
  Preserves compatibility with older restricted-write task definitions.

Current behavior:

- `codex`
  - `danger-full-access` -> `codex exec --yolo`
  - `workspace-write` -> `codex exec --skip-git-repo-check --sandbox workspace-write`
- `claude`
  - `danger-full-access` -> `claude -p --dangerously-skip-permissions`
  - `workspace-write` -> `claude -p --permission-mode acceptEdits`
- `gemini`
  - `danger-full-access` -> yolo approval mode
  - `workspace-write` -> no automatic yolo flag

If `sandboxMode` is omitted, the default is `danger-full-access`.

## API Surface

Base URL:

```text
http://localhost:18001
```

Common endpoints:

- `GET /api/status`
- `GET /api/tasks`
- `POST /api/tasks`
- `PUT /api/tasks/<task_id>`
- `POST /api/tasks/<task_id>/run`
- `GET /api/tasks/<task_id>/status`
- `GET /api/process/list`
- `GET /api/process/poll/<process_id>`
- `GET /api/runs?task_id=<id>`

Full API details:

- [references/api-contract.md](references/api-contract.md)

## Runtime Data

All generated state stays local and is ignored by Git:

- `.cron_agent_data/tasks/`
- `.cron_agent_data/runtime/`
- `.cron_agent_data/logs/`
- `.cron_agent_data/artifacts/`

This keeps task configs, run metadata, and output files colocated with the skill without polluting the repo history.

## Notes

- The service enforces single-instance execution per `task_id`.
- `spec.schedule.maxConcurrency` is forced to `1` at runtime.
- Task YAML files may contain secrets indirectly via environment references; avoid committing plaintext credentials.

## References

- [SKILL.md](SKILL.md)
- [references/task-spec.md](references/task-spec.md)
- [references/api-contract.md](references/api-contract.md)
