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

# 重复执行 start.sh 时旧 uvicorn 还占着 8000，会导致新后端 bind 失败。
# 注意两点：
# 1) 占位的可能是别的项目 / 旧版入口（如 api.main:app），按命令行 pkill 匹配不到，
#    必须按端口清理；
# 2) lsof 必须加 -sTCP:LISTEN —— 不加的话"连到 8000 的客户端连接"也会被算进来，
#    而 vite 的代理进程常年挂着到 127.0.0.1:8000 的出站连接，
#    曾导致每次重启都把 vite 一并误杀（前端整段不可用，浏览器报 ECONNREFUSED）。
PIDS_8000=$(lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null)
if [ -n "$PIDS_8000" ]; then
  echo "→ 端口 8000 已被占用（PID: $(echo $PIDS_8000 | tr '\n' ' ')），先停掉再启动本项目后端"
  kill $PIDS_8000 2>/dev/null || true
  # uvicorn 收到 SIGTERM 后会等存量连接（浏览器 WebSocket）断开才释放端口，
  # 最多等 3 秒；还占着就 SIGKILL，避免新后端 bind 失败。
  for _ in 1 2 3; do
    lsof -ti tcp:8000 -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 1
  done
  lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null || true
fi
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8000/tcp >/dev/null 2>&1 || true
fi
pkill -f "uvicorn main:app --port 8000" >/dev/null 2>&1 || true
sleep 1

# 让 Python 信任系统根证书 + 本地代理(Quantumult X 等)的 MITM CA。
# 否则经这类代理的 HTTPS 解密时，urllib 会报 SSL: UNEXPECTED_EOF_WHILE_READING，
# 导致拉不到 OKX 行情（页面一直空白 / no_base_candles）。
# macOS 上 curl 默认读系统钥匙串所以能通，但 Homebrew Python 用自带 OpenSSL、信任库为空，必须显式指定。
CA_BUNDLE="$ROOT/backend/.ca-bundle.pem"
if command -v security >/dev/null 2>&1; then
  : >"$CA_BUNDLE"
  security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >>"$CA_BUNDLE" 2>/dev/null
  security find-certificate -a -p "$HOME/Library/Keychains/login.keychain-db" >>"$CA_BUNDLE" 2>/dev/null
  security find-certificate -c "Quantumult X" -a -p "$HOME/Library/Keychains/login.keychain-db" >>"$CA_BUNDLE" 2>/dev/null
  N=$(grep -c 'BEGIN CERTIFICATE' "$CA_BUNDLE" 2>/dev/null || echo 0)
  # 关键：只有真的导出到证书才设 SSL_CERT_FILE。
  # 空文件会让 OpenSSL 信任库为空 —— 反而使所有 HTTPS 都报 CERTIFICATE_VERIFY_FAILED，
  # 比不设置还要糟（页面会彻底拉不到行情）。拿不到就删掉、回退到 Python 默认信任库。
  if [ "${N:-0}" -gt 0 ]; then
    export SSL_CERT_FILE="$CA_BUNDLE"
    export REQUESTS_CA_BUNDLE="$CA_BUNDLE"
    echo "[后端] 已注入 CA 信任包（$N 张）"
  else
    rm -f "$CA_BUNDLE"
    echo "[后端] CA 信任包生成失败（0 张），回退 Python 默认信任库"
  fi
fi

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
# 注意：uvicorn 的 socket 要等应用 startup 事件跑完才开始 LISTEN，而本项目的 startup
# 会预取全部交易对×全部周期的历史K线 —— 服务器上首次启动（本地缓存为空）可能要几分钟。
# 所以这里只确认"进程活着"即可，端口未就绪属正常，不能因等不到 LISTEN 就判失败。
if lsof -ti tcp:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[后端] 端口 8000 已就绪"
else
  echo "[后端] 进程已启动，正在预热历史K线缓存（首次可达 1~3 分钟，进度见 backend/uvicorn.log）"
  echo "       预热期间页面会提示接口不可用，完成后刷新页面即可，无需重启"
fi

echo ""
echo "[前端] 安装依赖…"
cd "$ROOT/frontend" || exit 1
# 前端 Vite 5 需要 Node 18+；fnm 默认可能是 16，会导致 vite 启动即崩、端口 5174 一直没监听。
# 从 fnm 已安装的版本里挑一个 >=18 的（取最高的）。不硬编码版本号 ——
# 否则 fnm 升级/换版本后路径失效，会静默退回 node 16，前端又起不来。
# 仅在本脚本的 shell 内改 PATH，不动你的全局 fnm 默认。
FNM_BASE="${FNM_DIR:-$HOME/Library/Application Support/fnm}"
if [ -d "$FNM_BASE/node-versions" ]; then
  BEST=""
  for v in $(ls -1 "$FNM_BASE/node-versions" 2>/dev/null | sort -Vr); do
    major=$(printf '%s' "$v" | sed 's/^v//' | cut -d. -f1)
    if [ -n "$major" ] && [ "$major" -ge 18 ] 2>/dev/null; then
      BEST="$FNM_BASE/node-versions/$v/installation/bin"
      break
    fi
  done
  if [ -n "$BEST" ] && [ -x "$BEST/node" ]; then
    export PATH="$BEST:$PATH"
    echo "[前端] 使用 Node $("$BEST/node" -v)（Vite 5 需 18+）"
  else
    echo "[前端] 未找到 Node 18+，使用当前 $("node" -v 2>/dev/null || echo 未知)（可能启动失败）"
  fi
fi
npm install --silent

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
