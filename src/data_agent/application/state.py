"""管线图状态（LangGraph state schema）。

对应共识 #8：单一大图；solver 的 attempt 循环在图内由 solve_node 内部
（Python 显式循环）表达，不展开为图边。
"""

from __future__ import annotations

from typing import TypedDict

from data_agent.domain.models import AnalysisGoal, Clarification
from data_agent.domain.pipeline import DocExtractResult, VideoResultAdvice
from data_agent.domain.solver import SolveOutcome


class PipelineState(TypedDict, total=False):
    session_id: str
    goal: AnalysisGoal
    task_dir: str
    knowledge: str
    history: str

    # 澄清（管线前的人机交互，ADR-0004）
    clarification: Clarification | None

    # 视频链（None=未跑；[]=无视频）
    video_parts: list | None
    video_result: VideoResultAdvice | None

    # doc 链（None=未跑；set()=无 doc）
    relevant_stems: set[str] | None
    doc_extract: DocExtractResult | None

    # 表过滤（软过滤：折叠 describe 的 key 集）
    collapse_keys: set[str] | None

    # 解题规划（None=未跑；""=无需/失败）；plan_question 记录规划对应的分析目标，
    # 追问（目标变化）时重跑规划，resume（同目标补澄清）时复用
    plan: str | None
    plan_question: str

    # 求解
    outcome: SolveOutcome | None
