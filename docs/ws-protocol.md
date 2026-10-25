# WebSocket 协议（v4）

前端（及本地执行器）与后端的唯一通道。v2 执行器指令族；v3 审核/重试/复盘事件；v4 技能沉淀交互（M4 已实现）。

REST 辅助（已开 CORS）：
- `GET /health`、`GET /api/tasks`（最近任务）
- `GET /api/tasks/{id}`（任务详情：方案/子任务结果/消息，用于回溯）
- `GET|POST|DELETE /api/schedules`（定时任务；POST body `{name, prompt, cron}`，cron 为 5 段表达式；触发时以无人在场自动模式跑完整任务流——方案自动批准、高危子任务自动跳过，结果照常推聊天流）

## 连接与认证

```
ws://<host>:<port>/ws?token=<PAA_TOKEN>
```

- token 错误：握手被拒（close code 4401）。
- 同一 token 可多连接（多设备）同时在线，所有事件广播到每个连接。
- REST 辅助端点：`GET /health`、`GET /api/tasks`（最近任务列表）。

## 信封（服务端 → 客户端）

```json
{"type": "<事件类型>", "payload": { }, "task_id": "t1-xxxxxx | null", "ts": 1761234567.89}
```

## 客户端 → 服务端（6 种）

### user_message —— 发消息（聊天或提需求）

```json
{"type": "user_message", "text": "帮我调研 X 并写成笔记"}
```

### plan_decision —— 对方案卡片的裁决

```json
{"type": "plan_decision", "task_id": "t1-xxxxxx", "decision": "approve"}
{"type": "plan_decision", "task_id": "t1-xxxxxx", "decision": "reject"}
{"type": "plan_decision", "task_id": "t1-xxxxxx", "decision": "modify",
 "plan": {"goal": "...", "analysis": "...", "subtasks": [ ...同 plan_card 结构... ]}}
```

> v1 语义：modify 视为"按修改稿执行"，不重新确认。

### confirm_response —— 高危二次确认

```json
{"type": "confirm_response", "confirm_id": "c1-xxxxxx", "approved": true, "note": ""}
```

### executor_hello —— 执行器注册（第二条 WS 连接，桌面端内嵌或 scripts/executor.py）

```json
{"type": "executor_hello", "capabilities": ["zcode", "shell"]}
```

注册后该连接不再接收聊天广播，只收直发的 `exec` 指令；服务端回 `executor_ack`。

### skill_decision —— 对技能建议的裁决（M4）

```json
{"type": "skill_decision", "skill_id": "sk-xxxxxxxx", "approved": true}
```

批准后写入技能库，之后规划提示词会注入"已入库技能"清单。

### exec_output / exec_done —— 执行器流式上报

```json
{"type": "exec_output", "exec_id": "e-xxxxxxxx", "chunk": "一行输出\n"}
{"type": "exec_done", "exec_id": "e-xxxxxxxx", "ok": true, "exit_code": 0, "error": ""}
```

## 服务端 → 客户端（11 种）

### message —— 聊天消息（无 task_id = 闲聊直答，收到即本轮结束）

```json
{"type": "message", "task_id": "t1-xxxxxx", "payload": {"role": "assistant", "text": "我把需求拆成了 2 个子任务…"}}
```

### plan_card —— 方案卡片（渲染成卡片，等用户裁决）

```json
{"type": "plan_card", "task_id": "t1-xxxxxx", "payload": {
  "goal": "调研多智能体框架并产出笔记",
  "analysis": "先调研再成文，两步串行",
  "subtasks": [
    {"id": "s1", "agent": "research", "title": "联网调研",
     "instruction": "…自包含指令…", "depends_on": [], "risky": false,
     "expected_output": "带来源的 markdown 报告"},
    {"id": "s2", "agent": "writer", "title": "整理成笔记",
     "instruction": "基于 s1 的输出…", "depends_on": ["s1"], "risky": true,
     "expected_output": "一篇 markdown 笔记"}
  ]
}}
```

### task_status —— 任务状态变化

```json
{"type": "task_status", "task_id": "t1-xxxxxx",
 "payload": {"status": "planning|pending_confirm|running|reviewing|done|failed|cancelled",
             "title": "…", "reason": "仅 failed 时有"}}
```

### subtask_status —— 子任务状态/进度（最高频事件）

```json
{"type": "subtask_status", "task_id": "t1-xxxxxx", "payload": {
  "subtask_id": "s1", "title": "联网调研", "agent": "research",
  "status": "running | progress | retrying | succeeded | failed | skipped",
  "note": "progress 的进度说明 / retrying 的失败原因",
  "attempt": 1,
  "output": "succeeded 时的输出节选",
  "artifacts": ["/path/to/artifact.md"],
  "error": "failed 时的原因"}}
```

> M3：`retrying` 表示该子任务失败（或审核未通过）后将换策略自动重跑，`attempt` 为已消耗的重试次数（上限 `PAA_MAX_RETRIES`，默认 2）。

