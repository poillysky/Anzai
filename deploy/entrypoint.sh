#!/bin/sh
set -eu

mkdir -p /app/data

# Prefer mounted data SQLite path
export DATABASE_URL="${DATABASE_URL:-sqlite:////app/data/anzai.db}"
export API_PROXY_TARGET="${API_PROXY_TARGET:-http://127.0.0.1:8515}"
export PORT="${PORT:-3515}"
export HOSTNAME="${HOSTNAME:-0.0.0.0}"

cd /app/api
uvicorn app.main:app --host 0.0.0.0 --port 8515 &
API_PID=$!

cd /app/web
node server.js &
WEB_PID=$!

term() {
  kill -TERM "$API_PID" "$WEB_PID" 2>/dev/null || true
  wait "$API_PID" "$WEB_PID" 2>/dev/null || true
}
trap term INT TERM

# Exit if either process dies
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
  sleep 2
done
term
exit 1
