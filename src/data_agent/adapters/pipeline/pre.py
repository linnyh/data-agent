"""知识精选与输出形态预测 Adapter：桥接 内置资产 run_pre_agent。

构造器注入模型工厂（默认 no-think 模型：单轮结构化抽取）；失败返回空串不阻塞。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from data_agent.assets.agents_v2.pre_agent import run_pre_agent
from data_agent.domain.models import AnalysisGoal


class AssetPreAgent:
    """抽取强对齐知识案例/字段约束，并预测输出列与行数上限，并入解题规划。"""

    def __init__(self, model_factory=None) -> None:
        self._factory = model_factory or self._default_factory

    @staticmethod
    def _default_factory() -> Any:
        from data_agent.assets.tools_v2.general_tools import build_model_nothink

        return build_model_nothink()

    async def extract(
        self,
        *,
        goal: AnalysisGoal,
        task_dir: Path,
        knowledge: str = "",
    ) -> str:
        try:
            out = await run_pre_agent(
                model=self._factory(),
                task_question=goal.text,
                knowledge_md=knowledge,
            )
        except Exception:
            return ""
        sections: list[str] = []
        if (out.related_use_cases or "").strip():
            sections.append(f"## 相关知识案例\n{out.related_use_cases.strip()}")
        if (out.related_field_constraints or "").strip():
            sections.append(f"## 相关字段约束\n{out.related_field_constraints.strip()}")
        shape = [f"任务类型: {out.task_type}"]
        if out.output_columns:
            shape.append(f"预期输出列: {', '.join(out.output_columns)}")
        if out.row_limit and out.row_limit > 0:
            shape.append(f"行数上限: {out.row_limit}")
        if (out.task_summary or "").strip():
            shape.append(f"任务摘要: {out.task_summary.strip()}")
        sections.append("## 输出形态建议\n" + "; ".join(shape))
        return "\n\n".join(sections)
