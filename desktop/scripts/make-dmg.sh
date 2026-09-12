#!/usr/bin/env bash
# 生成 DataPivot.dmg(需先跑 build-app.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$ROOT/desktop/src-tauri/target/release/bundle/macos/DataPivot.app"
OUT="$ROOT/desktop/DataPivot.dmg"

[[ -d "$APP" ]] || { echo "先运行 scripts/build-app.sh"; exit 1; }

hdiutil create -volname DataPivot -srcfolder "$APP" -ov -format UDZO "$OUT"
echo "dmg 已产出: $OUT"
