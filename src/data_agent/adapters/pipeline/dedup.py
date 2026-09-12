"""去重口径判定 Adapter：桥接 内置资产 run_dedup_judger_async。

构造器注入模型工厂（默认 no-think 模型：轻量口径判定）；失败返回空串不阻塞。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from data_agent.assets.agents_v2.dedup_judger_agent import run_dedup_judger_async
from data_agent.domain.models import AnalysisGoal


class AssetDedupJudger:
    """判定最终结果是否应按目标输出列去重，建议文本并入解题规划。"""

    def __init__(self, model_factory=None) -> None:
        self._factory = model_factory or self._default_factory

    @staticmethod
    def _default_factory() -> Any:
        from data_agent.assets.tools_v2.general_tools import build_model_nothink

        return build_model_nothink()

    async def judge(
        self,
        *,
        goal: AnalysisGoal,
        task_dir: Path,
        knowledge: str = "",
    ) -> str:
        try:
            res = await run_dedup_judger_async(
                model=self._factory(),
                task_dir=task_dir,
                question=goal.text,
                knowledge_md=knowledge,
            )
        except Exception:
            return ""
        if res.error:
            return ""
        verdict = "应" if res.should_dedup else "不应"
        lines = [f"## 去重口径建议", f"最终结果**{verdict}**按目标输出列去重。"]
        if res.reason:
            lines.append(f"依据: {res.reason}")
        return "\n".join(lines)
