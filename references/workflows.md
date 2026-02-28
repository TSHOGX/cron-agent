# Workflows

## Create and enable a task

1. `POST /api/tasks` with full task payload
2. `POST /api/scheduler/sync`
3. `GET /api/scheduler/status` to confirm installed cron jobs

## Update task settings

1. `PUT /api/tasks/<task_id>/settings`
2. `POST /api/scheduler/sync`
3. `GET /api/tasks/<task_id>/status`

## Manual run and tracking

1. `POST /api/tasks/<task_id>/run`
2. Poll `GET /api/tasks/<task_id>/status`
3. Query `GET /api/runs?task_id=<task_id>&limit=20`
4. For process-level details: `GET /api/process/list?task_id=<task_id>` and log endpoint

## Pause / resume

1. `POST /api/tasks/<task_id>/pause`
2. `POST /api/scheduler/sync`
3. Resume with `/resume`, then re-sync
