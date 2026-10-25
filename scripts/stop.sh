#!/usr/bin/env bash
# 个人AI助理 · 停止（清掉 .run/ 里的 PID；兜底按命令行特征匹配；顽固进程 SIGKILL）
# 尽力而为，不因单项失败中断。

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/.run"

pids=()
if [ -d "$RUN" ]; then
  for pidfile in "$RUN"/*.pid; do
    [ -f "$pidfile" ] || continue
    name="$(basename "$pidfile" .pid)"
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      pids+=("$pid")
      echo "■ 已停止 $name (pid $pid)"
    fi
    rm -f "$pidfile"
  done
fi

# 兜底：按命令行特征清理残留（-f 匹配可能命中无关进程，模式尽量收窄）
pkill -f "\.venv/bin/python -m app\.main" 2>/dev/null
pkill -f "python.*scripts/executor\.py" 2>/dev/null
lsof -ti :8600 2>/dev/null | xargs kill 2>/dev/null
lsof -ti :5173 2>/dev/null | xargs kill 2>/dev/null

# 顽固进程强杀
sleep 0.5
for pid in "${pids[@]}"; do
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null && echo "■ 强杀 $pid"
done

if [ "${#pids[@]}" -eq 0 ]; then
  echo "没有在运行的组件"
fi
exit 0
