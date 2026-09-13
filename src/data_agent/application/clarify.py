"""clarify 节点：意图三态判定 + interrupt 澄清。

对应 ADR-0004：人机交互只在管线两端——澄清发生在执行管线之前；
非分析请求（寒暄/闲聊/系统能力询问）走快速回复路径，不进管线。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from data_agent.application.state import PipelineState
from data_agent.domain.llm import ILLM
from data_agent.domain.models import AnalysisGoal, Clarification, Result, TableData
from data_agent.domain.solver import SolveOutcome

CLARIFY_SYSTEM = """你是数枢（数据分析 Agent）的前置意图识别助手。对用户的输入做三态判定:

1. **is_chitchat**: 输入不是数据分析请求, 也不涉及数据集——仅限寒暄("你好")、
   闲聊、询问系统能力("你能做什么")。此时直接给出简短友好回复(中文, 1-2 句),
   不要尝试分析。
   注意: 询问数据集/文件本身的问题**不是** chitchat——如"有哪些文件"、
   "这两个文件是什么"、"文件里有几列"——必须直通管线(由求解器查库回答),
   禁止用闲聊自由回复臆测文件内容。

2. **need_clarification**: 是数据分析请求, 但目标存在歧义且答案会不同:
   - 目标列/指标不明确;
   - 过滤条件、时间范围、单位、去重规则有多种合理解释。
   需要澄清时 question 必须一句话精确问出歧义点。

3. 两者皆否: 是明确的数据分析请求, 直接执行(轻微模糊不影响执行时宁可不问)。

输出规则: is_chitchat 与 need_clarification 不能同时为 true。

会话历史(如有)记录了此前轮次的问答。闲聊时自然衔接历史;
判断歧义时用历史理解指代(如"换个口径"指的是什么), 不要重复问历史已澄清过的口径。
"""


class ClarifyDecision(BaseModel):
    is_chitchat: bool = Field(
        default=False,
        description="true=输入不是数据分析请求(寒暄/闲聊/能力询问), 需要直接回复",
    )
    reply: str = Field(
        default="",
        description="is_chitchat=true 时的直接回复(中文, 1-2 句); 否则留空",
    )
    need_clarification: bool = Field(
        default=False,
        description="true=数据分析请求但必须澄清后才能正确执行",
    )
    question: str = Field(
        default="",
        description="需要澄清时的提问(一句话, 精确指出歧义点); 否则留空",
    )


def _clarify_user(
    goal: AnalysisGoal, knowledge: str, context_preview: str, history: str = ""
) -> str:
    kn = (knowledge or "").strip()[:4000]
    hist = (history or "").strip()[:3000]
    return (
        f"## 分析目标\n{goal.text}\n\n"
        f"## knowledge\n{kn or '(无)'}\n\n"
        f"## 数据集结构预览\n{context_preview or '(未提供)'}\n\n"
        f"{hist or '## 会话历史\n(无)'}"
    )


async def clarify_node(
    state: PipelineState,
    *,
    llm: ILLM,
    context_preview: str = "",
) -> dict[str, Any]:
    """三态判定；非分析请求直接产 outcome（走快速路径），
    需澄清则 interrupt 挂起，明确请求直通管线。"""
    goal: AnalysisGoal = state["goal"]
    knowledge = state.get("knowledge", "")

    try:
        decision = await llm.complete_structured(
            system=CLARIFY_SYSTEM,
            user=_clarify_user(goal, knowledge, context_preview, state.get("history", "")),
            schema=ClarifyDecision,
        )
    except Exception:
        return {}  # 判定失败不阻塞（按可直接执行）

    if decision.is_chitchat:
        # 快速路径：一次 LLM 调用直接回复，不进管线
        return {
            "outcome": SolveOutcome(
                status="ok",
                result=Result(
                    narration=decision.reply or "你好! 我是数枢, 上传数据后告诉我你的分析目标即可。",
                    table=TableData(columns=[], rows=[], total_rows=0),
                ),
                attempts=0,
            )
        }

    if not decision.need_clarification or not decision.question.strip():
        return {}

    answer = interrupt(
        {"clarification": Clarification(question=decision.question.strip())}
    )
    merged = AnalysisGoal(
        text=f"{goal.text}\n\n(澄清问答: 问: {decision.question.strip()} 答: {answer})"
    )
    return {"goal": merged}
