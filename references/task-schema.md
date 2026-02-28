# Task Schema (cron-agent)

## Required high-level fields

- `apiVersion`: `cron-agent`
- `kind`: `CronTask`
- `metadata.id`: slug-like id
- `spec.mode`: `agent` or `llm`
- `spec.runBackend`: `cron`
- `spec.schedule.cron`: 5-field cron expression

## Core defaults

- `spec.schedule.maxConcurrency = 1`
- `spec.execution.timeoutSeconds = 600`
- `spec.execution.workingDirectory = "."`

## Agent mode

- `spec.modeConfig.agent.provider` in `claude|codex|gemini|opencode|pi`
- `spec.modeConfig.agent.commandTemplate` is not supported

## LLM mode

- Default provider: `openai_compatible`
- Default model: `gpt-4o-mini`
- Default auth ref: `env:OPENAI_API_KEY`

## Minimal example

```yaml
apiVersion: cron-agent
kind: CronTask
metadata:
  id: demo
  name: Demo
  enabled: true
spec:
  mode: agent
  runBackend: cron
  schedule:
    cron: "*/10 * * * *"
    timezone: Asia/Shanghai
  input:
    prompt: "Do something useful"
```
