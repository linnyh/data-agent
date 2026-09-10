"""相关性判定单测：投票聚合语义 + 召回优先兜底（fake 模型，不依赖 endpoint）。"""

from __future__ import annotations

import asyncio


from data_agent.adapters.judges import DocRelevanceJudge, TableRelevanceJudge
from data_agent.adapters.judges.candidates import build_doc_candidates, build_table_candidates
from data_agent.adapters.duckdb import DuckDBDataSourceRegistry
from data_agent.domain.judges import RelevanceCandidate


class FakeLLM:
    """可编程 fake：每轮按轮序返回预设 verdicts，轮数超过预设则抛异常。"""

    def __init__(self, rounds: list[dict[str, bool]] | None = None):
        self.rounds = rounds or []
        self.calls = 0

    async def complete_structured(self, *, system: str, user: str, schema):
        if self.calls >= len(self.rounds):
            raise RuntimeError("fake model exhausted")
        verdicts = []
        for name, relevant in self.rounds[self.calls].items():
            # schema 类型区分 doc/table：字段名不同
            field = "doc_name" if "doc_name" in schema.model_fields["verdicts"].annotation.__args__[0].model_fields else "table_name"
            item = schema.model_fields["verdicts"].annotation.__args__[0](
                **{field: name, "reason": f"round{self.calls} reason", "relevant": relevant}
            )
            verdicts.append(item)
        self.calls += 1
        return schema(verdicts=verdicts)


def _cands() -> list[RelevanceCandidate]:
    return [
        RelevanceCandidate(key="a", name="a", preview="doc a preview"),
        RelevanceCandidate(key="b", name="b", preview="doc b preview"),
    ]


def test_majority_vote_relevant():
    """2:1 判相关 → 相关。"""
    llm = FakeLLM(rounds=[{"a": True, "b": False}, {"a": True, "b": False}, {"a": False, "b": True}])
    judge = DocRelevanceJudge(llm, vote_rounds=3)
    v = asyncio.run(judge.judge(question="q", knowledge="", candidates=_cands()))
    assert v.relevant == {"a"}
    assert v.skipped == {"b"}
    assert v.vote_stats["n_rounds_ok"] == 3


def test_tie_leans_relevant():
    """1:1 平票 → 偏召回判相关。"""
    llm = FakeLLM(rounds=[{"a": True, "b": False}, {"a": False, "b": True}])
    judge = TableRelevanceJudge(llm, vote_rounds=2)
    v = asyncio.run(judge.judge(question="q", knowledge="", candidates=_cands()))
    assert v.relevant == {"a", "b"}


def test_all_rounds_fail_degrade_to_all_relevant():
    """全轮失败 → 召回优先兜底：全相关（不过滤）。"""
    llm = FakeLLM(rounds=[])  # 任何调用都失败
    judge = DocRelevanceJudge(llm, vote_rounds=3)
    v = asyncio.run(judge.judge(question="q", knowledge="", candidates=_cands()))
    assert v.relevant == {"a", "b"}
    assert v.skipped == set()
    assert v.degraded


def test_unmentioned_candidate_defaults_relevant():
    """某候选所有成功轮都没判到 → 默认相关。"""
    llm = FakeLLM(rounds=[{"a": False}, {"a": False}])
    judge = DocRelevanceJudge(llm, vote_rounds=2)
    v = asyncio.run(judge.judge(question="q", knowledge="", candidates=_cands()))
    assert "b" in v.relevant
    assert v.vote_stats["tally"]["b"].startswith("0/0")


def test_partial_failure_rounds_complemented():
    """前几轮失败会被补齐：成功轮数达到 vote_rounds。"""
    llm = FakeLLM(rounds=[{"a": True, "b": True}])  # 只有 1 轮可成功
    judge = DocRelevanceJudge(llm, vote_rounds=2)
    v = asyncio.run(judge.judge(question="q", knowledge="", candidates=_cands()))
    assert v.vote_stats["n_rounds_ok"] == 1
    assert v.relevant == {"a", "b"}


def test_build_doc_candidates(tmp_path):
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir()
    (doc_dir / "alpha.md").write_text("# 标题\n" + "\n".join(f"line{i}" for i in range(60)), encoding="utf-8")
    (doc_dir / "beta.pdf").write_bytes(b"%PDF-fake")
    cands = build_doc_candidates(doc_dir)
    assert [c.key for c in cands] == ["alpha", "beta"]
    assert "共 61 行, 仅展示前 40 行" in cands[0].preview
    assert "无法读取预览" in cands[1].preview


def test_build_table_candidates(tmp_path):
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "t.csv").write_text("id,name\n1,张三\n2,李四\n", encoding="utf-8")
    reg = DuckDBDataSourceRegistry()
    reg.register_directory(tmp_path)
    cands = build_table_candidates(reg)
    assert len(cands) == 1
    assert "columns (2)" in cands[0].preview
    assert "sample1" in cands[0].preview
