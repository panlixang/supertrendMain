#!/bin/bash
# 一键启动（macOS / Linux）
# 用法: bash start.sh [代理端口，默认 7890]

PROXY_PORT=${1:-7890}
ROOT=$(cd "$(dirname "$0")" && pwd)

echo "────────────────────────────────────────"
echo "  超级趋势监控台 · Signal Engine"
echo "────────────────────────────────────────"

# 加载 .env（OKX 密钥、Server酱 等）。没有就跳过，不影响纯盯盘功能。
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  if [ -n "$OKX_API_KEY" ]; then
    if [ "${OKX_SIMULATED:-1}" = "0" ]; then
      echo "⚠️  已加载 .env — OKX【实盘】模式，下单会用真实资金"
    else
      echo "✓ 已加载 .env — OKX 模拟盘模式"
    fi
  else
    echo "✓ 已加载 .env"
  fi
else
  echo "→ 未找到 .env（自动挂单不可用，盯盘/提醒正常）"
  echo "  需要挂单请执行: cp .env.example .env 并填入你的 OKX 密钥"
fi

# OKX 在部分地区需要代理，检测到本地代理就用
if curl -s --max-time 2 --proxy "http://127.0.0.1:$PROXY_PORT" https://www.okx.com > /dev/null 2>&1; then
  echo "✓ 检测到本地代理 :$PROXY_PORT，已启用"
  export https_proxy="http://127.0.0.1:$PROXY_PORT"
  export http_proxy="http://127.0.0.1:$PROXY_PORT"
else
  echo "→ 未检测到代理，直连 OKX（失败会自动切 aws.okx.com）"
fi

echo ""
cd "$ROOT/backend" || exit 1

# 后端代码需要 Python 3.10–3.13。系统自带的 python3.9 会启动即崩。
# 3.13 已支持（requirements 用带官方 wheel 的版本，不必本地编译 Rust）。
if [ ! -x .venv/bin/python ]; then
  PY=$(command -v python3.12 || command -v python3.11 || command -v python3.13 || command -v python3.10)
  if [ -z "$PY" ]; then
    echo "✗ 未找到 Python 3.10+，请先安装 Python 3.12（不要用 3.9）"
    exit 1
  fi
  echo "[后端] 创建虚拟环境（$("$PY" --version)）…"
  "$PY" -m venv .venv
fi
VPY=$(.venv/bin/python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[后端] 当前 venv Python $VPY"

echo "[后端] 安装依赖…"
.venv/bin/pip install -r requirements.txt -q

# 重复执行 start.sh 时旧 uvicorn 还占着 8000，会在 _serve 里直接崩
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8000/tcp >/dev/null 2>&1 || true
fi
pkill -f "uvicorn main:app --port 8000" >/dev/null 2>&1 || true
sleep 1

echo "[后端] 启动 FastAPI :8000"
LOG="$ROOT/backend/uvicorn.log"
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 >"$LOG" 2>&1 &
BACKEND_PID=$!
sleep 2
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  echo "✗ 后端启动失败。完整报错在 $LOG ，末尾如下："
  tail -n 40 "$LOG"
  exit 1
fi

echo ""
echo "[前端] 安装依赖…"
cd "$ROOT/frontend" || exit 1
[ -d node_modules ] || npm install --silent

echo "[前端] 启动 Vite :5174"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "────────────────────────────────────────"
echo "  启动完成 → http://localhost:5174"
echo "  Ctrl+C 停止全部服务"
echo "────────────────────────────────────────"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo '已停止'" SIGINT SIGTERM
wait
