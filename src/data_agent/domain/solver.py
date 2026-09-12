"""求解域接口：求解脚本的执行沙箱与求解用例（依赖倒置，domain 不依赖引擎/框架）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from data_agent.domain.models import AnalysisGoal, Result


@dataclass
class SolveOutcome:
    """一次求解（含 attempt 重试）的最终结果。"""

    status: Literal["ok", "failed"]
    result: Result | None = None
    attempts: int = 0
    error: str = ""

    @property
    def prediction_exists(self) -> bool:
        return self.result is not None


class ISolverSandbox(Protocol):
    """执行求解脚本并回读产出的执行边界。

    实现必须保证：子进程执行（非当前进程内）、超时终止、
    输出包含三态结论（FAILED / NO OUTPUT / OK+自检）。
    """

    def execute_solver(self, task_dir: Path, *, timeout_s: float = 120.0) -> str:
        """运行 task_dir/workdir/solver.py，返回三态人类可读报告。"""
        ...

    def prediction_path(self, task_dir: Path) -> Path:
        """预测结果文件路径（workdir/prediction.csv）。"""
        ...


class IScaffoldGenerator(Protocol):
    """生成求解脚本骨架（读数据 block + 查询桩 + 保存样板）。"""

    def generate(self, task_dir: Path) -> Path:
        """检查 task_dir 并写入 workdir/solver.py，返回其路径。"""
        ...

    def render(self, task_dir: Path, question: str) -> str:
        """渲染脚手架源码文本（纯函数，供测试与对齐）。"""
        ...


class ISolver(Protocol):
    """求解用例：给定分析目标与任务目录，产出结果表格。

    实现内部负责 ReAct 循环、attempt 重试与脚手架恢复。
    """

    async def solve(
        self,
        *,
        goal: AnalysisGoal,
        task_dir: Path,
        knowledge: str = "",
        history: str = "",
        plan: str = "",
        max_attempts: int = 5,
    ) -> SolveOutcome:
        """执行求解并返回结果。plan 为前置解题规划（可空）；任何失败不抛异常，以 SolveOutcome 承载。"""
        ...
