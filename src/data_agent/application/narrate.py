"""narrate 节点：结果解读（LLM 叙述 + 可选图表规格）。

对应共识 #20（叙述 + 表格）与 ADR-0004（交互在管线两端）。
解读失败不阻塞：保留空叙述，表格数据不受影响。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from data_agent.application.state import PipelineState
from data_agent.domain.llm import ILLM
from data_agent.domain.models import ChartSpec
from data_agent.domain.solver import SolveOutcome

NARRATE_SYSTEM = """你是数枢的结果解读助手。用户完成一次分析后, 你交付两部分:

1. **narration**: 简洁的自然语言结论。直接回答问题, 点出关键数字, 必要时说明
   口径/范围/异常。2-4 句话即可, 不要复述整个表格, 不要虚构表格里没有的数字。

2. **chart**: 可选图表规格。当结果适合可视化且有助于理解时给出:
   - 分析目标涉及趋势/对比/分布/占比, 且结果表格是聚合后的少量行(≤100 行)时,
     通常应附图; 用户显式要求画图时必画; 明细清单类结果不要画。
   - type: bar(类别对比) / line(时间趋势) / pie(占比, 只允许一个系列) /
     scatter(两个数值维度的散点)。
   - x 是类目轴标签; series 每个系列的数据须与 x 等长, 且只从表格数据中取值。
   - 图表数据点必须与结果表格一致: 不得抽样、不得省略行, 不得虚构表格没有的数字。
   - 只输出与表格对齐的规格, 宁缺毋滥(chart=null)。
"""

_MAX_TABLE_ROWS = 100


class NarrateDecision(BaseModel):
    narration: str
    chart: ChartSpec | None = None


def _narrate_user(goal_text: str, outcome: SolveOutcome) -> str:
    t = outcome.result.table
    rows = t.rows[: _MAX_TABLE_ROWS]
    preview = {
        "columns": t.columns,
        "rows": rows,
        "total_rows": t.total_rows if t.total_rows is not None else len(t.rows),
    }
    return (
        f"## 分析目标\n{goal_text}\n\n"
        f"## 结果表格\n{json.dumps(preview, ensure_ascii=False, default=str)}"
    )


def _valid_chart(chart: ChartSpec | None) -> ChartSpec | None:
    """校验图表规格与数据对齐；不合法返回 None（不阻塞结果）。"""
    if chart is None or not chart.series:
        return None
    n = len(chart.x)
    for s in chart.series:
        if len(s.data) != n:
            return None
    return chart


async def narrate_node(
    state: PipelineState,
    *,
    llm: ILLM,
) -> dict[str, Any]:
    outcome: SolveOutcome | None = state.get("outcome")
    if outcome is None or outcome.result is None:
        return {}
    try:
        decision = await llm.complete_structured(
            system=NARRATE_SYSTEM,
            user=_narrate_user(state["goal"].text, outcome),
            schema=NarrateDecision,
        )
        outcome.result.narration = decision.narration.strip()
        outcome.result.chart = _valid_chart(decision.chart)
    except Exception:
        pass  # 解读失败不阻塞：保留空叙述与无图
    return {"outcome": outcome}
