# 架构文档

> 状态：M1–M4 全部已实现。核心循环、本机执行器（zcode）、审核重试与记忆复盘、定时任务与技能沉淀均已交付并验证。

## 1. 总体架构

前后端分离：**后端是大脑，前端是聊天窗 + 本地执行器**（前端"不思考"，只展示、确认、照指令执行）。

```
┌────────────── Mac / Windows 前端（Tauri，M2）──────────────┐
│  聊天界面                    本地执行器（内嵌进程）      │        ┌───────── 后端（云服务器 / 本地）─────────┐
│  ├ 对话流 / 方案卡片          ├ 接收后端下发的执行指令    │  WS    │  编排核心（Python / FastAPI）            │
│  ├ 计划批准 / 修改 / 否决     ├ 起子进程（zcode 等）     │◄──────►│  ├ 助理 Agent（唯一面向你的大脑）         │
│  ├ 子任务实时进度             ├ 流式回传输出/产物        │ 长连接 │  ├ 执行 Agent 池（调研/写作/… 可插拔）    │
│  ├ 高危操作二次确认弹窗       └ 报告完成/失败           │        │  ├ 任务状态机 + 事件总线 + Agent 注册表   │
│  └ 历史任务回溯                                        │        │  ├ SQLite（任务/消息/事件/记忆）         │
└────────────────────────────────────────────────────────┘        │  └ 定时调度器（M4）                      │
                                                                  └─────────────────────────────────────────┘
```

拓扑要点：**涉及本机资源的任务（编码/本地文件）通过前端执行器在用户机器上跑**，纯线上任务由后端执行。后端部署在云上也能指挥本机的 zcode（`zcode.cjs -p "<任务>" --cwd <仓库> --json`，已验证；无 `--model` 参数，模型跟账号走）。

## 2. 核心循环（已实现，含 M3）

```
用户消息 → 助理分流（LLM 结构化输出）
  ├ 闲聊/简单问题 → 直接回答，不进任务流
  └ 任务 → 助理产出方案（规划时检索注入历史记忆；goal + 子任务依赖图，每个子任务标注 agent/指令/高危/验收标准/workdir）
→ 编排器校验方案（执行者存在、id 唯一、无依赖环、本机类执行者带 workdir）
→ 推送方案卡片，等待用户 approve / reject / modify
→ 按依赖图分发：无依赖的并行跑；依赖失败的自动跳过
→ 高危子任务执行前推送二次确认，拒绝则跳过
→ 子任务执行失败 → 自动换策略重试（≤ PAA_MAX_RETRIES，默认 2；指令由助理改写，避免重复失败路径）
→ 全部结束 → 助理逐项审核（对照 expected_output 验收，发布 review_report）
     ├ 审核不合格且还有预算 → 换指令重跑（reviewing→running 回路，≤ PAA_MAX_REVIEW_ROUNDS 轮）
→ 生成总结 → 复盘提炼记忆（lesson/preference/fact）写入 memories 表并推送 retrospect
→ 任务终态 done（至少一个子任务成功）或 failed
→ 记忆闭环：下次任务规划时检索注入"相关记忆"块
```

状态机（`app/orchestration/state_machine.py`）：

```
任务:    planning → pending_confirm → running ⇄ reviewing → done | failed | cancelled
                                       （M3 审核重跑回路）
子任务:  queued → [awaiting_confirm] → running → [retrying…] → succeeded | failed | skipped
```

## 3. 模块地图（backend/app/）

| 模块 | 职责 | 对应设计原则 |
|---|---|---|
| `llm/` | 唯一与模型 SDK 对话的模块；`chat()`/`chat_json()`（Pydantic 结构化输出+失败重试）；zhipu/mock 双实现 | **逃生门**：将来换模型/框架只改这里 |
| `protocol/` | 助理结构化输出（TriageResult/Plan/Subtask 含 workdir）+ WS 信封 | 协议即文档，见 ws-protocol.md |
| `orchestration/` | **自研框架本体**：状态机、事件总线（多端广播+落库）、Agent 注册表、编排器 | 不依赖任何 Lang* 库 |
| `agents/` | 助理 Agent（分流/规划/总结提示词）；research / writer（后端执行）；coder / shell（经执行器在用户本机执行） | 新增执行者 = 一个类 + register() |
| `server/` | WS 认证、消息路由、事件广播；`executors.py` 执行器管理（注册/exec 下发/流式回传/超时/断线兜底） | 执行器指令直发不经广播 |
| `storage/` | SQLite：tasks/subtasks/messages/events/memories | **复盘透明度**：每步 prompt/输出/决策全量落库 |
| `scripts/test_client.py` | 最小前端模拟器（交互/一次性/auto-approve） | |
| `scripts/executor.py` | 独立本地执行器（zcode/shell 子进程、流式回传、断线重连）；桌面端内嵌同款逻辑 | |

