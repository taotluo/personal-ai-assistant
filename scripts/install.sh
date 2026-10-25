#!/usr/bin/env bash
# 个人AI助理 · 一键安装（macOS / Linux）
#
# 用法：
#   ./scripts/install.sh             # 安装后端 + 前端（浏览器模式）依赖
#   ./scripts/install.sh --desktop   # 额外安装 Rust 工具链并校验 Tauri 桌面端可编译
#
# 网络不通时自动回退国内镜像（PyPI 清华源 / npmmirror / rsproxy）。
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

log()  { printf "\033[36m[安装]\033[0m %s\n" "$*"; }
ok()   { printf "\033[32m[完成]\033[0m %s\n" "$*"; }
warn() { printf "\033[33m[注意]\033[0m %s\n" "$*"; }

PY_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
NPM_MIRROR="https://registry.npmmirror.com"

# ---------- 1. 后端 ----------

if ! command -v python3 >/dev/null; then
  echo "缺少 python3（需要 3.10+）"; exit 1
fi
PYV="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
log "检测到 Python $PYV"

cd backend
if [ ! -d .venv ]; then
  if command -v uv >/dev/null; then
    log "使用 uv 创建虚拟环境…"
    uv venv .venv >/dev/null
  else
    log "使用 python3 -m venv 创建虚拟环境…"
    python3 -m venv .venv
  fi
fi

log "安装后端依赖（首次约 1-2 分钟）…"
if command -v uv >/dev/null; then
  uv pip install --python .venv/bin/python -q \
    -r requirements.txt -r requirements-dev.txt \
  || { warn "默认源失败，改用清华镜像重试"; \
       uv pip install --python .venv/bin/python -q \
         -r requirements.txt -r requirements-dev.txt -i "$PY_MIRROR"; }
else
  .venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt \
  || { warn "默认源失败，改用清华镜像重试"; \
       .venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt -i "$PY_MIRROR"; }
fi

if [ ! -f .env ]; then
  cp .env.example .env
  warn "已生成 backend/.env —— 请填入 PAA_ZHIPU_API_KEY（或改用 openai 兼容后端）"
fi

.venv/bin/python -c "import app.main" >/dev/null && ok "后端就绪（backend/.venv）"
cd "$ROOT"

# ---------- 2. 前端 ----------

if command -v npm >/dev/null; then
  cd frontend
  log "安装前端依赖…"
  # --cache 绕开 ~/.npm 属主异常；失败回退 npmmirror
  npm install --cache /tmp/paa-npm-cache --no-audit --no-fund \
  || npm install --cache /tmp/paa-npm-cache --no-audit --no-fund --registry "$NPM_MIRROR"
  ok "前端就绪（npm run dev / npm run tauri dev）"
  cd "$ROOT"
else
  warn "未检测到 npm，跳过前端（界面需要 Node 18+）"
fi

# ---------- 3. 桌面端 Rust（可选：--desktop） ----------

if [ "${1:-}" = "--desktop" ]; then
  if command -v cargo >/dev/null || [ -x "$HOME/.cargo/bin/cargo" ]; then
    ok "Rust 工具链已存在"
  else
    log "通过 rsproxy.cn 镜像安装 Rust 工具链（约几分钟）…"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y --default-toolchain stable --profile minimal
    mkdir -p "$HOME/.cargo"
    cat > "$HOME/.cargo/config.toml" <<'EOF'
[source.crates-io]
replace-with = 'rsproxy-sparse'

[source.rsproxy-sparse]
registry = "sparse+https://rsproxy.cn/index/"
EOF
    ok "Rust 安装完成（已配置 rsproxy 镜像）"
  fi
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env" 2>/dev/null || export PATH="$HOME/.cargo/bin:$PATH"
  log "校验 Tauri 桌面端可编译（首次拉取 crate 需几分钟）…"
  (cd frontend/src-tauri && cargo check -q)
  ok "Tauri 桌面端可编译"
fi

# ---------- 4. 环境提示 ----------

if [ "$(uname)" = "Darwin" ] && [ ! -x /Applications/ZCode.app/Contents/MacOS/ZCode ]; then
  warn "未检测到 ZCode.app：coder（zcode）任务不可用，其他功能不受影响"
fi

cat <<'EOF'

安装完成！接下来：
  PAA_LLM=mock ./scripts/start.sh all     # 无 key 冒烟（mock 后端 + 执行器 + 网页端）
  ./scripts/start.sh all                  # 正式启动（需先在 backend/.env 配好 key）
  浏览器打开 http://localhost:5173
  桌面端：cd frontend && npm run tauri dev
  停止：./scripts/stop.sh
EOF
