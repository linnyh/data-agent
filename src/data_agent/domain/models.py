"""领域模型：对应 CONTEXT.md 术语表的持久化对象。

本模块只含数据定义，无行为逻辑。领域规则（如状态转移、权限）
由 application 层的用例服务承载。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now_utc() -> datetime:
    return datetime.now(UTC)


class SessionStatus(StrEnum):
    """会话生命周期状态（ADR-0004：澄清只发生在执行管线之前）。"""

    CREATED = "created"
    CLARIFYING = "clarifying"
    ANALYZING = "analyzing"
    DONE = "done"
    FAILED = "failed"


class DataSourceKind(StrEnum):
    CSV = "csv"
    JSON = "json"
    SQLITE = "sqlite"


class DataSourceFile(BaseModel):
    """数据集中的单个结构化表格文件。"""

    name: str
    kind: DataSourceKind
    path: str


class DocumentFile(BaseModel):
    """数据集中的散文式文档（md/pdf），需结构化抽取才能被分析使用。"""

    name: str
    path: str
    is_pdf: bool = False


class VideoBriefing(BaseModel):
    """数据集中的操作讲解视频（briefing.mp4）。"""

    name: str = "briefing.mp4"
    path: str


class Dataset(BaseModel):
    """用户上传给一个会话的数据文件集合。"""

    sources: list[DataSourceFile] = Field(default_factory=list)
    documents: list[DocumentFile] = Field(default_factory=list)
    video: VideoBriefing | None = None


class AnalysisGoal(BaseModel):
    """用户在一个会话中提出的具体数据分析问题。

    追问（用户看到结果后发起的新分析）就是一个新的 AnalysisGoal。
    """

    text: str
    created_at: datetime = Field(default_factory=_now_utc)


class Clarification(BaseModel):
    """Agent 在分析前因目标存在歧义而向用户发起的提问。"""

    question: str
    context: str | None = None


class TableData(BaseModel):
    """结果中的结构化表格部分。"""

    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    total_rows: int | None = None


class Result(BaseModel):
    """一次分析执行交付给用户的产出：叙述解读 + 结构化表格。"""

    narration: str
    table: TableData
    produced_at: datetime = Field(default_factory=_now_utc)
    source_kind: Literal["solver", "fallback"] = "solver"


class Session(BaseModel):
    """用户与 Agent 之间的一段多轮对话，绑定一份数据集。"""

    session_id: str
    user_id: str
    dataset: Dataset = Field(default_factory=Dataset)
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = Field(default_factory=_now_utc)
