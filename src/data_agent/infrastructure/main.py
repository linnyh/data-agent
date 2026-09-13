"""服务入口：`python -m data_agent.infrastructure.main`。

环境变量（与 内置资产 兼容）:
- MODEL_API_URL / MODEL_API_KEY / MODEL_NAME : 模型 endpoint
- DATA_AGENT_DATA_DIR : 数据目录（db/checkpoint/会话文件），默认 ./data
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 内置资产（fanout/video 链）的裸导入需要其目录在 sys.path 上
sys.path.insert(
    0, str(Path(__file__).resolve().parents[3] / "内置资产" / "data_agent_baseline")
)

from data_agent.infrastructure.env import load_dotenv

# 用仓库根绝对路径读 .env（cwd 可能不在仓库根，如从 web/ 目录启动）
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from data_agent.infrastructure.api.app import create_app
from data_agent.infrastructure.checkpoint import build_checkpointer
from data_agent.infrastructure.container import Container
from data_agent.infrastructure.db import Database
from data_agent.infrastructure.storage import SessionStorage


def _bundle_root() -> Path:
    """PyInstaller frozen 时资源根为 _MEIPASS,否则为仓库根。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # noqa: SLF001
    return Path(__file__).resolve().parents[3]


async def build_application():
    # 数据目录锚定仓库根（与 .env 读取一致），不依赖进程启动目录；
    # 桌面打包后默认 AppSupport（壳会显式注入 DATA_AGENT_DATA_DIR）
    root = _bundle_root()
    default_data_dir = root / "data"
    if getattr(sys, "frozen", False):
        default_data_dir = Path.home() / "Library" / "Application Support" / "DataPivot"
    data_dir = Path(
        os.environ.get("DATA_AGENT_DATA_DIR", str(default_data_dir))
    ).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        # 桌面 App 用户配置（AppSupport/.env）；setdefault 语义保证环境变量优先
        load_dotenv(data_dir / ".env")

    db = Database(data_dir / "app.db")
    db.connect()

    storage = SessionStorage(data_dir / "sessions")

    container = Container()
    checkpointer = await build_checkpointer(data_dir / "checkpoints.db")
    graph = container.build_graph(checkpointer=checkpointer)

    app = create_app(db=db, storage=storage, graph=graph, llm=container.llm_nothink)
    app.state.db = db
    app.state.checkpointer = checkpointer
    app.state.data_dir = data_dir  # 设置端点持久化 .env 用

    # 前端 build 产物托管（共识 #4：生产单端口同源）。dist 不存在时跳过（纯 API 模式）。
    from fastapi.staticfiles import StaticFiles

    dist = root / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def main() -> None:
    import uvicorn

    app = asyncio.run(build_application())
    # 默认 0.0.0.0(Docker/服务器部署);桌面壳注入 127.0.0.1 仅本机监听
    host = os.environ.get("DATA_AGENT_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=int(os.environ.get("DATA_AGENT_PORT", "8000")))


if __name__ == "__main__":
    main()
