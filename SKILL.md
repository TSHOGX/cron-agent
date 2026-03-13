---
name: cron-agent-manager
description: "Manages cron-based AI agent tasks with single-instance execution. Use when: (1) User wants to schedule AI agent tasks with cron expressions, (2) User needs to run Claude Code tasks on a schedule, (3) User asks to create/manage/list cron jobs for AI agents, (4) User wants a self-contained cron agent service managed by pm2"
---

# Cron Agent Manager

## Overview

Manages cron-scheduled AI agent tasks with a self-contained architecture. The service runs via pm2 in its own directory, with all data stored locally within the skill directory.

## Quick Start

1. **Install dependencies**:
   ```bash
   cd /path/to/cron-agent-manager
   ./scripts/install_deps.sh
   ```

2. **Start service**:
   ```bash
   pm2 start ecosystem.config.js
   ```

3. **Verify service**:
   ```bash
   curl http://localhost:18001/api/status
   ```

## Available Operations

### Service Management

- **Start service**: `pm2 start ecosystem.config.js`
- **Stop service**: `pm2 stop cron-agent`
- **Restart service**: `pm2 restart cron-agent`
- **View logs**: `pm2 logs cron-agent`
- **Check status**: `curl http://localhost:18001/api/status`

### Task Management

All task operations use the REST API at `http://localhost:18001`:

| Operation | API | Description |
|-----------|-----|-------------|
| List tasks | `GET /api/tasks` | List all tasks |
| Get task | `GET /api/tasks/<task_id>` | Get specific task |
| Create task | `POST /api/tasks` | Create new task |
| Update task | `PUT /api/tasks/<task_id>` | Update task |
| Delete task | `DELETE /api/tasks/<task_id>` | Delete task |
| Pause task | `POST /api/tasks/<task_id>/pause` | Pause scheduling |
| Resume task | `POST /api/tasks/<task_id>/resume` | Resume scheduling |
| Run task | `POST /api/tasks/<task_id>/run` | Trigger immediate run |

### Creating Tasks from YAML

You can create tasks from YAML files using the helper script:

```bash
./scripts/create-task-from-yaml.py <path-to-yaml-file>
```

This script converts YAML to JSON and calls the API. Example YAML files can be placed in `.cron_agent_data/tasks/` directory.

### Process Management

| Operation | API | Description |
|-----------|-----|-------------|
| List processes | `GET /api/process/list` | List running processes |
| Poll status | `GET /api/process/poll/<process_id>` | Get process status |
| Get logs | `GET /api/process/log/<process_id>` | Stream process logs |
| Kill process | `POST /api/process/kill/<process_id>` | Kill running process |

### Query Runs

| Operation | API | Description |
|-----------|-----|-------------|
| List runs | `GET /api/runs?task_id=<id>` | List task runs |
| Get run | `GET /api/runs/<run_id>` | Get run details |

## Data Storage

All data is stored in `.cron_agent_data/` within the skill directory:

```
.cron_agent_data/
├── tasks/          # Task YAML configurations (*.yaml)
├── runtime/       # Runtime state (state.json)
├── logs/          # Process logs
└── artifacts/     # Task output artifacts
```

## Task Configuration

See [references/task-spec.md](references/task-spec.md) for task YAML specification.

## API Reference

See [references/api-contract.md](references/api-contract.md) for complete API documentation.

## Scripts

### ecosystem.config.js
PM2 configuration for running the service. Sets `CRON_AGENT_DATA_DIR` environment variable to store data in skill directory.

### scripts/install_deps.sh
Installs Python dependencies from assets/requirements.txt.
