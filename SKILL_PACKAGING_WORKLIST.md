# Cron Agent 改造与 Skills 打包工作清单

本文档用于指导后续把当前项目打造成可复用的 Skills。  
目标是让新接手同学在缺少上下文时，也能按清单逐项推进，不遗漏关键改动。

---

## 0. 目标定义（先统一口径）

最终目标不是“再做一个 demo”，而是交付一套可复用能力：

1. 任意任务可选择调度后端（`tmux` 或 `cron`）。
2. 任意任务可选择执行模式（`agent` 或 `llm`）。
3. 任务配置、状态、日志、产物都可管理、可追踪、可迁移。
4. 项目可打包为 Skills，被 coding agent 加载后可直接创建/管理任务。

---

## 1. 代码清理与架构收敛（必须先做）

### 1.1 清理旧 demo 逻辑（截图+日记耦合实现）
- 问题：当前仍存在“旧架构 + 新架构”并存，职责重复。
- 目标：保留一套主路径，避免双入口导致维护复杂度上升。
- 操作：
1. 梳理并标记 legacy 入口：`scheduler.py` 中旧 `tmux/cron` 相关分支。
2. 将“通用能力”与“示例任务”彻底分离：
   - 通用能力保留在 `cron_manager.py` 和统一 API。
   - 截图写日记逻辑改为任务模板，不再作为核心控制面默认逻辑。
3. 旧 API 标记为 deprecated（保留兼容期），并在文档写明替代接口。
- 涉及文件：
  - `/Users/hsw/Workspace/vibe/cron_agent/scheduler.py`
  - `/Users/hsw/Workspace/vibe/cron_agent/api.py`
  - `/Users/hsw/Workspace/vibe/cron_agent/README.md`
- 验收：
  - 新增任务不依赖 `scheduler.py` 旧命令即可完整运行。
  - 文档只推荐新接口，旧接口仅列兼容说明。

### 1.2 明确“控制平面 vs 运行后端”
- 问题：历史上把管理器和 cron 实现揉在一起，概念容易混淆。
- 目标：对外只讲三层：
1. `CronManager`（控制平面）
2. `TmuxBackend`
3. `CronBackend`
- 操作：
1. 统一术语（代码注释、README、API 返回字段）。
2. API/UI 明确展示：任务属于哪个后端。
- 验收：
  - 新人阅读文档可明确“管理器不是第三个调度器”。

---

## 2. 旧任务迁移到新系统（核心业务改造）

### 2.1 建立迁移清单（先盘点，后迁移）
- 需要迁移的典型任务：
1. 定时截图分析（原 tmux loop）
2. 日报生成
3. 周报生成
4. 月报生成
5. 上午/下午/晚上分时总结

### 2.2 迁移策略
- 原则：先“同逻辑迁移”，再“能力增强”。
- 操作：
1. 为每个旧任务创建独立 YAML（`tasks/*.yaml`）。
2. 显式指定：
   - `spec.runBackend`（截图建议 `tmux`；汇总建议 `cron`）
   - `spec.mode`（`agent` 或 `llm`）
3. 保持原时间边界规则一致（尤其跨天统计窗口）。
4. 对每个迁移任务做一次手动触发与结果比对。
- 涉及文件：
  - `/Users/hsw/Workspace/vibe/cron_agent/tasks/*.yaml`
  - `/Users/hsw/Workspace/vibe/cron_agent/cron_manager.py`
  - `/Users/hsw/Workspace/vibe/cron_agent/summarizer.py`
  - `/Users/hsw/Workspace/vibe/cron_agent/recorder.py`
- 验收：
  - 旧任务在新系统都能跑通，且输出格式与质量不回退。

### 2.3 冲突保护（迁移必须配套）
- 操作：
1. 防止同一任务被两个后端重复调度。
2. 防止同一任务并发重入（锁 + 幂等）。
3. 迁移期间先暂停旧调度，再启新调度，最后做一次 `sync` 校验。
- 验收：
  - 不出现重复写记录、重复生成日报等问题。

---

## 3. 输出路径与仓库污染治理（必须尽快做）

### 3.1 现状问题
- 产物直接落在仓库工作目录，导致：
1. `git status` 持续脏
2. 提交容易混入运行产物
3. 迁移与备份边界不清晰

### 3.2 推荐方案（默认 `output_root`）
- 结论：采用“统一数据根目录”方案，而不是只做简单 prefix。
- 设计：
1. 新增全局配置：`output_root`
2. 所有运行时数据迁移到该目录：
   - logs
   - runtime state
   - artifacts
   - records/journal/messages（视业务决定是否全部外置）
3. 代码目录仅保留：
   - 源码
   - 任务模板
   - 文档
- 建议默认值：
  - 本地开发：`./.cron_agent_data/`
  - 或用户目录：`~/Library/Application Support/cron_agent/`（macOS）
- 涉及文件：
  - `/Users/hsw/Workspace/vibe/cron_agent/cron_manager.py`
  - `/Users/hsw/Workspace/vibe/cron_agent/recorder.py`
  - `/Users/hsw/Workspace/vibe/cron_agent/config.json`
  - `/Users/hsw/Workspace/vibe/cron_agent/.gitignore`
- 验收：
  - 跑任务后 `git status` 不新增运行产物。

---

## 4. 前后端改版（从 demo 控制台升级为任务控制台）

