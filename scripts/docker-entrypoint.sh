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

handle_stop() {
  shutdown
  wait "$backend_pid" 2>/dev/null || true
  wait "$frontend_pid" 2>/dev/null || true
  exit 143
}

process_state() {
  # 第三个字段是 Linux 进程状态；Z/X 表示进程已退出但尚未被父进程回收。
  stat_file="/proc/$1/stat"
  if [ ! -r "$stat_file" ]; then
    echo ""
    return
  fi
  awk '{print $3}' "$stat_file" 2>/dev/null || true
}

is_process_alive() {
  state="$(process_state "$1")"
  case "$state" in
    ""|Z|X)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

exit_after_process_stopped() {
  name="$1"
  stopped_pid="$2"
  other_pid="$3"

  set +e
  wait "$stopped_pid"
  exit_code=$?
  set -e

  echo "$name process exited with status $exit_code" >&2
  kill "$other_pid" 2>/dev/null || true
  wait "$other_pid" 2>/dev/null || true

  # 即使子进程正常退出，容器也应按故障退出，便于各种重启策略接管。
  if [ "$exit_code" -eq 0 ]; then
    exit 1
  fi
  exit "$exit_code"
}

trap handle_stop INT TERM

while true; do
  if ! is_process_alive "$backend_pid"; then
    exit_after_process_stopped "backend" "$backend_pid" "$frontend_pid"
  fi

  if ! is_process_alive "$frontend_pid"; then
    exit_after_process_stopped "frontend" "$frontend_pid" "$backend_pid"
  fi

  sleep 2
done