## 4. 关键实现决策

- **future 先注册后发布**：方案卡片/高危确认的等待 future 必须在事件发布前注册，否则订阅者"先决议"会被静默丢弃（已踩过并修复的竞态，测试覆盖）。
- **用户消息独立协程**：WS 接收循环不阻塞，多任务可并发。
- **子任务调度**：循环扫描依赖图，`FIRST_COMPLETED` 驱动；子任务超时（默认 600s）、异常、未注册执行者都归约为 failed 结果，不炸主流程。
- **mock LLM**：与真实实现同接口，固定产出 research→writer+高危确认演示链路，无 key 全链路可验。

## 5. 路线图

- ~~**M2 前端 + 本地执行器**~~ ✅ 已完成：
  - 执行器协议 v2（`executor_hello`/`exec`/`exec_output`/`exec_done`）与 ExecutorManager（超时、断线兜底、流式回传）
  - coder Agent（zcode 桥，产出执行日志产物）+ shell Agent；方案校验要求本机类执行者必须带 workdir，且一律标记高危
  - 独立执行器 `scripts/executor.py` 与桌面端内嵌执行器（Tauri Rust 子进程桥）双实现
  - **已验证**：真实 zcode 全链路（助理→执行器→zcode headless 创建文件→输出回流）；桌面 UI 全交互流（浏览器联调）
  - 关键踩坑：zcode CLI 需与 App 内置 node 同版本运行（`ELECTRON_RUN_AS_NODE=1` + App 二进制），否则 SQLite 迁移失败
- ~~**M3 审核与自我优化 v1**~~ ✅ 已完成：
  - 审核环：reviewing 阶段助理对照 expected_output 逐项验收，发布 `review_report`
  - 失败/审核不合格自动换策略重试：指令由助理改写（含失败原因反馈），执行失败与审核不通过共用每子任务重试预算（默认 2），审核-重跑循环上限 2 轮
  - 记忆复盘：任务终态前助理提炼 lesson/preference/fact 入 memories 表（`retrospect` 事件），下次规划时按关键词相关性检索注入（embedding 检索留待后续）
  - 已验证：16 个测试全绿 + mock 全链路冒烟（审核/记忆事件可见、记忆二次注入生效）
- ~~**M4 打磨**~~ ✅ 已完成：
  - 定时任务：APScheduler（本地时区），REST 管理（`/api/schedules`，cron 5 段表达式），启动时从 schedules 表重载；触发时以**无人在场自动模式**跑完整任务流（方案自动批准、高危子任务自动跳过、规划提示词要求避开高危与本机执行任务），结果照常推聊天流可回看。已用 `*/1 * * * *` 真实触发验证。
  - 技能沉淀（自我优化 v2）：复盘时可提出 ≤1 个技能候选 → `skill_suggestion` 卡片 → 用户批准后入 skills 表 → 后续规划提示词注入"已入库技能"清单（闭环已测）。
  - 任务回溯：`GET /api/tasks/{id}` 详情（方案/子任务结果/消息/产物路径）+ 前端"历史"抽屉；REST 开 CORS。
  - 多端同步：事件总线广播天然多端；断线不补发（历史走 REST 回溯）。

## 后续候选（未排期）

- research Agent 通用搜索（非智谱后端的联网检索）
- 记忆 embedding 检索；跨重启任务恢复（running 任务断点续跑）
- 桌面端打包分发（`npm run tauri build`）、Windows 执行器路径适配

## 6. 已知限制（M1）

- 单用户单 token；无跨重启任务恢复（进程重启后 running 任务丢失，SQLite 有历史记录但不续跑）。
- 前端断线期间事件不补发（重连后仅收到新事件；历史可查 `/api/tasks` + events 表）。
- `modify` 决议视为"按修改稿直接执行"，不再二次确认（协议预留了重新确认的扩展位）。
- 高危确认等待无超时：用户不响应则任务一直挂起（by design：宁可挂着不可误跑）。

## 7. 部署与安全

- 默认监听 `127.0.0.1:8601`，token 认证（`PAA_TOKEN`，默认 dev-token 有启动告警）。
- 云部署：`PAA_HOST=0.0.0.0` + 反向代理 TLS（或 Tailscale 私网），务必改 token。
- 数据目录 `backend/data/`（SQLite + artifacts 产物文件）。
