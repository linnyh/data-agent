"""narrate 节点：结果解读（LLM 叙述 + 表格数据）。

对应共识 #20（叙述 + 表格）与 ADR-0004（交互在管线两端）。
解读失败不阻塞：保留空叙述，表格数据不受影响。
"""

from __future__ import annotations

import json
from typing import Any

from data_agent.application.state import PipelineState
from data_agent.domain.llm import ILLM
from data_agent.domain.solver import SolveOutcome

NARRATE_SYSTEM = """你是数据分析结果的解读助手。用户完成一次分析后, 你用简洁的
自然语言说清结论: 直接回答问题, 点出关键数字, 必要时说明口径/范围/异常。
2-4 句话即可, 不要复述整个表格, 不要虚构表格里没有的数字。"""

_MAX_TABLE_ROWS = 20


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


async def narrate_node(
    state: PipelineState,
    *,
    llm: ILLM,
) -> dict[str, Any]:
    outcome: SolveOutcome | None = state.get("outcome")
    if outcome is None or outcome.result is None:
        return {}
    try:
        narration = await llm.complete_text(
            system=NARRATE_SYSTEM,
            user=_narrate_user(state["goal"].text, outcome),
        )
        outcome.result.narration = narration.strip()
    except Exception:
        pass  # 解读失败不阻塞：保留空叙述
    return {"outcome": outcome}
