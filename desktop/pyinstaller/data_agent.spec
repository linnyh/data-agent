# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec:打包数枢 FastAPI 服务为 onedir sidecar
# 用法:在仓库根执行 .venv/bin/pyinstaller desktop/pyinstaller/data_agent.spec --noconfirm
# ROOT 取 cwd(build-sidecar.sh 保证在仓库根执行),不依赖 SPECPATH

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.getcwd()

# 原生库/动态导入重灾区包:数据+二进制+隐藏导入全收集
datas, binaries, hiddenimports = [], [], []
for pkg in ("faster_whisper", "ctranslate2", "av", "duckdb", "opencc", "uvloop", "tokenizers"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 资源:离线 whisper 模型(计划共识 #4)+ 前端 build 产物
datas += [
    (os.path.join(ROOT, "src", "asr_models"), "asr_models"),
    (os.path.join(ROOT, "web", "dist"), os.path.join("web", "dist")),
]

# 全部 dist-info:pydantic-ai 链在运行时 importlib.metadata.version(...) 查包元数据
import glob
import sysconfig

_site = sysconfig.get_paths()["purelib"]
datas += [(d, os.path.basename(d)) for d in glob.glob(os.path.join(_site, "*.dist-info"))]

# static_ffmpeg 预置 ffmpeg/ffprobe(94M):避免运行时联网下载,保持离线可用
datas += [(os.path.join(_site, "static_ffmpeg", "bin"), os.path.join("static_ffmpeg", "bin"))]

a = Analysis(
    [os.path.join(ROOT, "desktop", "pyinstaller", "entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="data-agent-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="data-agent-server",
)
