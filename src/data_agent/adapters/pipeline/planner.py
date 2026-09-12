"""解题规划 Adapter：桥接 内置资产 run_plan_agent_async。

构造器注入模型工厂（pydantic-ai 兼容 think 模型）；规划失败返回空串不阻塞。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from data_agent.assets.agents_v2.plan_agent import run_plan_agent_async
from data_agent.domain.models import AnalysisGoal


class AssetPlanner:
    """求解前的解题规划（带 explore_data 实查验证口径假设）。"""

    def __init__(self, model_factory=None) -> None:
        self._factory = model_factory or self._default_factory

    @staticmethod
    def _default_factory() -> Any:
        from data_agent.assets.tools_v2.general_tools import build_model

        return build_model()

    async def plan(
        self,
        *,
        goal: AnalysisGoal,
        task_dir: Path,
        knowledge: str = "",
    ) -> str:
        try:
            res = await run_plan_agent_async(
                model=self._factory(),
                task_dir=task_dir,
                question=goal.text,
                knowledge_md=knowledge,
            )
        except Exception:
            return ""
        return res.plan_md or ""
