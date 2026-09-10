"""M2 验收：求解沙箱 / 脚手架 / 文件工具 / attempt 循环（不依赖真实 LLM endpoint）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd
import pytest
from langchain_core.language_models.chat_models import SimpleChatModel

from data_agent.adapters.sandbox import SubprocessSolverSandbox
from data_agent.adapters.scaffold import ScaffoldGenerator
from data_agent.adapters.solver_files import SolverFileTool
from data_agent.adapters.solver_agent import LangGraphSolver
from data_agent.domain.models import AnalysisGoal


@pytest.fixture()
def task_dir(tmp_path: Path) -> Path:
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "t.csv").write_text("id,value\n1,10\n2,20\n3,30\n", encoding="utf-8")
    (ctx / "knowledge.md").write_text("# 知识\n", encoding="utf-8")
    (tmp_path / "task.json").write_text('{"question": "列出 value 全部数据"}', encoding="utf-8")
    return tmp_path


# -- scaffold ----------------------------------------------------------------


def test_scaffold_generates_and_runs(task_dir: Path):
    gen = ScaffoldGenerator()
    path = gen.generate(task_dir)
    assert path.name == "solver.py"
    code = path.read_text()
    assert "DuckDBDataSourceRegistry" in code
    assert "## 进行查询" in code
    assert "prediction.csv" in code

    # 未填查询: 跑通但 NO OUTPUT（不写 prediction.csv）
    sb = SubprocessSolverSandbox()
    out = sb.execute_solver(task_dir)
    assert "NO OUTPUT" in out
    assert not sb.prediction_path(task_dir).exists()


def test_scaffold_filled_query_produces_prediction(task_dir: Path):
    gen = ScaffoldGenerator()
    path = gen.generate(task_dir)
    code = path.read_text()
    code = code.replace(
        'result = None  # TODO: 替换为上面示例形式的真实查询',
        'result = run_sql("""SELECT value FROM df_t""")',
    )
    path.write_text(code)

    sb = SubprocessSolverSandbox()
    out = sb.execute_solver(task_dir)
    assert "OK" in out
    assert "rows=3" in out
    pred = pd.read_csv(sb.prediction_path(task_dir))
    assert list(pred["value"]) == [10, 20, 30]


# -- sandbox -----------------------------------------------------------------


def test_sandbox_timeout(task_dir: Path):
    gen = ScaffoldGenerator()
    path = gen.generate(task_dir)
    code = path.read_text().replace(
        'result = None  # TODO: 替换为上面示例形式的真实查询',
        "import time\ntime.sleep(5)\nresult = run_sql('SELECT 1')",
    )
    path.write_text(code)
    sb = SubprocessSolverSandbox()
    out = sb.execute_solver(task_dir, timeout_s=1)
    assert "TIMEOUT" in out


def test_sandbox_rejects_missing_solver(tmp_path: Path):
    sb = SubprocessSolverSandbox()
    out = sb.execute_solver(tmp_path)
    assert "未找到" in out


# -- solver file tool --------------------------------------------------------


def test_file_tool_read_edit_roundtrip(task_dir: Path):
    gen = ScaffoldGenerator()
    gen.generate(task_dir)
    ft = SolverFileTool(task_dir)

    content = ft.read()
    assert "1:" in content  # hashline 标记
    first_line = content.splitlines()[0]
    line_no, hash_ = first_line.split(":", 1)[0], first_line.split("|", 1)[0].split(":")[1]

    out = ft.edit(
        start_line=int(line_no),
        start_hash=hash_,
        new_content="# this file path is `./workdir/solver.py` (edited)",
    )
    assert "Edited" in out
    assert "(edited)" in ft.read()


def test_file_tool_hash_mismatch(task_dir: Path):
    gen = ScaffoldGenerator()
    gen.generate(task_dir)
    ft = SolverFileTool(task_dir)
    out = ft.edit(start_line=1, start_hash="XX", new_content="# nope")
    assert "hash 校验失败" in out


def test_file_tool_syntax_rollback(task_dir: Path):
    gen = ScaffoldGenerator()
    gen.generate(task_dir)
    ft = SolverFileTool(task_dir)
    before = ft.read()
    out = ft.edit(start_line=1, start_hash="WQ", new_content="def broken(:\n  pass")
    # hash 可能不对；换一种：直接对第一行真实 hash 做坏编辑
    first = before.splitlines()[0]
    real_line, real_hash = int(first.split(":", 1)[0]), first.split("|", 1)[0].split(":")[1]
    out = ft.edit(start_line=real_line, start_hash=real_hash, new_content="def broken(:\n  pass\n")
    assert "SyntaxError" in out
    assert ft.read() == before  # 回滚，内容未变


# -- solver attempt 循环（fake model） ----------------------------------------


class _NoopChatModel(SimpleChatModel):
    """永不调用工具的假模型：直接返回文本（不产生工具调用）。"""

    @property
    def _llm_type(self) -> str:
        return "noop"

    def _call(self, messages, stop=None, run_manager=None, **kwargs) -> str:
        return "done"

    def bind_tools(self, tools, **kwargs):
        # 假模型不支持真实工具调用：绑定后原样返回自身
        return self


def test_solver_attempts_exhaust_and_fallback(task_dir: Path):
    """假模型不写代码：attempt 循环耗尽 → 兜底 sandbox → 仍无产出 → failed。"""
    from data_agent.adapters.models import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM.__new__(OpenAICompatibleLLM)
    llm._chat = _NoopChatModel()
    llm.model_name = "noop"

    solver = LangGraphSolver(llm=llm, request_limit=3)
    outcome = asyncio.run(
        solver.solve(
            goal=AnalysisGoal(text="列出 value 全部数据"),
            task_dir=task_dir,
            max_attempts=2,
        )
    )
    assert outcome.status == "failed"
    assert outcome.attempts == 2


class _FakeSandbox:
    """假沙箱：兜底执行直接写出 prediction.csv（模拟子进程产出）。"""

    def execute_solver(self, task_dir: Path, *, timeout_s: float = 120.0) -> str:
        (task_dir / "workdir" / "prediction.csv").write_text(
            "value\n10\n20\n30\n", encoding="utf-8"
        )
        return "OK"

    def prediction_path(self, task_dir: Path) -> Path:
        return task_dir / "workdir" / "prediction.csv"


def test_solver_fallback_produces_result(task_dir: Path):
    """假模型不写代码 → attempt 耗尽 → 兜底 sandbox 产出 → ok + Result 加载正确。"""
    from data_agent.adapters.models import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM.__new__(OpenAICompatibleLLM)
    llm._chat = _NoopChatModel()
    llm.model_name = "noop"

    solver = LangGraphSolver(llm=llm, sandbox=_FakeSandbox(), request_limit=3)
    outcome = asyncio.run(
        solver.solve(
            goal=AnalysisGoal(text="列出 value 全部数据"),
            task_dir=task_dir,
            max_attempts=2,
        )
    )
    assert outcome.status == "ok"
    assert outcome.result is not None
    assert outcome.result.table.columns == ["value"]
    assert outcome.result.table.rows == [[10], [20], [30]]
