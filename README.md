# Personal AI Assistant · 个人 AI 助理框架

> 后端是大脑，前端是聊天窗 + 本地执行器。助理分析需求、给出方案，**经你确认后**分发给执行 Agent 池，完成后自动审核、总结、复盘。支持直接调用 zcode 等编程 Agent 在你的仓库里干活，越用越懂你。

## 特性

- **强人控流**：方案先经你批准才执行；高危子任务（删文件 / git push / 对外发送）二次确认；定时任务无人在场时高危自动跳过
- **多 Agent 编排**：1 个助理（分流/规划/审核/总结/复盘）+ n 个可插拔执行者（research / writer / coder / shell，新增=一个类+注册）
- **zcode 集成**：coder Agent 经本地执行器把任务下发到 zcode headless，在你本机仓库干活，输出实时回流（已真机验证）
- **自我优化**：失败自动换策略重试 → 逐项审核验收 → 记忆复盘入库（lesson/preference/fact）→ 下次规划检索注入；技能沉淀（你批准的可复用流程模板）
- **定时任务**：cron 驱动（如每日 9 点学习简报），REST 管理，结果推聊天流
- **LLM 可插拔**：智谱原生 / 任何 OpenAI 兼容 API（DeepSeek、Kimi、Qwen、Ollama、one-api…）/ mock 冒烟
- **全链路透明**：每步 prompt/输出/决策落 SQLite，任务与产物可回溯；多端同时在线事件广播
- **自研薄编排**：不用 LangChain/LangGraph，裸 SDK + Pydantic，LLM 调用隔离在单模块（逃生门）

## 架构

```
┌────────────── Mac / Windows 前端（Tauri 2）──────────────┐
│  聊天界面 / 方案卡片 / 确认 / 进度 / 历史      本地执行器   │      ┌───────── 后端（云 / 本地，FastAPI）─────────┐
│                                              zcode/shell │  WS  │  助理 Agent（大脑）                        │
│  浏览器开发模式：npm run dev                  子进程桥     │◄────►│  执行 Agent 池 + 状态机 + 事件总线          │
└──────────────────────────────────────────────────────────┘      │  SQLite（任务/事件/记忆/技能）+ 定时调度    │
                                                                   └────────────────────────────────────────────┘
```

详细设计：[docs/architecture.md](docs/architecture.md) · 通信协议：[docs/ws-protocol.md](docs/ws-protocol.md)

## 快速开始

### 一键安装（macOS / Linux）

```bash
git clone git@github.com:taotluo/personal-ai-assistant.git
cd personal-ai-assistant
./scripts/install.sh            # 后端 + 前端依赖；网络差自动回退国内镜像
./scripts/install.sh --desktop  # 可选：连 Rust 工具链一起装（Tauri 桌面端）

# 配置 key（三选一）
vim backend/.env               # a) 智谱：PAA_ZHIPU_API_KEY=...
                               # b) OpenAI 兼容：PAA_LLM=openai + PAA_OPENAI_API_KEY/BASE_URL
                               # c) 先不配，直接 mock 冒烟 ↓

PAA_LLM=mock ./scripts/start.sh all    # 冒烟启动：后端+执行器+网页端
# 浏览器打开 http://localhost:5173 聊一句"帮我调研X并写成笔记"
./scripts/stop.sh                      # 停止
```

### 让 zcode 真干活

打开界面（或独立执行器 `./scripts/start.sh executor` 已在跑）后对助理说：

> 用 zcode 在 /Users/you/repos/my-project 里给项目补一个 README 并配上安装说明

助理会出方案 → 你批准 → 高危确认 → zcode 在该目录真实执行，输出流式回传，改动留在你的仓库里。

### 定时任务（如每日学习简报）

```bash
curl -X POST http://127.0.0.1:8600/api/schedules \
  -H "Content-Type: application/json" \
  -d '{"name":"学习简报","prompt":"总结我今天的学习内容并给出明日建议","cron":"0 21 * * *"}'
```

### 桌面端

```bash
cd frontend && npm run tauri dev      # 开发；打包：npm run tauri build
```

## 配置（backend/.env，前缀 PAA_）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PAA_LLM` | `zhipu` | `zhipu` / `openai`（任何 OpenAI 兼容）/ `mock` |
| `PAA_ZHIPU_API_KEY` | — | 智谱 key |
| `PAA_OPENAI_API_KEY` / `PAA_OPENAI_BASE_URL` | — / openai 官方 | OpenAI 兼容后端（DeepSeek `api.deepseek.com/v1`、Kimi `api.moonshot.cn/v1`、Ollama `localhost:11434/v1`…） |
| `PAA_MODEL_STRONG` / `PAA_MODEL_FAST` | glm-4.6 / glm-4.5-flash | 规划审核 / 简单子任务 分级模型 |
| `PAA_TOKEN` | dev-token | WS 认证令牌（公网部署必改） |
| `PAA_HOST` / `PAA_PORT` | 127.0.0.1 / 8600 | 监听地址；云端用 0.0.0.0 + TLS |
| `PAA_AGENT_TIMEOUT` | 600 | 子任务超时（秒） |
| `PAA_MAX_RETRIES` / `PAA_MAX_REVIEW_ROUNDS` | 2 / 2 | 每子任务重试预算 / 审核重跑轮数 |

## 项目结构

```
backend/
  app/
    llm/            LLM 调用层（zhipu / openai 兼容 / mock，唯一与模型 SDK 交互的模块）
    protocol/       结构化输出与 WS 协议对象
    orchestration/  编排核心：状态机 / 事件总线 / Agent 注册表 / 编排器
    agents/         助理 + research / writer / coder / shell
    server/         WS 路由 + 执行器管理
    storage/        SQLite（tasks/subtasks/messages/events/memories/schedules/skills）
    scheduler.py    定时任务
  scripts/          executor.py（独立执行器）/ test_client.py（CLI 客户端）
  tests/            24 个测试（含 mock 端到端）
frontend/           Tauri 2 + React：聊天 UI + 内嵌执行器（Rust 子进程桥）
scripts/            install.sh / start.sh / stop.sh 一键部署
docs/               architecture.md / ws-protocol.md
```

## 常见问题

- **zcode headless 启动报 `Migration transaction ownership mismatch`**：系统 node 版本与 ZCode.app 内置 node 不一致。执行器已内置解法（`ELECTRON_RUN_AS_NODE=1` + App 二进制），若自定义路径请保持该方式。
- **npm 安装报 `EACCES ~/.npm`**：`~/.npm` 属主异常，用 `npm install --cache /tmp/paa-npm-cache`（脚本已内置）。
- **Rust 下载超时**：走 rsproxy.cn 镜像（`install.sh --desktop` 已内置）。
- **直连 GitHub 超时**：HTTPS 443 不通时用 SSH（22 端口通常可达）。
- **非智谱后端的联网调研**：OpenAI 兼容协议无统一搜索工具，research 会降级为按模型已有知识回答。

## 状态

M1 后端闭环 ✅ → M2 桌面端 + 本机执行器（真实 zcode 验证）✅ → M3 审核/重试/记忆复盘 ✅ → M4 定时任务/技能沉淀/任务回溯 ✅

测试：`cd backend && .venv/bin/python -m pytest -q`（24 passed）

## License

个人项目，仅供学习与自用。
