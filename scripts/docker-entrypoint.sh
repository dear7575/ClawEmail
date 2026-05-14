#!/bin/sh
set -eu

# 容器内同时启动 FastAPI 与 Next.js，任一进程退出都让容器退出，便于 Docker 重启策略接管。
cd /app

python /app/backend/app/main.py &
backend_pid=$!

cd /app/frontend
node ./node_modules/next/dist/bin/next start \
  --hostname "${FRONTEND_HOST:-0.0.0.0}" \
  --port "${FRONTEND_PORT:-3000}" &
frontend_pid=$!

shutdown() {
  # 收到停止信号时同时关闭前后端，避免留下孤儿进程。
  kill "$backend_pid" "$frontend_pid" 2>/dev/null || true
}

trap shutdown INT TERM

while true; do
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    shutdown
    wait "$frontend_pid" 2>/dev/null || true
    exit 1
  fi

  if ! kill -0 "$frontend_pid" 2>/dev/null; then
    shutdown
    wait "$backend_pid" 2>/dev/null || true
    exit 1
  fi

  sleep 2
done
