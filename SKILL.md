---
name: cron-agent
description: Manage and observe a pm2-deployed cron-agent service via HTTP API only, including task CRUD, scheduler sync/status, run triggering, process/session control, and run/log inspection.
---

# Cron Agent Skill

Use this skill when user wants to manage cron tasks on a running cron-agent service.
Default interaction mode is API-only (no shell wrapper scripts required).

## Prerequisites

- Service is running and reachable.
- Base URL defaults to `http://127.0.0.1:18001`.
- If user provides another endpoint, use it.

## Workflow

1. Check service health:
   - `GET /api/status`
   - `GET /api/scheduler/status`
2. Inspect tasks:
   - `GET /api/tasks`
   - `GET /api/tasks/<task_id>`
3. Manage tasks:
   - create/update/delete/pause/resume via `/api/tasks*`
4. Apply scheduler changes:
   - `POST /api/scheduler/sync`
5. Trigger and inspect runs:
   - `POST /api/tasks/<task_id>/run`
   - `GET /api/tasks/<task_id>/status`
   - `GET /api/runs` / `GET /api/runs/<run_id>`
6. Troubleshoot execution:
   - `GET /api/process/list`
   - `GET /api/process/poll/<process_id>`
   - `GET /api/process/log/<process_id>`

## References

- API surface and request patterns: `references/api-surface.md`
- Task schema constraints and examples: `references/task-schema.md`
- End-to-end operation playbooks: `references/workflows.md`
- Failure diagnosis checklist: `references/troubleshooting.md`
