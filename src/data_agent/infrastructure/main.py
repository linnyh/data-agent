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


async def build_application():
    data_dir = Path(os.environ.get("DATA_AGENT_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    db = Database(data_dir / "app.db")
    db.connect()

    storage = SessionStorage(data_dir / "sessions")

    container = Container()
    checkpointer = await build_checkpointer(data_dir / "checkpoints.db")
    graph = container.build_graph(checkpointer=checkpointer)

    app = create_app(db=db, storage=storage, graph=graph)
    app.state.db = db
    app.state.checkpointer = checkpointer

    # 前端 build 产物托管（共识 #4：生产单端口同源）。dist 不存在时跳过（纯 API 模式）。
    from fastapi.staticfiles import StaticFiles

    dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def main() -> None:
    import uvicorn

    app = asyncio.run(build_application())
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DATA_AGENT_PORT", "8000")))


if __name__ == "__main__":
    main()
