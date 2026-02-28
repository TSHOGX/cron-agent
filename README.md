# Cron Agent

通用的 cron 任务控制平面，支持 `agent` 与 `llm` 两种执行模式。
本仓库同时作为 skill 根目录使用（含 `SKILL.md` 与 `references/`）。

## 架构

- `runtime/cron_manager.py`: 任务控制面（YAML 任务、校验、执行、调度同步、运行状态）
- `runtime/api.py`: Flask API
- `runtime/process_manager.py`: process 会话层（start/list/poll/log/write/submit/kill）
- `runtime/recorder.py`: records/journal/messages 读取工具
- `runtime/storage_paths.py`: 本地数据目录工具

## 当前约束

- 只支持 `cron` 调度后端（不支持 tmux）
- 任务 schema 使用 `apiVersion: cron-agent`
- 所有运行数据位于本地目录 `.cron_agent_data/`
- 任务定义位于 `.cron_agent_data/tasks/*.yaml`

## 目录

```text
cron-agent/
├── SKILL.md
├── references/
├── runtime/
│   ├── api.py
│   ├── cron_manager.py
│   ├── process_manager.py
│   ├── recorder.py
│   └── storage_paths.py
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
python3 -m runtime.api
```

API 默认监听 `http://localhost:18001`。

## CLI

```bash
# 查看任务
python3 -m runtime.cron_manager list-tasks

# 运行任务
python3 -m runtime.cron_manager run-task <task_id> --trigger manual

# 暂停/恢复/删除
python3 -m runtime.cron_manager pause <task_id>
python3 -m runtime.cron_manager resume <task_id>
python3 -m runtime.cron_manager delete <task_id>

# 同步 / 查看调度器状态
python3 -m runtime.cron_manager sync
python3 -m runtime.cron_manager scheduler-status
```

## API 一览

- `GET /api/status`
- `GET /api/scheduler/status`
- `POST /api/scheduler/sync`
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
- `POST /api/process/start`
- `GET /api/process/list`
- `GET /api/process/poll/<process_id>`
- `GET /api/process/log/<process_id>?offset=0&limit=200`
- `POST /api/process/write/<process_id>`
- `POST /api/process/submit/<process_id>`
- `POST /api/process/kill/<process_id>`
- `GET /api/runs`
- `GET /api/runs/<run_id>`
- `GET /api/records`
- `GET /api/logs`
- `GET /api/records/dates`
- `GET /api/journal/<period>`
- `GET /api/journal/<period>/<path:filename>`
- `GET /api/messages`

接口说明见 [API Surface](references/api-surface.md)。

## 任务 YAML 示例

```yaml
apiVersion: cron-agent
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

- `.cron_agent_data/` 与 `.cron_agent_data/tasks/*.yaml` 已忽略，不会进入 Git。
- 若使用明文密钥，请仅写入本地任务 YAML，不要提交到仓库。
