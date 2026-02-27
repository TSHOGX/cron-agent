# Cron Agent

自动截图并分析当前工作内容，持续写入活动记录，按日/周/月与时段生成总结。

## 核心能力

- 多屏截图（macOS `screencapture`）
- 使用 Moonshot Kimi（OpenAI SDK）分析截图
- 记录写入 JSONL（按天分文件）
- 支持 Cron Manager 任务编排（YAML 定义 + 双模式执行）
- 自动生成日报/周报/月报/上午下午晚上小结
- Web 控制台管理服务、配置、提示词和历史数据
- 缺失总结自动补齐（catch-up）

## 运行架构

- `cron_manager.py`：统一任务控制平面（任务 CRUD、同步、状态、运行日志）
- `tmux backend`：用于 interval 循环任务（如截图分析）
- `cron backend`：用于 cron 表达式任务（如日报/周报/月报）
- `job_workers.py`：任务执行单元（截图分析、日报/周报/月报）
- `Flask`：提供控制台与 API（`api.py`，默认 `18001` 端口）

## 环境要求

- Python 3.10+
- macOS（依赖 `screencapture`）
- `tmux`
- `crontab`（系统 cron）
- `codex` CLI（`mode=agent` 执行所需）
- 屏幕录制权限（终端/运行进程必须授权）

## 快速开始

```bash
# 1) 安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install openai pillow

# 2) 配置 API Key（编辑 config.json 的 api.auth_token）
#    或通过控制台 /api/config 更新

# 3) 启动 Web 控制台
python3 api.py
```

打开 `http://localhost:18001`。

## 常用命令

```bash
# Cron Manager 任务列表
python3 cron_manager.py list-tasks

# 校验任务 YAML
python3 cron_manager.py validate tasks/capture_analyze.yaml
python3 cron_manager.py validate tasks/summary_daily.yaml
python3 cron_manager.py validate tasks/summary_weekly.yaml
python3 cron_manager.py validate tasks/summary_monthly.yaml

# 手动触发一次任务
python3 cron_manager.py run-task capture-analyze --trigger manual
python3 cron_manager.py run-task summary-daily --trigger manual
python3 cron_manager.py run-task summary-weekly --trigger manual
python3 cron_manager.py run-task summary-monthly --trigger manual

# 任务暂停 / 恢复 / 删除
python3 cron_manager.py pause capture-analyze
python3 cron_manager.py resume capture-analyze
python3 cron_manager.py delete capture-analyze

# 同步任务到两种后端（tmux + cron）与查看状态
python3 cron_manager.py sync
python3 cron_manager.py backends-status

# 迁移后四个核心任务 smoke test
./scripts/test_migrated_tasks.sh

# 快速 E2E（仅测 capture + daily）
FAST=1 ./scripts/test_migrated_tasks.sh

# 自检与清理
python3 job_workers.py capture-analyze
python3 job_workers.py cleanup
```

## 配置说明

配置文件：`config.json`

关键字段：

- `capture_interval`：截图循环间隔（秒，`tmux` 服务使用）
- `model`：模型名（默认 `kimi-k2.5`）
- `output_root`：运行数据根目录（默认 `./.cron_agent_data`）
- `daily_summary_time`：日报触发时间（`HH:MM`）
- `weekly_summary_day`：周报触发日（`monday`...`sunday`）
- `monthly_summary_day`：月报触发日（1-28/31）
- `time_periods`：时段范围（`morning/afternoon/evening`）
- `api.auth_token`：Moonshot API Key
- `api.base_url`：默认 `https://api.moonshot.cn/v1`
- `record_prompt`：截图分析提示词
- `summary_prompt`：各类总结提示词

运行数据目录说明：

- 默认所有运行时数据写入 `output_root`，包括 `logs/runtime/artifacts/records/journal/messages`
- `records_dir/journal_dir/messages_dir` 为相对路径时，将解析为 `output_root` 下子目录
- 首次启动会自动把仓库旧目录中的历史数据迁移到 `output_root`

### Cron Manager 任务字段（YAML）

每个任务必须配置“在哪里跑”：

- `spec.runBackend: cron | tmux`
- 当 `runBackend=cron`：使用 `spec.schedule.cron`（5 段 cron 表达式）
- 当 `runBackend=tmux`：使用 `spec.schedule.intervalSeconds`（正整数秒）

执行模式语义：

