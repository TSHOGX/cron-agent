# API Surface

Base URL: `http://127.0.0.1:18001`

## Service

- `GET /api/status`

## Scheduler

- `GET /api/scheduler/status`
- `POST /api/scheduler/sync`
- Status codes:
  - `GET`: `200`
  - `POST`: `200` or `500`
- Key fields:
  - status: `backend/installed/count/jobs`
  - sync: `success/scheduler`

## Tasks

- `GET /api/tasks`
- `GET /api/tasks/<task_id>`
- `POST /api/tasks`
- `PUT /api/tasks/<task_id>`
- `DELETE /api/tasks/<task_id>`
- `GET /api/tasks/<task_id>/settings`
- `PUT /api/tasks/<task_id>/settings`
- `POST /api/tasks/<task_id>/pause`
- `POST /api/tasks/<task_id>/resume`
- `POST /api/tasks/<task_id>/run`
- `GET /api/tasks/<task_id>/status`
- Status codes (common):
  - list/get/settings/status: `200` (`404` if task not found)
  - create/update/settings-put/run: `202/200/400` depending endpoint
  - delete/pause/resume: `200` or `404`
- Run semantics:
  - `POST /api/tasks/<task_id>/run` is async.
  - Success returns `202` with `run_id` and `status=running`.
  - Final result must be checked via task status/runs/process.

## Process Sessions

- `POST /api/process/start`
- `GET /api/process/list`
- `GET /api/process/poll/<process_id>`
- `GET /api/process/log/<process_id>?offset=0&limit=200`
- `POST /api/process/write/<process_id>`
- `POST /api/process/submit/<process_id>`
- `POST /api/process/kill/<process_id>`
- Status codes:
  - start/list: `200` (`start` returns `400` on bad payload)
  - poll/log: `200` or `404`
  - write/submit/kill: `200` or `400`
- Key fields:
  - start: `success/process_id/run_id`
  - poll: `found/status/error/returncode`
  - log: `items/next_offset/eof`

## Runs and Records

- `GET /api/runs`
- `GET /api/runs/<run_id>`
- `GET /api/records`
- `GET /api/logs`
- `GET /api/records/dates`
- `GET /api/journal/<period>`
- `GET /api/journal/<period>/<path:filename>`
- `GET /api/messages`
- Status codes:
  - runs list/records/dates/journal list/messages: `200`
  - run detail/journal file: `200` or `404`

## Notes

- Deprecated/removed endpoints are intentionally unavailable (`/api/backends/*`, `/api/runs/<run_id>/events`).
- Task trigger is async; query status and runs/process for progress.
