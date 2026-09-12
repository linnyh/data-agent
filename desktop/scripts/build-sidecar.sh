#!/usr/bin/env bash
# 构建 Python sidecar(PyInstaller onedir)并放入 src-tauri/binaries/<name>-<target-triple>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# PyInstaller 需运行在项目 venv 内(分析依赖链)
uv pip install pyinstaller >/dev/null

.venv/bin/pyinstaller desktop/pyinstaller/data_agent.spec --noconfirm

# ad-hoc 签名(arm64 macOS 运行硬性要求)
codesign --force --deep --sign - "dist/data-agent-server/data-agent-server" \
  || echo "警告: sidecar 签名失败,tauri dev 可能无法启动"

TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
mkdir -p desktop/src-tauri/binaries
rm -rf "desktop/src-tauri/binaries/data-agent-server-${TRIPLE}"
cp -R "dist/data-agent-server" "desktop/src-tauri/binaries/data-agent-server-${TRIPLE}"
echo "sidecar 已产出: desktop/src-tauri/binaries/data-agent-server-${TRIPLE}"
