"""M4 验收：对话层（clarify interrupt / narrate / 追问共享 thread 幂等）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langgraph.types import Command

from data_agent.application.graph import PipelineGraphBuilder
from data_agent.domain.models import AnalysisGoal, Result, TableData
from data_agent.domain.pipeline import DocExtractResult, VideoPartsResult
from data_agent.domain.solver import SolveOutcome


class FakeLLM:
    """可编程 fake LLM：clarify 判定 + narrate 叙述。"""

    def __init__(self, need_clarification: bool = False, question: str = ""):
        self.need_clarification = need_clarification
        self.question = question
        self.clarify_calls = 0
        self.narrate_calls = 0
        self.clarify_users: list[str] = []

    async def complete_structured(self, *, system: str, user: str, schema):
        self.clarify_calls += 1
        self.clarify_users.append(user)
        return schema(
            need_clarification=self.need_clarification,
            question=self.question,
        )

    async def complete_text(self, *, system: str, user: str) -> str:
        self.narrate_calls += 1
        return "共 2 行数据。"


class FakeSolver:
    def __init__(self):
        self.calls: list[str] = []
        self.histories: list[str] = []

    async def solve(self, *, goal, task_dir, knowledge="", history="", max_attempts=5) -> SolveOutcome:
        self.calls.append(goal.text)
        self.histories.append(history)
        return SolveOutcome(
            status="ok",
            result=Result(
                narration="",
                table=TableData(columns=["value"], rows=[[1], [2]], total_rows=2),
            ),
            attempts=1,
        )


class FakeDocExtractor:
    async def extract(self, *, task_dir, log_dir, relevant_stems=None) -> DocExtractResult:
        return DocExtractResult(db_path=task_dir / "context" / "db", tables=["a"])


class FakeVideoPreprocessor:
    def preprocess(self, *, task_dir, question="", knowledge="", log_dir=None) -> VideoPartsResult:
        return VideoPartsResult(parts=[], has_video=False)


@pytest.fixture()
def task_dir(tmp_path: Path) -> Path:
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "t.csv").write_text("id,value\n1,10\n2,20\n", encoding="utf-8")
    (ctx / "knowledge.md").write_text("# 知识\n", encoding="utf-8")
    return tmp_path


def _builder(llm: FakeLLM, solver: FakeSolver) -> PipelineGraphBuilder:
    return PipelineGraphBuilder(
        solver=solver,
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(),
        llm=llm,
    )


def test_no_clarification_straight_through(task_dir: Path):
    llm = FakeLLM(need_clarification=False)
    solver = FakeSolver()
    out = asyncio.run(
        _builder(llm, solver).build().ainvoke(
            {"goal": AnalysisGoal(text="列出 value"), "task_dir": str(task_dir)},
            config={"configurable": {"thread_id": "s1"}},
        )
    )
    assert llm.clarify_calls == 1
    assert out["outcome"].status == "ok"
    assert out["outcome"].result.narration == "共 2 行数据。"  # narrate 生成
    assert "__interrupt__" not in out


def test_clarify_interrupt_and_resume(task_dir: Path):
    llm = FakeLLM(need_clarification=True, question="要哪一列？")
    solver = FakeSolver()
    graph = _builder(llm, solver).build()
    config = {"configurable": {"thread_id": "s2"}}

    first = asyncio.run(
        graph.ainvoke(
            {"goal": AnalysisGoal(text="查一下数据"), "task_dir": str(task_dir)},
            config=config,
        )
    )
    # 挂起：产出 clarification，未到求解
    assert "__interrupt__" in first
    intr = first["__interrupt__"][0]
    assert intr.value["clarification"].question == "要哪一列？"
    assert first.get("outcome") is None  # 未到求解（load_context 已清上一轮结果）

    # resume：用户回答 → 续跑完成
    second = asyncio.run(
        graph.ainvoke(Command(resume="value 列"), config=config)
    )
    assert "outcome" in second
    assert second["outcome"].status == "ok"
    assert "value 列" in solver.calls[-1]  # 澄清答案并入 goal
    assert "澄清问答" in second["goal"].text


def test_followup_reuses_checkpoint_skips_preprocessing(task_dir: Path):
    """追问（新调用、同 thread）：管线节点幂等跳过，只重跑 solve。"""
    llm = FakeLLM(need_clarification=False)
    solver = FakeSolver()
    graph = _builder(llm, solver).build()
    config = {"configurable": {"thread_id": "s3"}}

    asyncio.run(
        graph.ainvoke(
            {"goal": AnalysisGoal(text="列出 value"), "task_dir": str(task_dir)},
            config=config,
        )
    )
    # 追问：新分析目标，同 thread
    asyncio.run(
        graph.ainvoke(
            {"goal": AnalysisGoal(text="换个口径重算"), "task_dir": str(task_dir)},
            config=config,
        )
    )
    assert solver.calls == ["列出 value", "换个口径重算"]
    # clarify 每轮都跑（管线前的交互），narrate 每轮都跑
    assert llm.clarify_calls == 2
    assert llm.narrate_calls == 2


def test_multiturn_history_injected_into_clarify_and_solve(task_dir: Path):
    """多轮记忆：历史从 checkpoint 读回，注入 clarify 与 solver 上下文。"""
    llm = FakeLLM(need_clarification=False)
    solver = FakeSolver()
    graph = _builder(llm, solver).build()
    config = {"configurable": {"thread_id": "s-mem"}}

    asyncio.run(
        graph.ainvoke(
            {
                "goal": AnalysisGoal(text="列出 value"),
                "task_dir": str(task_dir),
                "session_id": "s-mem",
            },
            config=config,
        )
    )
    assert solver.histories[-1] == ""  # 第一轮无历史

    asyncio.run(
        graph.ainvoke(
            {
                "goal": AnalysisGoal(text="换个口径重算"),
                "task_dir": str(task_dir),
                "session_id": "s-mem",
            },
            config=config,
        )
    )
    # 第二轮：clarify 与 solver 都能看到第一轮问答
    assert "列出 value" in llm.clarify_users[-1]
    assert "列出 value" in solver.histories[-1]


def test_narrate_failure_keeps_result(task_dir: Path):
    """narrate 失败不阻塞：结果保留，叙述为空。"""

    class _BrokenNarrateLLM(FakeLLM):
        async def complete_text(self, *, system: str, user: str) -> str:
            raise RuntimeError("narrate boom")

    llm = _BrokenNarrateLLM(need_clarification=False)
    solver = FakeSolver()
    out = asyncio.run(
        _builder(llm, solver).build().ainvoke(
            {"goal": AnalysisGoal(text="列出 value"), "task_dir": str(task_dir)},
            config={"configurable": {"thread_id": "s4"}},
        )
    )
    assert out["outcome"].status == "ok"
    assert out["outcome"].result.narration == ""  # 解读失败，结果不受影响
