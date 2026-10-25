# Frontend（M2 已实现）

Mac / Windows 桌面端：**Tauri 2**（Web 技术写界面，包体小，双端一致）。

## 组成

1. **聊天界面**（React + TS + Vite，`src/`）
   - 对话流：直答消息、任务状态、总结卡片
   - 方案卡片：goal/analysis/子任务列表（高危标记、依赖、workdir），批准 / 否决
   - 子任务实时进度日志、高危二次确认卡片
2. **内嵌本地执行器**（"不思考"，照指令执行）
   - 第二条 WS 连接注册为执行器（`executor_hello`，capabilities: zcode/shell）
   - 收到 `exec` 指令 → 调用 Rust 命令 `spawn_exec`（`src-tauri/src/main.rs`）起子进程
   - stdout/stderr 按行以 Tauri 事件回推，JS 转发为 `exec_output/exec_done` 给后端
   - zcode 运行时：优先 `ELECTRON_RUN_AS_NODE=1` + ZCode.app 内置 node（版本匹配关键，见 docs/ws-protocol.md 踩坑记录）

## 运行

```bash
npm install --cache /tmp/paa-npm-cache
npm run tauri dev    # 桌面端（首次需 Rust：rustup target stable）
npm run dev          # 纯浏览器开发模式（聊天可用，本机执行器不可用）
npm run build        # TS + vite 生产构建
```

后端默认连 `ws://127.0.0.1:8600/ws`（顶栏可改，localStorage 记忆）。

## 协议依据

- [../docs/ws-protocol.md](../docs/ws-protocol.md)（v2：含执行器指令族与时序图）
