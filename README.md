# Personal AI Assistant

个人 AI 助理框架：**后端是大脑，前端是聊天窗 + 本地执行器**。助理 Agent 分析需求、给出方案，经你确认后分发给执行 Agent 池，完成后审核总结。用于日常学习与工作。

```
┌────────────── Mac / Windows 前端（Tauri，M2）──────────────┐
│  聊天界面 / 方案卡片 / 进度 / 确认      本地执行器（zcode 桥）│
└────────────────────────┬───────────────────────────────────┘
                         │ WebSocket + token
┌────────────────────────┴───────────────────────────────────┐
│ 后端（云 / 本地，Python + FastAPI）                          │
│  助理 Agent（规划→确认→分发→审核→总结）                     │
│  执行 Agent 池（调研 / 写作 / … 可插拔注册）                 │
│  任务状态机 · 事件总线 · SQLite 记忆/产物 · 定时调度（M4）   │
└─────────────────────────────────────────────────────────────┘
```

## 当前状态：M1–M4 全部完成 🎉

- ✅ M1 后端闭环：分流 → 方案卡片 → 确认 → 依赖图执行 → 高危二次确认 → 总结，全程事件流 + SQLite 落库
- ✅ M2 本地执行器：coder（zcode 桥）+ shell；独立执行器 `scripts/executor.py` + Tauri 桌面端内嵌；**真实 zcode 集成已验证**
- ✅ M3 审核与自我优化 v1：失败自动换策略重试（≤2 次/子任务）、逐项审核验收（不合格换指令重跑 ≤2 轮）、记忆复盘入库 + 规划时检索注入
- ✅ M4 定时任务与技能沉淀：
  - **定时任务**（cron，REST 管理）：无人在场自动模式（方案自动批、高危自动跳），如"每天 9 点学习简报"——`curl -X POST :8600/api/schedules -d '{"name":"日报","prompt":"...","cron":"0 9 * * *"}'`
  - **技能沉淀**（自我优化 v2）：复盘提出可复用流程 → 你批准入库 → 之后规划自动参考
  - 任务回溯 API + 前端"历史"抽屉
- LLM 后端可插拔：zhipu / **任何 OpenAI 兼容 API**（DeepSeek/Kimi/Qwen/Ollama/one-api，见 .env.example）/ mock

## 快速开始

```bash
# 后端（终端 1）
cd backend && source .venv/bin/activate
PAA_LLM=mock python -m app.main &                 # 无 key 冒烟；真实使用：.env 填 key
# LLM 可换任何 OpenAI 兼容厂商（DeepSeek/Kimi/Qwen/Ollama/one-api…）：
#   PAA_LLM=openai  PAA_OPENAI_API_KEY=sk-..  PAA_OPENAI_BASE_URL=https://api.deepseek.com/v1
#   PAA_MODEL_STRONG=deepseek-chat  PAA_MODEL_FAST=deepseek-chat   （详见 .env.example）
python scripts/executor.py &                      # 独立本地执行器（桌面端内置，可省）
python scripts/test_client.py --message "帮我调研X并写成笔记" --auto-approve   # CLI 客户端

# 桌面端（终端 2）
cd frontend && npm install --cache /tmp/paa-npm-cache
npm run tauri dev                                 # 首次需 Rust 工具链；浏览器开发模式：npm run dev

# 让 zcode 真干活（经执行器下发到本机）
# 对助理说："用 zcode 在 /path/to/repo 给项目加上 README"
```

## 目录

```
backend/   Python 后端（FastAPI + 自研编排：状态机/事件总线/Agent注册表）
frontend/  M2：Tauri 桌面端（聊天 + 本地执行器）
docs/      architecture.md · ws-protocol.md
```

核心原则：**不用 LangChain/LangGraph，裸 SDK + Pydantic 薄封装**，LLM 调用隔离在 `backend/app/llm/` 单模块后（逃生门），每一步的 prompt/输出/决策全量落库（自我优化的数据地基）。