- `spec.mode=agent`：由 coding agent（SDK）执行任务，不再通过 `commandTemplate` 直接执行本地命令。
- `spec.mode=llm`：由 LLM 文本生成器执行。
- `spec.mode=agent` 下若配置 `spec.modeConfig.agent.commandTemplate`，校验会失败（已废弃）。

示例（cron）：

```yaml
spec:
  runBackend: cron
  schedule:
    cron: "*/30 * * * *"
    timezone: "Asia/Shanghai"
```

示例（tmux）：

```yaml
spec:
  runBackend: tmux
  schedule:
    intervalSeconds: 900
```

说明：`pause/delete` 后建议执行一次 `python3 cron_manager.py sync`，确保系统后端状态与任务文件一致。

提示词占位符：

- `record_prompt.user`：`{time}`、`{context}`
- `summary_prompt.daily`：`{date}`、`{records}`
- `summary_prompt.weekly/monthly`：`{date_range}`、`{notes}`
- `summary_prompt.time_of_day`：`{label}`、`{records}`

## LLM 调用策略

- `analyzer.py` 与 `summarizer.py` 均使用非流式调用（`stream=False`）。
- 只使用最终回答文本（`message.content`）作为结果，不使用 `reasoning_content`。
- 当前两者 `max_tokens` 均固定为 `200000`，不做二次放大重试。

## API 一览

- `GET /api/status`：统一服务状态（cron manager + backends）
- `GET|POST /api/config`：读取/更新配置
- `GET|POST /api/record_prompt`：读取/更新截图提示词
- `GET|POST /api/summary_prompt`：读取/更新总结提示词
- `GET /api/records?date=YYYY-MM-DD&limit=50`：活动记录
- `GET /api/records/dates`：可用记录日期
- `GET /api/journal/<period>`：总结文件列表（`daily|weekly|monthly|period`）
- `GET /api/journal/<period>/<filename>`：总结内容
- `GET /api/messages`：消息列表
- `GET /api/tasks`：Cron Manager 任务列表
- `POST /api/tasks`：创建任务
- `PUT /api/tasks/<task_id>`：更新任务
- `DELETE /api/tasks/<task_id>`：删除任务
- `POST /api/tasks/<task_id>/pause`：暂停任务
- `POST /api/tasks/<task_id>/resume`：恢复任务
- `POST /api/tasks/<task_id>/run`：立即执行任务
- `GET /api/tasks/<task_id>/status`：任务状态
- `POST /api/tasks/sync`：同步 YAML 任务到 tmux + crontab
- `GET /api/backends/status`：查看后端状态（tmux + cron）
- `POST /api/backends/sync`：同步后端（tmux + cron）
- `GET /api/runs`：任务运行摘要（JSONL 聚合）
- `GET /api/runs/<run_id>/events`：单次运行事件流

旧 demo 调度接口已日落：不再提供 `capture/summarizer/services/cron` 这些旧启动停止接口。

状态页按钮已切换为新接口语义：

- 启动/停止截图：`pause/resume capture-analyze` + `POST /api/tasks/sync`
- 启动/停止汇总：`pause/resume summary-*` + `POST /api/tasks/sync`

## 目录结构

```text
cron_agent/
├── api.py
├── job_workers.py
├── analyzer.py
├── recorder.py
├── summarizer.py
├── storage_paths.py
├── config.json
├── requirements.txt
├── cron_manager.py
├── tasks/
│   └── *.yaml
├── .cron_agent_data/
│   ├── records/
│   ├── runtime/
│   ├── logs/
│   │   └── runs/
│   ├── artifacts/
│   ├── journal/
│   │   ├── daily/
│   │   ├── weekly/
│   │   ├── monthly/
│   │   └── period/
│   └── messages/
├── web/
│   ├── templates/
│   └── static/
```

截图文件默认写入系统临时目录（如 `/tmp` 或 macOS 的 `/var/folders/.../T`）。

## 数据格式示例

活动记录（`.cron_agent_data/records/YYYY-MM-DD.jsonl` 每行一条 JSON）：

说明：活动记录仅保留时间与描述，不保存截图文件路径。

```json
{
  "timestamp": "2026-02-26T17:20:00.123456",
  "description": "正在修改 README 文档并核对 API 路由"
}
```

## 注意事项

- 请不要把真实 `api.auth_token` 提交到代码仓库。
- 首次运行失败时，先检查：
  - macOS 屏幕录制权限
  - `tmux`/`cron` 是否可用
  - API Key 与 `base_url` 是否正确
