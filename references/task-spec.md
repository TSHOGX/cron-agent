# Task Spec

本文档说明 `.cron_agent_data/tasks/*.yaml` 的任务定义结构与注意事项。

## 最小示例

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
      sandboxMode: danger-full-access
      trace:
        enabled: true
        maxEventBytes: 262144
  output:
    sink: file
    pathTemplate: artifacts/{task_id}/{run_id}/result.txt
    format: text
```

## 字段说明

- `metadata.id`: 任务唯一标识。
- `metadata.enabled`: 是否启用调度。
- `spec.mode`: 执行模式（当前常用 `agent`）。
- `spec.runBackend`: 运行后端，当前仅支持 `cron`。
- `spec.schedule.cron`: cron 表达式。
- `spec.schedule.timezone`: 时区。
- `spec.execution.timeoutSeconds`: 超时秒数。
- `spec.execution.workingDirectory`: 执行工作目录。
- `spec.modeConfig.agent.*`: agent 运行参数。
- `spec.modeConfig.agent.sandboxMode`: provider CLI 的权限模式。默认值是 `danger-full-access`，表示沿用当前无交互的自动执行行为；旧任务里的 `workspace-write` 仍然兼容，会按受限写权限模式调用支持的 provider。
- `spec.output.*`: 输出位置和格式。

## 并发与运行约束

- 系统采用强单实例：同一 `task_id` 同时只允许一个运行实例。
- `spec.schedule.maxConcurrency` 在运行时会被固定为 `1`。
- 手动触发与调度触发都会经过同一套并发锁校验。

## 安全建议

- `.cron_agent_data/tasks/*.yaml` 已被 `.gitignore` 忽略。
- 不要将明文密钥提交到 Git。
- 建议使用本地环境变量或密钥管理方案注入凭证。
