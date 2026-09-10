"""相关性判定实现（IRelevanceJudge）。

模板方法：候选 → 并发投票补齐 → 多数票聚合（平票偏召回）→ 全失败兜底全相关。
自 内置资产 agents_v2 doc/table_relevance_agent 重构：
- 投票循环与聚合逻辑重写为模板方法（核心域逻辑）
- prompt 文本（instruction）从 内置资产 import 复用（调优文本资产，ADR-0002）
- 模型从 pydantic-ai Agent 改为构造器注入的 ILLM（依赖倒置）
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from data_agent.domain.judges import IRelevanceJudge, RelevanceCandidate, RelevanceVerdict

# 内置资产 prompt 文本资产（经长期调优，原样复用）
from data_agent.assets.agents_v2.doc_relevance_agent import DOC_RELEVANCE_INSTRUCTION
from data_agent.assets.agents_v2.table_relevance_agent import TABLE_RELEVANCE_INSTRUCTION


class DocVerdictOut(BaseModel):
    doc_name: str = Field(
        description="doc 文件的 stem (不含扩展名), 必须与输入清单中的 stem 完全一致"
    )
    reason: str = Field(
        description="先写判定依据: 解题为何确需此 doc; 或为何无关. 结论必须与 relevant 一致."
    )
    relevant: bool = Field(
        description="承接 reason 的结论: 确需则 true, 否则 false. 边界拿不准时填 true."
    )


class DocRelevanceOut(BaseModel):
    verdicts: list[DocVerdictOut] = Field(default_factory=list)


class TableVerdictOut(BaseModel):
    table_name: str = Field(
        description="表名 (canonical), 必须与输入清单中的表名完全一致"
    )
    reason: str = Field(
        description="先写判定依据: 解题为何确需此表; 或为何无关. 结论必须与 relevant 一致."
    )
    relevant: bool = Field(
        description="承接 reason 的结论: 确需则 true, 否则 false. 边界拿不准时填 true."
    )


class TableRelevanceOut(BaseModel):
    verdicts: list[TableVerdictOut] = Field(default_factory=list)


class _VotingJudge(IRelevanceJudge):
    """并发多轮投票 + 多数票聚合的判定基类（模板方法）。"""

    def __init__(
        self,
        llm,
        *,
        vote_rounds: int = 5,
        timeout_s: float = 60.0,
    ) -> None:
        self._llm = llm
        self._vote_rounds = max(1, int(vote_rounds))
        self._timeout_s = timeout_s

    # -- 子类钩子 -------------------------------------------------------------

    def _instruction(self) -> str:  # pragma: no cover - 抽象
        raise NotImplementedError

    def _verdict_schema(self) -> type[BaseModel]:  # pragma: no cover - 抽象
        raise NotImplementedError

    def _item_name(self, verdict: BaseModel) -> str:  # pragma: no cover - 抽象
        raise NotImplementedError

    def _render_candidate(self, c: RelevanceCandidate) -> str:
        return f"### {c.name}\n{c.preview}"

    def _build_user_input(
        self, *, question: str, knowledge: str, candidates: list[RelevanceCandidate]
    ) -> str:
        blocks = [self._render_candidate(c) for c in candidates]
        return (
            f"## task question\n{question}\n\n"
            f"## knowledge\n{(knowledge or '').strip()[:8000] or '(本任务无 knowledge.md)'}\n\n"
            f"## 候选清单 (共 {len(candidates)} 个)\n" + "\n\n".join(blocks)
        )

    # -- 模板方法 --------------------------------------------------------------

    async def judge(
        self,
        *,
        question: str,
        knowledge: str,
        candidates: list[RelevanceCandidate],
        log_dir: Path | None = None,
    ) -> RelevanceVerdict:
        all_keys = {c.key for c in candidates}
        user_input = self._build_user_input(
            question=question, knowledge=knowledge, candidates=candidates
        )
        instruction = self._instruction()
        schema = self._verdict_schema()

        async def _one_round() -> dict[str, BaseModel] | None:
            try:
                out = await asyncio.wait_for(
                    self._llm.complete_structured(
                        system=instruction, user=user_input, schema=schema
                    ),
                    timeout=self._timeout_s,
                )
            except Exception:
                return None
            verdicts = out.verdicts if isinstance(out, BaseModel) else []
            return {self._item_name(v): v for v in verdicts if self._item_name(v) in all_keys}

        # 循环并发补齐到 N 个成功轮（墙钟 ≈ 单轮）
        ok_rounds: list[dict[str, BaseModel]] = []
        max_batches = self._vote_rounds + 2
        for _ in range(max_batches):
            need = self._vote_rounds - len(ok_rounds)
            if need <= 0:
                break
            results = await asyncio.gather(*[_one_round() for _ in range(need)])
            ok_rounds.extend(r for r in results if r is not None)

        # 全失败 → 召回优先兜底（不过滤）
        if not ok_rounds:
            return RelevanceVerdict(
                relevant=set(all_keys),
                skipped=set(),
                all_candidates=set(all_keys),
                vote_stats={"n_rounds_ok": 0},
            )

        relevant: set[str] = set()
        skipped: set[str] = set()
        tally: dict[str, str] = {}
        for key in all_keys:
            judged = [r[key] for r in ok_rounds if key in r]
            if not judged:
                # 所有成功轮都没判到 → 偏召回默认相关
                relevant.add(key)
                tally[key] = "0/0(默认相关)"
                continue
            yes = sum(1 for v in judged if v.relevant)
            # 多数票；平票偏召回（yes*2 >= n）
            is_rel = yes * 2 >= len(judged)
            (relevant if is_rel else skipped).add(key)
            tally[key] = f"{yes}/{len(judged)}"

        return RelevanceVerdict(
            relevant=relevant,
            skipped=skipped,
            all_candidates=set(all_keys),
            vote_stats={"n_rounds_ok": len(ok_rounds), "tally": tally},
        )


class DocRelevanceJudge(_VotingJudge):
    """doc 相关性判定（判无关 → 跳过抽取，硬过滤）。"""

    def _instruction(self) -> str:
        return DOC_RELEVANCE_INSTRUCTION

    def _verdict_schema(self) -> type[BaseModel]:
        return DocRelevanceOut

    def _item_name(self, verdict: BaseModel) -> str:
        return verdict.doc_name

    def _render_candidate(self, c: RelevanceCandidate) -> str:
        return f"### doc stem: {c.key}\n{c.preview}"


class TableRelevanceJudge(_VotingJudge):
    """表相关性判定（判无关 → 折叠 describe，软过滤）。"""

    def _instruction(self) -> str:
        return TABLE_RELEVANCE_INSTRUCTION

    def _verdict_schema(self) -> type[BaseModel]:
        return TableRelevanceOut

    def _item_name(self, verdict: BaseModel) -> str:
        return verdict.table_name

    def _render_candidate(self, c: RelevanceCandidate) -> str:
        return f"### table: {c.key}\n{c.preview}"
