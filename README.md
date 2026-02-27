# Cron Agent

面向 `cron`/`tmux` 的通用任务管理系统，支持 `agent` 与 `llm` 两种执行模式。

## 当前架构

- `cron_manager.py`: 任务控制平面（YAML 任务加载、校验、执行、同步、运行状态）
- `api.py`: Flask API
- `process_manager.py`: process 会话层（start/list/poll/log/write/submit/kill）
- `recorder.py`: records/journal/messages 读写工具
- `storage_paths.py`: 本地数据目录与历史数据迁移工具

## 关键变化（v2）

- 已删除旧 worker 链路：`analyzer.py` / `summarizer.py` / `job_workers.py`
- 已删除全局配置：`config.json`
- 已删除示例目录：`generated/`、`scripts/`
- 所有配置改为 task 级别（`/api/tasks/<task_id>/settings`）
- 任务 YAML 改为本地目录：`.cron_agent_data/tasks/*.yaml`（已 gitignore）

## 目录

```text
cron_agent/
├── api.py
├── cron_manager.py
├── process_manager.py
├── recorder.py
├── storage_paths.py
└── .cron_agent_data/
    ├── tasks/
    ├── logs/
    ├── runtime/
    ├── artifacts/
    ├── records/
    ├── journal/
    └── messages/
```

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 api.py
```

API 服务默认监听 `http://localhost:18001`。

## 常用命令

```bash
# 查看任务
python3 cron_manager.py list-tasks

# 运行任务
python3 cron_manager.py run-task <task_id> --trigger manual

# 暂停/恢复/删除
python3 cron_manager.py pause <task_id>
python3 cron_manager.py resume <task_id>
python3 cron_manager.py delete <task_id>

# 同步到 cron + tmux
python3 cron_manager.py sync
python3 cron_manager.py backends-status
```

## API 一览

- `GET /api/status`
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
- `POST /api/tasks/sync`
- `GET /api/backends/status`
- `POST /api/backends/sync`
- `POST /api/process/start`
- `GET /api/process/list`
- `GET /api/process/poll/<process_id>`
- `GET /api/process/log/<process_id>?offset=0&limit=200`
- `POST /api/process/write/<process_id>`
- `POST /api/process/submit/<process_id>`
- `POST /api/process/kill/<process_id>`
- `GET /api/runs`
- `GET /api/runs/<run_id>`
- `GET /api/runs/<run_id>/events` (deprecated, returns 410)
- `GET /api/records`
- `GET /api/records/dates`
- `GET /api/journal/<period>`
- `GET /api/journal/<period>/<filename>`
- `GET /api/messages`

说明：`POST /api/tasks/<task_id>/run` 现在是异步触发，成功时返回 `202` 与 `run_id`（执行结果通过 `/api/tasks/<task_id>/status` 和 `/api/process/*` 查询）。

详细 API 契约与运行语义见：[API Contract](docs/api-contract.md)。

## 任务 YAML 示例

任务定义位于本地目录 `.cron_agent_data/tasks/*.yaml`。

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
      sandboxMode: workspace-write
      trace:
        enabled: true
        maxEventBytes: 262144
  output:
    sink: file
    pathTemplate: artifacts/{task_id}/{run_id}/result.txt
    format: text
```

## 注意

- `tasks/*.yaml` 与 `.cron_agent_data/tasks/*.yaml` 均已忽略，不会进入 Git。
- 若使用明文密钥，请仅写入本地任务 YAML，不要提交到仓库。
- `runBackend=tmux` 已改为 run-once 语义，不再通过 tmux 内部 while-loop 做周期调度。