### confirm_request —— 高危确认请求（阻塞等待 confirm_response）

```json
{"type": "confirm_request", "task_id": "t1-xxxxxx",
 "payload": {"confirm_id": "c1-xxxxxx", "subtask_id": "s2", "title": "整理成笔记",
             "reason": "子任务「整理成笔记」被标记为高危操作…"}}
```

### summary —— 任务总结（终态前最后一条实质内容）

```json
{"type": "summary", "task_id": "t1-xxxxxx", "payload": {
  "text": "## 结果总览\n…",
  "subtasks": [{"id": "s1", "title": "联网调研", "ok": true, "artifacts": ["/path/…"]}]}}
```

### error —— 错误（不断连接）

```json
{"type": "error", "payload": {"message": "…", "task_id": "t1-xxxxxx"}}
```

### review_report —— 审核报告（M3，reviewing 阶段逐项验收结果）

```json
{"type": "review_report", "task_id": "t1-xxxxxx", "payload": {
  "verdicts": [
    {"subtask_id": "s1", "passed": true, "reason": "达标"},
    {"subtask_id": "s2", "passed": false, "reason": "未引用来源"}
  ],
  "overall": "整体一句话评价"}}
```

不合格且还有重试预算的子任务会换策略重跑（任务状态回到 running，相关子任务发出 `retrying`）；预算耗尽则如实进入总结。审核-重跑循环上限 `PAA_MAX_REVIEW_ROUNDS`（默认 2）。

### retrospect —— 记忆沉淀（M3，总结之后、终态之前发布）

```json
{"type": "retrospect", "task_id": "t1-xxxxxx", "payload": {
  "memories": [{"kind": "lesson", "content": "zcode 任务必须给绝对路径"}]}}
```

这些记忆会在后续任务的规划提示词中检索注入（"相关记忆"块）——自我优化的数据闭环。

### skill_suggestion —— 技能建议（M4，任务终态后发布，等用户裁决）

```json
{"type": "skill_suggestion", "task_id": "t1-xxxxxx", "payload": {
  "skill_id": "sk-xxxxxxxx", "name": "每日学习简报",
  "description": "当用户要每日学习简报时使用",
  "plan_hint": "research 调研 → writer 成文"}}
```

### executor_ack —— 执行器注册成功（直发给执行器连接）

```json
{"type": "executor_ack", "payload": {"executor_id": "ex-xxxxxx"}}
```

### exec —— 执行指令（直发给执行器连接，不经广播）

```json
{"type": "exec", "payload": {
  "exec_id": "e-xxxxxxxx", "kind": "zcode | shell",
  "prompt": "给 zcode 的任务描述（kind=zcode）",
  "command": "shell 命令（kind=shell）",
  "cwd": "/Users/you/repos/project", "timeout": 595}}
```

**zcode 运行时要点（踩坑记录）**：执行器启动 zcode CLI 时，若系统 `node` 版本与 ZCode.app 内置 node 不一致，CLI 的 SQLite 迁转会报 `Migration transaction ownership mismatch` 拒绝启动。正确做法：用 `ELECTRON_RUN_AS_NODE=1 /Applications/ZCode.app/Contents/MacOS/ZCode <zcode.cjs> -p …` 以 App 内置 node 运行。`scripts/executor.py` 与桌面端 Rust 桥均已内置此逻辑。

## 典型时序

```
前端                          后端
 │ user_message ─────────────►│ 分流（LLM）
 │◄───────────── message      │ 闲聊 → 直答，结束
 │◄───────────── task_status(planning)
 │◄───────────── plan_card    │ 任务 → 规划+校验
 │ plan_decision(approve) ───►│
 │◄───────────── task_status(running)
 │◄───────────── subtask_status(s1 running)
 │◄───────────── subtask_status(s1 progress) ×N
 │◄───────────── subtask_status(s1 succeeded)
 │◄───────────── confirm_request(s2 高危)     │ 阻塞等确认
 │ confirm_response(approved) ───────────────►│
 │◄───────────── subtask_status(s2 succeeded)
 │◄───────────── task_status(reviewing)
 │◄───────────── summary
 │◄───────────── task_status(done)
```

## 断线语义（v1）

- 重连后不补发历史事件；用 `GET /api/tasks` + 后续 events 查询接口（M2）做回溯。
- 任务在后端继续执行，不因前端断线而取消；确认类请求会一直等待重连后的响应。

## 执行器时序（M2 已实现）

```
执行器                         后端                          聊天前端
 │ executor_hello ─────────────►│                              │
 │◄──────────── executor_ack    │                              │
 │                              │◄────────── user_message ─────│
 │                              │ … 规划 → plan_card ─────────►│
 │                              │◄────────── plan_decision ────│
 │◄──────────── exec(zcode)     │ (子任务高危时先确认)          │
 │ exec_output ×N ─────────────►│──── subtask_status(progress)►│
 │ exec_done ──────────────────►│──── subtask_status(succeeded)│
 │                              │ … 审核 → summary ───────────►│
```
