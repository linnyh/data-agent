"""极简 .env 加载（不引入 python-dotenv 依赖）。

已存在的环境变量优先（shell export 覆盖 .env）；
支持 # 注释与 KEY=VALUE / KEY="VALUE" 形式。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    p = path or Path.cwd() / ".env"
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)
