"""管线域接口：文档抽取与视频预处理（依赖倒置，domain 不感知 内置资产 资产）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from data_agent.domain.models import AnalysisGoal


@dataclass
class DocExtractResult:
    """文档结构化抽取结果（每个相关 doc 一张同名表）。"""

    db_path: Path | None = None
    tables: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def n_tables(self) -> int:
        return len(self.tables)


@dataclass
class VideoPartsResult:
    """视频多模态 parts（图片+版面+旁白交错，按时间轴）。"""

    parts: list = field(default_factory=list)
    has_video: bool = False


@dataclass
class VideoResultAdvice:
    """视频答案预判（前置判定 agent 的产出，仅供 solver 参考）。"""

    decision: str = "RECOMPUTE"
    has_displayed_answer: bool = False
    confidence: float = 0.0
    claimed_values: list | None = None
    normalized_values: list | None = None
    target_columns: list = field(default_factory=list)
    source_frames: list = field(default_factory=list)
    distractor_notes: str = ""
    has_video: bool = False
    error: str = ""


class IDocExtractor(Protocol):
    """把 doc/*.md（+pdf reflow）抽取为结构化表（context/db/<stem>.db）。"""

    async def extract(
        self,
        *,
        task_dir: Path,
        log_dir: Path,
        relevant_stems: set[str] | None = None,
    ) -> DocExtractResult:
        """抽取相关 doc；relevant_stems=None 表示抽取全部。任何失败不抛异常。"""
        ...


class IVideoPreprocessor(Protocol):
    """视频 → 多模态 parts（抽帧 + ASR + hiccup + 时间轴交错）。"""

    def preprocess(
        self,
        *,
        task_dir: Path,
        question: str = "",
        knowledge: str = "",
        log_dir: Path | None = None,
    ) -> VideoPartsResult:
        """无视频时返回 has_video=False 的空结果；失败不抛异常。"""
        ...


class IVideoResultJudge(Protocol):
    """判定视频面板是否已直接显示答案（多轮投票，仅供 solver 参考的建议）。"""

    async def judge(
        self,
        *,
        task_dir: Path,
        log_dir: Path | None = None,
        vote_rounds: int = 5,
    ) -> VideoResultAdvice:
        """返回预判建议；无视频/失败时 has_video=False。"""
        ...


class IPlanner(Protocol):
    """解题规划：在求解前产出一份规划文本（markdown），供 solver 参考。

    实现可带只读探查工具实查数据验证口径假设；失败返回空串不阻塞。
    """

    async def plan(
        self,
        *,
        goal: AnalysisGoal,
        task_dir: Path,
        knowledge: str = "",
    ) -> str:
        """返回解题规划 markdown；无规划/失败时返回空串。"""
        ...
