#!/usr/bin/env bash
# M1 验收:打包 sidecar 独立运行 + 完整跑通一次上传分析
# 用法: DATA_AGENT_DATA_DIR=<dir> [PORT=8799] ./scripts/e2e-m1.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PORT="${PORT:-8799}"
DATA_DIR="${DATA_AGENT_DATA_DIR:-/tmp/datapivot-m1-e2e}"
SIDECAR="desktop/src-tauri/binaries/data-agent-server-$(rustc -vV | sed -n 's/^host: //p')/data-agent-server"
E2E_LOG=/tmp/m1-e2e.log

[[ -x "$SIDECAR" ]] || { echo "先运行 scripts/build-sidecar.sh"; exit 1; }

rm -rf "$DATA_DIR" "$E2E_LOG"
mkdir -p "$DATA_DIR"
# .env 必须在 sidecar 启动前就位(load_dotenv 于启动时执行);可选:无则分析失败
[ -f .env ] && cp .env "$DATA_DIR/.env"

DATA_AGENT_PORT="$PORT" DATA_AGENT_DATA_DIR="$DATA_DIR" "$SIDECAR" > "$E2E_LOG" 2>&1 &
SIDE_PID=$!
trap 'kill $SIDE_PID 2>/dev/null || true' EXIT

for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/sessions" 2>/dev/null || true)
  [ "$code" = "200" ] && break
  sleep 2
done
[ "$code" = "200" ] || { echo "FAIL: sidecar 未就绪"; tail -20 "$E2E_LOG"; exit 1; }
echo "1/4 sidecar 就绪"

SID=$(curl -s -X POST "http://127.0.0.1:$PORT/sessions" -H "Content-Type: application/json" \
  -d '{"title":"m1-e2e"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "2/4 会话创建: $SID"

curl -s -X POST "http://127.0.0.1:$PORT/sessions/$SID/upload" -F "file=@demo_sales.csv" > /dev/null
echo "3/4 文件上传完成"

curl -s -N -X POST "http://127.0.0.1:$PORT/sessions/$SID/chat" \
  -H "Content-Type: application/json" \
  -d '{"question":"这个 CSV 里总销售额是多少?一句话回答。"}' --max-time 900 \
  > /tmp/m1-chat-sse.txt || true

NARR=$(python3 - /tmp/m1-chat-sse.txt <<'PYEOF'
import json, sys
lines = [l for l in open(sys.argv[1], encoding='utf-8').read().splitlines() if l.startswith('data:')]
narration = ''
for l in lines:
    try:
        d = json.loads(l[5:])
        if d.get('type') == 'result':
            narration = d.get('narration') or ''
    except Exception:
        pass
print(narration)
PYEOF
)
if [ -n "$NARR" ]; then
  echo "4/4 分析完成: ${NARR:0:120}"
  echo "PASS"
else
  echo "FAIL: 未得到分析结果(检查 $DATA_DIR/.env 是否有模型配置)"
  tail -5 "$E2E_LOG"
  exit 1
fi