### 4.1 后端接口改造
- 必要能力：
1. 任务 CRUD（已具备，需稳定化）
2. 后端状态（tmux/cron）统一查询
3. 运行历史与事件日志分页
4. 下一次运行时间计算接口（便于前端显示）
5. 任务 schema 校验接口（前端保存前预校验）
- 涉及文件：
  - `/Users/hsw/Workspace/vibe/cron_agent/api.py`
  - `/Users/hsw/Workspace/vibe/cron_agent/cron_manager.py`
- 验收：
  - 前端无需拼接内部逻辑即可展示“任务-后端-状态-日志”。

### 4.2 前端 UI 改造
- 目标：从“截图 demo 面板”改为“任务运维面板”。
- 页面建议：
1. 任务列表页：
   - 任务名、后端、模式、启用状态、下次运行时间、最近结果
2. 任务详情页：
   - YAML 视图/表单视图切换
   - 最近 N 次运行摘要
   - 单次运行事件流
3. 后端状态页：
   - tmux session 状态
   - cron 托管条目状态
4. 操作入口：
   - 立即运行、暂停、恢复、删除、同步
- 涉及文件：
  - `/Users/hsw/Workspace/vibe/cron_agent/web/templates/index.html`
  - `/Users/hsw/Workspace/vibe/cron_agent/web/static/app.js`
  - `/Users/hsw/Workspace/vibe/cron_agent/web/static/style.css`
- 验收：
  - 不用手写命令，用户可在 UI 完成核心管理动作。

### 4.3 动态发现与动态更新
- 要求：
1. 后端新增任务后，前端可刷新发现
2. 状态变化可轮询更新（先轮询，后续再考虑 SSE/WebSocket）
- 验收：
  - 新任务创建后在 UI 中可见，状态变化可实时或准实时反映。

---

## 5. 配置、Schema、迁移工具（减少未来返工）

### 5.1 固化 Task Schema
- 操作：
1. 输出正式 schema 文档（字段、默认值、后端约束）。
2. 提供 schema 版本号（如 `cron-agent/v1`）。
3. 禁止隐式兼容过多历史字段，避免长期技术债。

### 5.2 旧配置迁移工具
- 操作：
1. 提供脚本将旧 `config.json` + 旧定时逻辑转换为新 YAML 任务。
2. 支持 dry-run（只预览，不写入）。
3. 生成迁移报告（成功/失败/待人工处理）。

### 5.3 变更防护
- 操作：
1. 保存任务前先校验。
2. 提供“应用前 diff”能力（UI 或 CLI）。

---

## 6. 可观测性与稳定性（上线前必须补）

### 6.1 运行可靠性
- 操作：
1. 连续失败计数 + 自动告警阈值
2. timeout、retry、并发策略可配置
3. 运行锁异常恢复（避免卡死为 running）

### 6.2 日志治理
- 操作：
1. JSONL 字段规范固定
2. 敏感字段脱敏（token、Authorization）
3. 日志轮转与保留策略

### 6.3 诊断工具
- 提供 `doctor` 命令检查：
1. tmux 可用性
2. crontab 可用性
3. 必要权限（截图权限等）
4. API key 与模型连通性（可选）

---

## 7. 安全治理（必须单列）

### 7.1 Secrets 管理
- 要求：
1. 不允许真实 token 写入仓库文件
2. 统一通过 env 或 secret 文件引用
3. 示例配置必须使用占位符

### 7.2 Agent 执行风险控制
- 要求：
1. `commandTemplate` 使用白名单或安全策略（至少文档明确风险）
2. 明确哪些任务允许文件写入、哪些只读

---

## 8. 测试体系（交付质量底线）

### 8.1 单元测试
- 覆盖：
1. schema 校验
2. backend 路由
3. run 生命周期（start/success/fail/retry）
4. 状态锁释放

### 8.2 集成测试
- 覆盖：
1. tmux 任务创建/同步/运行/停止
2. cron 任务创建/同步/运行/停止
3. 混合任务同时存在
4. 迁移脚本正确性

### 8.3 E2E 回归脚本
- 至少保留两条：
1. `agent` 任务生成代码并验证产物
2. `llm` 任务生成文本并验证日志

---

## 9. Skills 打包前置工作（直接面向最终交付）

### 9.1 技能边界定义
- 明确 skill 负责：
1. 创建/修改任务 YAML
2. 调用 `apply/sync/run/status/logs` 命令
3. 根据失败信息给出修复建议

### 9.2 Skills 资产准备
- 需要输出：
1. `SKILL.md`（标准流程、命令、注意事项）
2. 任务模板库（截图、日报、周报、月报、巡检）
3. FAQ/排障文档（权限、超时、重复执行）

### 9.3 验收标准
- 新人只看 skill 文档即可：
1. 新建任务
2. 运行并看日志
3. 修改并重载
4. 排查常见故障

---

## 10. 推荐执行顺序（按依赖，不按时间）

1. 先完成“代码清理与架构收敛”（第 1 节）
2. 再做“旧任务迁移”（第 2 节）
3. 立即做“输出路径治理”（第 3 节）
4. 同步推进“前后端改版”（第 4 节）
5. 然后补“schema+迁移工具+稳定性+安全+测试”（第 5~8 节）
6. 最后打包 Skills（第 9 节）

---

## 11. 当前已知风险清单（接手者先看）

1. 旧 `scheduler.py` 与新 `cron_manager.py` 仍有职责重叠。
2. 环境权限可能导致 `crontab` 操作失败（需在真实环境验证）。
3. `tmux` / `cron` 并存阶段若配置不当，存在重复执行风险。
4. 运行产物目录策略未完全统一时，仍可能污染仓库提交。
5. 历史配置中可能残留敏感信息，需要专门清理与轮换。

