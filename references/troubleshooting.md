# Troubleshooting

## Service unreachable

- Check `GET /api/status`.
- Confirm the service process is up and listening on expected host/port.

## Task creation/update fails (400)

- Validate `apiVersion=cron-agent`, `kind=CronTask`, `runBackend=cron`.
- Ensure `metadata.id` is slug-like.
- Ensure `spec.schedule.cron` is valid.

## Run fails immediately

- Check `GET /api/tasks/<task_id>/status` for `errors`.
- Check `GET /api/runs/<run_id>` for run/process summary.
- Check `GET /api/process/log/<process_id>` for exact stderr.

## Task appears stuck running

- Query `GET /api/process/poll/<process_id>`.
- If process is lost after restart, runtime should auto-mark failed.

## Scheduler mismatch

- Re-run `POST /api/scheduler/sync`.
- Inspect `GET /api/scheduler/status` and verify job count.
