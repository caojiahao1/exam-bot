#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  exam-bot - 开卷答题助手"
echo "============================================"
echo

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[错误] 没有找到 Python 3，请先安装：https://www.python.org/downloads/"
  exit 1
fi

echo "[1/2] 启动本地服务..."
"$PY" app/server.py &
SRV=$!
sleep 2

echo "[2/2] 打开控制台..."
URL="http://127.0.0.1:8778"
if command -v open >/dev/null 2>&1; then open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
else echo "请手动打开：$URL"; fi

echo
echo "服务已在后台运行（PID $SRV）。按 Ctrl+C 停止。"
wait $SRV
