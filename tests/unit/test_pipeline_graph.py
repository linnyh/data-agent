"""M3 验收：管线图端到端（fake 依赖注入，不依赖真实 LLM/视频/ASR）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from data_agent.application.graph import PipelineGraphBuilder
from data_agent.domain.models import AnalysisGoal, Result, TableData
from data_agent.domain.pipeline import (
    DocExtractResult,
    VideoPartsResult,
    VideoResultAdvice,
)
from data_agent.domain.solver import SolveOutcome


class FakeSolver:
    async def solve(self, *, goal, task_dir, knowledge="", max_attempts=5) -> SolveOutcome:
        return SolveOutcome(
            status="ok",
            result=Result(
                narration="",
                table=TableData(columns=["value"], rows=[[1], [2]], total_rows=2),
            ),
            attempts=1,
        )


class FakeDocExtractor:
    def __init__(self):
        self.calls: list[set[str] | None] = []

    async def extract(self, *, task_dir, log_dir, relevant_stems=None) -> DocExtractResult:
        self.calls.append(relevant_stems)
        if relevant_stems == set():  # 无 doc 不应被调用
            raise AssertionError("extractor called with empty stems")
        return DocExtractResult(db_path=task_dir / "context" / "db", tables=["alpha"])


class FakeVideoPreprocessor:
    def __init__(self, has_video: bool):
        self.has_video = has_video

    def preprocess(self, *, task_dir, question="", knowledge="", log_dir=None) -> VideoPartsResult:
        return VideoPartsResult(parts=["frame-part"] if self.has_video else [], has_video=self.has_video)


class FakeVideoResultJudge:
    async def judge(self, *, task_dir, log_dir=None, vote_rounds=5) -> VideoResultAdvice:
        return VideoResultAdvice(
            decision="ADOPT", has_displayed_answer=True, confidence=0.9, has_video=True
        )


class FakeRelevanceJudge:
    """按预设 key 判无关。"""

    def __init__(self, skipped: set[str]):
        self._skipped = skipped

    async def judge(self, *, question, knowledge, candidates, log_dir=None):
        from data_agent.domain.judges import RelevanceVerdict

        all_keys = {c.key for c in candidates}
        relevant = all_keys - self._skipped
        return RelevanceVerdict(
            relevant=relevant,
            skipped=self._skipped & all_keys,
            all_candidates=all_keys,
        )


@pytest.fixture()
def task_dir(tmp_path: Path) -> Path:
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "t.csv").write_text("id,value\n1,10\n2,20\n", encoding="utf-8")
    (ctx / "knowledge.md").write_text("# 知识\n", encoding="utf-8")
    (tmp_path / "task.json").write_text('{"question": "列出 value"}', encoding="utf-8")
    return tmp_path


_thread_counter = 0


def _run(graph, task_dir: Path, goal_text: str = "列出 value"):
    global _thread_counter
    _thread_counter += 1
    return asyncio.run(
        graph.ainvoke(
            {
                "goal": AnalysisGoal(text=goal_text),
                "task_dir": str(task_dir),
            },
            config={"configurable": {"thread_id": f"t{_thread_counter}"}},
        )
    )


def test_pipeline_no_video_no_doc(task_dir: Path):
    """无视频无 doc：直通求解。"""
    builder = PipelineGraphBuilder(
        solver=FakeSolver(),
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(has_video=False),
    )
    graph = builder.build()
    out = _run(graph, task_dir)
    assert out["outcome"].status == "ok"
    assert out["video_parts"] == []
    assert out["video_result"] is None
    assert out["relevant_stems"] == set()  # 无 doc → 空集 → doc_extract 跳过


def test_pipeline_with_doc_relevance_filter(task_dir: Path):
    """有 doc：judge 判无关的 doc 不传给 extractor。"""
    (task_dir / "context" / "doc").mkdir()
    (task_dir / "context" / "doc" / "alpha.md").write_text("# a\n内容\n", encoding="utf-8")
    (task_dir / "context" / "doc" / "beta.md").write_text("# b\n内容\n", encoding="utf-8")

    extractor = FakeDocExtractor()
    builder = PipelineGraphBuilder(
        solver=FakeSolver(),
        doc_extractor=extractor,
        video_preprocessor=FakeVideoPreprocessor(has_video=False),
        doc_relevance_judge=FakeRelevanceJudge(skipped={"beta"}),
    )
    out = _run(builder.build(), task_dir)
    assert out["relevant_stems"] == {"alpha"}
    assert extractor.calls == [{"alpha"}]  # 只抽相关 doc
    assert out["doc_extract"].tables == ["alpha"]


def test_pipeline_with_video_and_advice(task_dir: Path):
    """有视频：video_result 建议进入状态。"""
    builder = PipelineGraphBuilder(
        solver=FakeSolver(),
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(has_video=True),
        video_result_judge=FakeVideoResultJudge(),
    )
    out = _run(builder.build(), task_dir)
    assert out["video_parts"] == ["frame-part"]
    assert out["video_result"].decision == "ADOPT"


def test_pipeline_table_relevance_collapse(task_dir: Path):
    """表判定：无关表的折叠 key 进状态。"""
    (task_dir / "context" / "unrelated.csv").write_text(
        "x,y\n1,2\n", encoding="utf-8"
    )
    builder = PipelineGraphBuilder(
        solver=FakeSolver(),
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(has_video=False),
        table_relevance_judge=FakeRelevanceJudge(skipped={"unrelated"}),
    )
    out = _run(builder.build(), task_dir)
    assert out["collapse_keys"] == {"context/unrelated.csv"}


def test_pipeline_judge_failure_degrades(task_dir: Path):
    """判定失败 → 降级：不过滤（relevant_stems=None 语义 → 全相关）。"""

    class _BrokenJudge:
        async def judge(self, *, question, knowledge, candidates, log_dir=None):
            raise RuntimeError("boom")

    builder = PipelineGraphBuilder(
        solver=FakeSolver(),
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(has_video=False),
        doc_relevance_judge=_BrokenJudge(),
        table_relevance_judge=_BrokenJudge(),
    )
    out = _run(builder.build(), task_dir)
    assert out["relevant_stems"] == set()  # 无 doc
    assert out["collapse_keys"] is None  # 表判定失败 → 不折叠
