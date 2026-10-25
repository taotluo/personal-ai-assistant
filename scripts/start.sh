#!/usr/bin/env bash
# 个人AI助理 · 启动
#
# 用法：
#   ./scripts/start.sh              # 全部：后端 + 本地执行器 + 前端(浏览器模式)
#   ./scripts/start.sh backend      # 仅后端
#   ./scripts/start.sh executor     # 仅本地执行器（桌面端已内嵌，可省）
#   ./scripts/start.sh frontend     # 仅前端 dev server
#   PAA_LLM=mock ./scripts/start.sh # 环境变量透传（无 key 冒烟）
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/.run"
mkdir -p "$RUN"
COMP="${1:-all}"

start() {
  local name="$1"; shift
  if [ -f "$RUN/$name.pid" ] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then
    echo "· $name 已在运行 (pid $(cat "$RUN/$name.pid"))"
    return
  fi
  # 关键：重定向写在子 shell 命令上 + exec 替换进程 —— 不留任何持有
  # 父级 stdout 管道的中间 shell（否则 start.sh 被管道使用时会一直挂起），
  # stdin 指向 /dev/null 使服务彻底脱离终端。
  ( cd "$ROOT" && exec "$@" ) >> "$RUN/$name.log" 2>&1 < /dev/null &
  local pid=$!
  echo "$pid" > "$RUN/$name.pid"
  echo "▶ $name 已启动 (pid $pid, 日志 .run/$name.log)"
}

case "$COMP" in
  backend|all)
    [ -f backend/.env ] || cp backend/.env.example backend/.env
    start backend bash -c 'cd backend && exec .venv/bin/python -m app.main'
    ;;
esac
case "$COMP" in
  executor|all)
    start executor bash -c 'cd backend && exec .venv/bin/python scripts/executor.py'
    ;;
esac
case "$COMP" in
  frontend|all)
    start frontend bash -c 'cd frontend && exec npm run dev'
    ;;
esac

# 健康检查
sleep 2.5
if curl -sf http://127.0.0.1:8600/health >/dev/null 2>&1; then
  echo "✔ 后端健康检查通过 http://127.0.0.1:8600/health"
else
  echo "✘ 后端未就绪，查看日志：tail -30 .run/backend.log"
fi

cat <<'EOF'

使用入口：
  浏览器界面   http://localhost:5173
  CLI 客户端   backend/scripts/test_client.py --message "帮我调研X并写成笔记"
  桌面端       cd frontend && npm run tauri dev
  停止全部     ./scripts/stop.sh
EOF
