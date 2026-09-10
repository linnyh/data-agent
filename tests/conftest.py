"""Pytest 公共配置：注入 内置资产 裸导入路径。

内置资产 代码使用裸包名导入（`import tools_v2`），依赖其自身目录在 sys.path 上
（与原方案 `python src/data_agent_baseline/zz_agent_v2.py` 的机制一致）。
"""

import sys
from pathlib import Path

LEGACY_PKG = Path(__file__).resolve().parents[1] / "内置资产" / "data_agent_baseline"
if str(LEGACY_PKG) not in sys.path:
    sys.path.insert(0, str(LEGACY_PKG))
