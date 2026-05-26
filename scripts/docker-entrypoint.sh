#!/bin/sh
set -eu

# 容器内同时启动 FastAPI 与 Next.js，任一进程退出都让容器退出，便于 Docker 重启策略接管。
cd /app

health_interval="${HEALTHCHECK_INTERVAL_SECONDS:-30}"
health_timeout="${HEALTHCHECK_TIMEOUT_SECONDS:-5}"
health_retries="${HEALTHCHECK_RETRIES:-3}"
health_start_period="${HEALTHCHECK_START_PERIOD_SECONDS:-45}"
health_failures=0
last_health_check=0
health_start_after=$(($(date +%s) + health_start_period))
backend_pid=""
frontend_pid=""

shutdown() {
  # 收到停止信号时同时关闭前后端，避免留下孤儿进程。
  if [ -n "$backend_pid" ]; then
    kill "$backend_pid" 2>/dev/null || true
  fi
  if [ -n "$frontend_pid" ]; then
    kill "$frontend_pid" 2>/dev/null || true
  fi
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

wait_for_backend_ready() {
  backend_url="http://127.0.0.1:${PORT:-8000}/health"
  backend_wait_deadline=$(($(date +%s) + ${BACKEND_START_TIMEOUT_SECONDS:-30}))
  while ! curl -fsS --max-time 2 "$backend_url" >/dev/null 2>&1; do
    if ! is_process_alive "$backend_pid"; then
      exit_after_process_stopped "backend" "$backend_pid" "$backend_pid"
    fi
    if [ "$(date +%s)" -ge "$backend_wait_deadline" ]; then
      echo "backend did not become ready before frontend startup: $backend_url" >&2
      wait "$backend_pid" 2>/dev/null || true
      exit 1
    fi
    sleep 1
  done
}

check_url() {
  name="$1"
  url="$2"
  if curl -fsS --max-time "$health_timeout" "$url" >/dev/null 2>&1; then
    return 0
  fi
  echo "$name health check failed: $url" >&2
  return 1
}

check_http_health() {
  now="$(date +%s)"
  if [ "$now" -lt "$health_start_after" ]; then
    return 0
  fi
  if [ $((now - last_health_check)) -lt "$health_interval" ]; then
    return 0
  fi

  last_health_check="$now"
  backend_url="http://127.0.0.1:${PORT:-8000}/health"
  frontend_url="http://127.0.0.1:${FRONTEND_PORT:-3000}/health"
  if check_url "backend" "$backend_url" && check_url "frontend" "$frontend_url"; then
    if [ "$health_failures" -gt 0 ]; then
      echo "container health check recovered" >&2
    fi
    health_failures=0
    return 0
  fi

  health_failures=$((health_failures + 1))
  echo "container health check failed ($health_failures/$health_retries)" >&2
  if [ "$health_failures" -ge "$health_retries" ]; then
    echo "health failure threshold reached, exiting for Docker restart policy" >&2
    shutdown
    wait "$backend_pid" 2>/dev/null || true
    wait "$frontend_pid" 2>/dev/null || true
    exit 1
  fi
}

trap handle_stop INT TERM

python /app/backend/app/main.py &
backend_pid=$!

wait_for_backend_ready

cd /app/frontend
node ./node_modules/next/dist/bin/next start \
  --hostname "${FRONTEND_HOST:-0.0.0.0}" \
  --port "${FRONTEND_PORT:-3000}" &
frontend_pid=$!

while true; do
  if ! is_process_alive "$backend_pid"; then
    exit_after_process_stopped "backend" "$backend_pid" "$frontend_pid"
  fi

  if ! is_process_alive "$frontend_pid"; then
    exit_after_process_stopped "frontend" "$frontend_pid" "$backend_pid"
  fi

  check_http_health

  sleep 2
done
