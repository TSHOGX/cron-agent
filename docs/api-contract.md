# API Contract

本文档定义 `cron_agent` 当前对外 API 的关键契约，重点覆盖任务触发与 process 会话控制。

## 1. Task Run Contract

### `POST /api/tasks/<task_id>/run`

- 语义：异步触发一次任务运行（创建 `run_id`，执行过程由后台线程推进）。
- 成功状态码：`202`
- 失败状态码：`400`

返回体固定字段：

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

失败示例（任务不存在）：

```json
{
  "success": false,
  "task_id": "not-found",
  "run_id": null,
  "process_id": null,
  "status": "failed",
  "error": "task not found: not-found",
  "error_code": "task_not_found",
  "output_path": null,
  "trace_path": null
}
```

说明：

- `process_id` 在异步触发响应中通常为 `null`，执行阶段生成后可通过 task status 或 process list 查询。
- 结果产出（成功/失败）不在该接口阻塞返回。

## 2. Process Session Contract

### `POST /api/process/start`

- 成功状态码：`200`
- 失败状态码：`400`

请求（task 模式）：

```json
{
  "task_id": "capture-analyze",
  "mode": "agent",
  "prompt": "optional override",
  "timeout_seconds": 300
}
```

请求（adhoc 模式）：

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

成功响应：

```json
{
  "success": true,
  "process_id": "proc_1234567890ab",
  "run_id": "run_20260227_120000_abc123"
}
```

说明：

- 当传入 `task_id` 时，会复用任务有效性与并发锁校验，不会绕过任务锁。

### `GET /api/process/list`

- 成功状态码：`200`
- 查询参数：`task_id?` `run_id?` `status?` `limit?`
- 返回：process 摘要数组。

### `GET /api/process/poll/<process_id>`

- 成功状态码：`200`
- 不存在：`404`
- 返回包含 `found` 与 process 当前状态。

### `GET /api/process/log/<process_id>?offset=0&limit=200`

- 成功状态码：`200`
- 不存在：`404`
- 返回字段：`found/process_id/items/next_offset/eof`

### `POST /api/process/write/<process_id>`

- 请求体：`{"data":"raw text"}`
- 语义：写入 stdin，不自动追加换行。
- 成功状态码：`200`
- 失败状态码：`400`

### `POST /api/process/submit/<process_id>`

- 请求体：`{"data":"line"}`
- 语义：写入 stdin 并自动追加 `\n`。
- 成功状态码：`200`
- 失败状态码：`400`

### `POST /api/process/kill/<process_id>`

- 请求体：`{"signal":"TERM"}` 或 `{"signal":"KILL"}`
- 成功状态码：`200`
- 失败状态码：`400`

## 3. Run Query Contract

### `GET /api/runs?task_id=<id>&limit=<n>`

- 成功状态码：`200`
- 查询参数：`task_id?` `limit?`
- 返回：run 摘要数组（聚合自 `state.json` 与 `trace_index`）。

### `GET /api/runs/<run_id>`

- 成功状态码：`200`
- 不存在：`404`
- 返回字段：
  - `found`
  - `run`（run 摘要）
  - `process`（process poll 结果）
  - `process_log_preview`（前 100 条日志）

## 4. Runtime Semantics

- 一次任务触发会创建一个 `run_id`，执行阶段创建一个 `process_id`（当前实现为 `1 run -> 1 process`）。
- task 运行状态与 process 运行状态通过 `runtime/state.json` 同步。
- 服务重启后，历史 `running/starting` process 会标记为失败（`process lost after service restart`）。

## 5. Compatibility Notes

- `GET /api/runs/<run_id>/events` 仍为 deprecated（返回 `410`）。
- `runBackend=tmux` 已为 run-once 语义，不再使用 tmux 内部 while-loop 调度。
