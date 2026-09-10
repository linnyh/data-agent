"""相关性判定接口：doc / 表的召回优先筛选（软过滤 vs 硬跳过的语义由调用方决定）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class RelevanceCandidate:
    """一个待判定的候选（一篇 doc 或一张结构化表）。"""

    key: str
    name: str
    preview: str


@dataclass
class RelevanceVerdict:
    """判定结果。

    语义约定（召回优先）：实现失败/超时时必须返回 relevant=all_candidates，
    即"不过滤"的安全降级——宁可多处理，不可漏处理。
    """

    relevant: set[str]
    skipped: set[str]
    all_candidates: set[str]
    vote_stats: dict = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        """是否为安全降级（relevant 未被可信地收窄）。"""
        return self.relevant == self.all_candidates


class IRelevanceJudge(Protocol):
    """判定哪些候选与当前分析目标相关。"""

    async def judge(
        self,
        *,
        question: str,
        knowledge: str,
        candidates: list[RelevanceCandidate],
        log_dir: Path | None = None,
    ) -> RelevanceVerdict:
        """对 candidates 做多轮投票判定；任何失败按召回优先兜底。"""
        ...
