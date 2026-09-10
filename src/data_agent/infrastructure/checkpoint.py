"""LangGraph checkpoint 持久化（共识 #11：AsyncSqliteSaver，thread_id=会话 ID）。"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from data_agent.domain.models import AnalysisGoal, Clarification
from data_agent.domain.pipeline import DocExtractResult, VideoPartsResult, VideoResultAdvice
from data_agent.domain.solver import SolveOutcome

# 图状态里会进 checkpoint 的全部领域类型（显式允许序列化，
# 避免未来版本 strict 模式下拒绝反序列化）
_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        ("data_agent.domain.models", "Result"),
        ("data_agent.domain.solver", "SolveOutcome"),
        ("data_agent.domain.pipeline", "VideoResultAdvice"),
        ("data_agent.domain.pipeline", "DocExtractResult"),
        ("data_agent.domain.pipeline", "VideoPartsResult"),
        ("data_agent.domain.models", "AnalysisGoal"),
        ("data_agent.domain.models", "Clarification"),
    ]
)


async def build_checkpointer(path: Path) -> AsyncSqliteSaver:
    """构建 AsyncSqliteSaver（调用方负责 close）。

    Postgres 替换点：仅换此工厂实现（开闭原则，ADR 决策 #11）。
    """
    conn = await aiosqlite.connect(path)
    return AsyncSqliteSaver(conn, serde=_SERDE)
