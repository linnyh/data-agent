#!/usr/bin/env bash
# 完整构建 .app:sidecar + tauri build(ad-hoc 签名)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/desktop"

./scripts/build-sidecar.sh

npm install --no-audit --no-fund >/dev/null
npm run tauri -- build --bundles app
echo "产物: desktop/src-tauri/target/release/bundle/macos/DataPivot.app"
