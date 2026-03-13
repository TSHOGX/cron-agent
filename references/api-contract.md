# API Contract

本文档对应当前 `api.py` 实现，覆盖全部公开路由。

## 1. Conventions

- Base URL: `http://localhost:18001`
- Content-Type: `application/json`
- 布尔查询/状态字段大小写敏感，建议直接按示例值使用。
- 失败返回通常包含 `success: false` 或 `found: false` 与 `error` 字段。
- `POST /api/tasks/<task_id>/run` 是异步触发：返回成功仅表示已受理，不表示任务已完成。

## 2. Endpoint Index

### Service

- `GET /` 健康入口，返回服务元信息。
- `GET /api/status` 返回任务总览统计。

### Tasks

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

### Process

- `POST /api/process/start`
- `GET /api/process/list`
- `GET /api/process/poll/<process_id>`
- `GET /api/process/log/<process_id>?offset=0&limit=200`
- `POST /api/process/write/<process_id>`
- `POST /api/process/submit/<process_id>`
- `POST /api/process/kill/<process_id>`

### Runs

- `GET /api/runs?task_id=<id>&limit=<n>`
- `GET /api/runs/<run_id>`

## 3. Task APIs

### `GET /api/tasks`

- 状态码：`200`
- 返回：任务数组；每项包含 `_valid`、`_errors` 校验信息。

### `GET /api/tasks/<task_id>`

- 状态码：`200` / `404`
- `404` 示例：`{"success": false, "error": "task not found"}`

### `POST /api/tasks`

- 状态码：`200` / `400`
- 请求体：任务对象（可不传 `metadata.id`，后端会根据名称自动 slug）。
- 成功返回：

```json
{
  "success": true,
  "task": { "...": "..." }
}
```

### `PUT /api/tasks/<task_id>`

- 状态码：`200` / `400`
- 请求体：任务对象，路径中的 `<task_id>` 会强制覆盖 `metadata.id`。

### `DELETE /api/tasks/<task_id>`

- 状态码：`200` / `404` / `500`
- 说明：文件删除成功但 crontab 同步失败时会返回错误并附带 `task_deleted: true`。

### `GET /api/tasks/<task_id>/settings`

- 状态码：`200` / `404`
- 返回：
  - `success`
  - `task_id`
  - `settings`（`mode/runBackend/schedule/input/execution/modeConfig/output`）

### `PUT /api/tasks/<task_id>/settings`

- 状态码：`200` / `400`
- 请求体：局部更新对象，支持深度合并字典字段。
- 可更新键：`mode`、`runBackend`、`schedule`、`input`、`execution`、`modeConfig`、`output`。

### `POST /api/tasks/<task_id>/pause`

- 状态码：`200` / `404`

### `POST /api/tasks/<task_id>/resume`

- 状态码：`200` / `404`

### `POST /api/tasks/<task_id>/run`

- 状态码：`202` / `400`
- 语义：异步触发一次任务运行（创建 `run_id`，后台线程执行）。
- 返回体固定字段：

```json
{
  "success": true,
  "task_id": "capture-analyze",
  "run_id": "run_20260227_120000_abc123",
  "process_id": null,
  "status": "running",
  "error": null,
  "error_code": null,
  "output_path": null,
  "trace_path": null
}
```

- 常见 `error_code`：
  - `task_not_found`
  - `task_invalid`
  - `task_disabled`
  - `task_running`
  - `async_start_failed`

### `GET /api/tasks/<task_id>/status`

- 状态码：`200` / `404`
- 返回字段：
  - `found`
  - `task_id`
  - `enabled`
  - `paused`
  - `valid`
  - `errors`
  - `runtime`

## 4. Process APIs

### `POST /api/process/start`

- 状态码：`200` / `400`
- 请求体（task 模式）：

```json
{
  "task_id": "capture-analyze",
  "mode": "agent",
  "prompt": "optional override",
  "timeout_seconds": 300,
  "run_id": "optional_custom_run_id"
}
```

- 请求体（adhoc 模式）：

```json
{
  "mode": "agent",
  "prompt": "do something",
  "workdir": ".",
  "timeout_seconds": 120,
  "agent": {
    "provider": "codex",
    "model": "gpt-5-codex"
  }
}
```

- 说明：
  - 传 `task_id` 时会走任务校验与单实例锁，不绕过任务并发控制。
  - 未传 `task_id` 时按 adhoc 进程启动，默认 `task_id=adhoc`。

### `GET /api/process/list`

- 状态码：`200`
- 查询参数：
  - `task_id` 可选
  - `run_id` 可选
  - `status` 可选
  - `limit` 可选，默认 `100`
- 返回：process 摘要数组。

### `GET /api/process/poll/<process_id>`

- 状态码：`200` / `404`
- 返回：包含 `found` 与 process 当前状态。

### `GET /api/process/log/<process_id>`

- 状态码：`200` / `404`
- 查询参数：
  - `offset` 可选，默认 `0`
  - `limit` 可选，默认 `200`
- 返回字段：`found/process_id/items/next_offset/eof`

### `POST /api/process/write/<process_id>`

- 状态码：`200` / `400`
- 请求体：`{"data":"raw text"}`
- 语义：写入 stdin，不自动追加换行。

### `POST /api/process/submit/<process_id>`

- 状态码：`200` / `400`
- 请求体：`{"data":"line"}`
- 语义：写入 stdin 并自动追加 `\n`。

### `POST /api/process/kill/<process_id>`

- 状态码：`200` / `400`
- 请求体：`{"signal":"TERM"}` 或 `{"signal":"KILL"}`（默认 `TERM`）。

## 5. Run APIs

### `GET /api/runs`

- 状态码：`200`
- 查询参数：
  - `task_id` 可选
  - `limit` 可选，默认 `100`
- 返回：run 摘要数组（聚合 `runtime/state.json` 与 `trace_index`）。

### `GET /api/runs/<run_id>`

- 状态码：`200` / `404`
- 返回字段：
  - `found`
  - `run`
  - `process`
  - `process_log_preview`（最多 100 条）

## 6. Runtime Semantics

- 一次任务触发会创建一个 `run_id`，执行阶段创建一个 `process_id`（当前实现是 `1 run -> 1 process`）。
- 任务运行状态与 process 状态通过 `runtime/state.json` 同步。
- 服务重启后，历史 `running/starting` process 会标记为失败（`process lost after service restart`）。
- `spec.runBackend` 当前仅支持 `cron`。
