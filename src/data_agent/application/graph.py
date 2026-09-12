"""对话式数据分析图：clarify → 执行管线 → narrate。

- clarify / narrate 是管线两端的人机交互（ADR-0004）；管线内部全自动。
- 节点幂等：追问（新调用、同 thread）时复用 checkpoint 中已算好的
  视频/doc/折叠/规划结果，只重跑 clarify 与 solve。
- interrupt 需要 checkpointer（M4 用 MemorySaver，M5 换 AsyncSqliteSaver）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from data_agent.adapters.duckdb import DuckDBDataSourceRegistry, FileDescriber
from data_agent.adapters.judges.candidates import (
    build_doc_candidates,
    build_table_candidates,
)
from data_agent.application.clarify import clarify_node
from data_agent.application.narrate import narrate_node
from data_agent.application.state import PipelineState
from data_agent.domain.judges import IRelevanceJudge
from data_agent.domain.llm import ILLM
from data_agent.domain.pipeline import (
    IDedupJudger,
    IDocExtractor,
    IPlanner,
    IPreAgent,
    IVideoPreprocessor,
    IVideoResultJudge,
)
from data_agent.domain.solver import ISolver

_CLARIFY_PREVIEW_CHARS = 3000


def _collapse_keys_for(registry: DuckDBDataSourceRegistry, skipped: set[str]) -> set[str]:
    """canonical 表名 → describe 折叠 key（csv/json 用 source_rel；sqlite 表用 rel::表）。"""
    keys: set[str] = set()
    for t in registry.tables():
        if t.canonical not in skipped:
            continue
        keys.add(f"{t.source_rel}::{t.sqlite_table}" if t.sqlite_table else t.source_rel)
    return keys


class PipelineGraphBuilder:
    """构造对话式数据分析图（依赖注入点）。"""

    def __init__(
        self,
        *,
        solver: ISolver,
        doc_extractor: IDocExtractor,
        video_preprocessor: IVideoPreprocessor,
        llm: ILLM | None = None,
        planner: IPlanner | None = None,
        dedup_judger: IDedupJudger | None = None,
        pre_agent: IPreAgent | None = None,
        video_result_judge: IVideoResultJudge | None = None,
        doc_relevance_judge: IRelevanceJudge | None = None,
        table_relevance_judge: IRelevanceJudge | None = None,
        video_result_vote_rounds: int = 5,
        max_attempts: int = 5,
    ) -> None:
        self._solver = solver
        self._doc_extractor = doc_extractor
        self._video_preprocessor = video_preprocessor
        self._llm = llm
        self._planner = planner
        self._dedup_judger = dedup_judger
        self._pre_agent = pre_agent
        self._video_result_judge = video_result_judge
        self._doc_relevance_judge = doc_relevance_judge
        self._table_relevance_judge = table_relevance_judge
        self._video_result_vote_rounds = video_result_vote_rounds
        self._max_attempts = max_attempts
        self._graph = None

    # -- 节点 -----------------------------------------------------------------

    async def _load_context(self, state: PipelineState) -> dict[str, Any]:
        task_dir = Path(state["task_dir"])
        goal = state["goal"]
        knowledge = state.get("knowledge", "")
        if not knowledge:
            kp = task_dir / "context" / "knowledge.md"
            if kp.is_file():
                knowledge = kp.read_text(encoding="utf-8")
        if not goal.text:
            tj = task_dir / "task.json"
            if tj.is_file():
                goal = state["goal"].model_copy(
                    update={"text": json.loads(tj.read_text(encoding="utf-8")).get("question", "")}
                )
        # outcome 置 None：每轮新分析开始清掉上一轮结果
        # （否则 clarify 的条件边会把 checkpoint 里的旧 outcome 误判为本轮产出）
        history = ""
        if state.get("session_id"):
            history = await self._read_history(state["session_id"], goal.text)
        return {"goal": goal, "knowledge": knowledge, "history": history, "outcome": None}

    async def _read_history(self, session_id: str, current_goal: str) -> str:
        """从 checkpoint 读历史轮次（ADR-0005 事实源），构造摘要注入本轮。

        每轮一条 `问/答`；跳过进行中快照（outcome=None）与同 goal 的旧轮
        （resume 复用语义，不算历史）。
        """
        if self._graph is None:
            return ""
        try:
            snaps = []
            async for snap in self._graph.aget_state_history(
                {"configurable": {"thread_id": session_id}}
            ):
                snaps.append(snap)
        except Exception:
            return ""
        lines: list[str] = []
        for snap in reversed(snaps):  # 快照时间正序
            s = snap.values
            g, o = s.get("goal"), s.get("outcome")
            if g is None or o is None or o.result is None:
                continue
            if g.text == current_goal:
                continue
            narr = (o.result.narration or "").strip().replace("\n", " ")
            lines.append(f"- 问: {g.text.strip()[:500]}")
            if narr:
                lines.append(f"  答: {narr[:300]}")
        if not lines:
            return ""
        return "## 会话历史（此前轮次的问答，供理解指代与上下文）\n" + "\n".join(lines[-20:])

    async def _clarify(self, state: PipelineState) -> dict[str, Any]:
        if self._llm is None:
            return {}
        task_dir = Path(state["task_dir"])
        preview = FileDescriber().describe_context_dir(
            task_dir, "context", skip_knowledge=True
        )[:_CLARIFY_PREVIEW_CHARS]
        return await clarify_node(state, llm=self._llm, context_preview=preview)

    async def _video_preprocess(self, state: PipelineState) -> dict[str, Any]:
        if "video_parts" in state:  # 幂等：追问复用已算结果
            return {}
        task_dir = Path(state["task_dir"])
        try:
            res = self._video_preprocessor.preprocess(
                task_dir=task_dir,
                question=state["goal"].text,
                knowledge=state.get("knowledge", ""),
                log_dir=Path(state["task_dir"]) / ".." / "logs" / "video",
            )
        except Exception:
            return {"video_parts": [], "video_result": None}
        if not res.has_video:
            return {"video_parts": [], "video_result": None}
        return {"video_parts": res.parts}

    async def _video_result(self, state: PipelineState) -> dict[str, Any]:
        if "video_result" in state:  # 幂等
            return {}
        if not state.get("video_parts") or self._video_result_judge is None:
            return {"video_result": None}
        try:
            advice = await self._video_result_judge.judge(
                task_dir=Path(state["task_dir"]),
                vote_rounds=self._video_result_vote_rounds,
            )
        except Exception:
            return {"video_result": None}
        return {"video_result": advice if advice.has_video else None}

    async def _doc_relevance(self, state: PipelineState) -> dict[str, Any]:
        if "relevant_stems" in state:  # 幂等
            return {}
        doc_dir = Path(state["task_dir"]) / "context" / "doc"
        candidates = build_doc_candidates(doc_dir)
        if not candidates:
            return {"relevant_stems": set()}
        if self._doc_relevance_judge is None:
            return {"relevant_stems": {c.key for c in candidates}}
        try:
            verdict = await self._doc_relevance_judge.judge(
                question=state["goal"].text,
                knowledge=state.get("knowledge", ""),
                candidates=candidates,
            )
        except Exception:
            return {"relevant_stems": {c.key for c in candidates}}
        return {"relevant_stems": verdict.relevant}

    async def _doc_extract(self, state: PipelineState) -> dict[str, Any]:
        if "doc_extract" in state:  # 幂等
            return {}
        stems = state.get("relevant_stems")
        if stems is not None and not stems:
            return {"doc_extract": None}
        try:
            res = await self._doc_extractor.extract(
                task_dir=Path(state["task_dir"]),
                log_dir=Path(state["task_dir"]) / ".." / "logs" / "doc",
                relevant_stems=stems,
            )
        except Exception:
            return {"doc_extract": None}
        return {"doc_extract": res if res.db_path is not None else None}

    async def _table_relevance(self, state: PipelineState) -> dict[str, Any]:
        if "collapse_keys" in state:  # 幂等
            return {}
        if self._table_relevance_judge is None:
            return {"collapse_keys": None}
        registry = DuckDBDataSourceRegistry()
        registry.register_directory(Path(state["task_dir"]))
        candidates = build_table_candidates(registry)
        if not candidates:
            return {"collapse_keys": None}
        try:
            verdict = await self._table_relevance_judge.judge(
                question=state["goal"].text,
                knowledge=state.get("knowledge", ""),
                candidates=candidates,
            )
        except Exception:
            return {"collapse_keys": None}
        if verdict.degraded or not verdict.skipped:
            return {"collapse_keys": None}
        return {"collapse_keys": _collapse_keys_for(registry, verdict.skipped)}

    async def _plan(self, state: PipelineState) -> dict[str, Any]:
        if "plan" in state:  # 幂等：追问复用已算规划
            return {}
        # 解题规划 + 去重口径建议 + 知识精选/输出形态建议：三者互不依赖，并发执行
        goal = state["goal"]
        task_dir = Path(state["task_dir"])
        knowledge = state.get("knowledge", "")

        async def _safe(coro):
            try:
                return await coro
            except Exception:
                return ""

        tasks: list[Any] = []
        if self._planner is not None:
            tasks.append(_safe(self._planner.plan(goal=goal, task_dir=task_dir, knowledge=knowledge)))
        if self._dedup_judger is not None:
            tasks.append(
                _safe(self._dedup_judger.judge(goal=goal, task_dir=task_dir, knowledge=knowledge))
            )
        if self._pre_agent is not None:
            tasks.append(
                _safe(self._pre_agent.extract(goal=goal, task_dir=task_dir, knowledge=knowledge))
            )
        parts = await asyncio.gather(*tasks) if tasks else []
        return {"plan": "\n\n".join(p for p in parts if p.strip())}

    async def _solve(self, state: PipelineState) -> dict[str, Any]:
        outcome = await self._solver.solve(
            goal=state["goal"],
            task_dir=Path(state["task_dir"]),
            knowledge=state.get("knowledge", ""),
            history=state.get("history", ""),
            plan=state.get("plan", "") or "",
            max_attempts=self._max_attempts,
        )
        return {"outcome": outcome}

    async def _narrate(self, state: PipelineState) -> dict[str, Any]:
        if self._llm is None:
            return {}
        return await narrate_node(state, llm=self._llm)

    # -- 图 -------------------------------------------------------------------

    def build(self, *, checkpointer=None):
        g = StateGraph(PipelineState)
        g.add_node("load_context", self._load_context)
        g.add_node("clarify", self._clarify)
        g.add_node("video_preprocess", self._video_preprocess)
        g.add_node("video_result", self._video_result)
        g.add_node("doc_relevance", self._doc_relevance)
        g.add_node("doc_extract", self._doc_extract)
        g.add_node("table_relevance", self._table_relevance)
        g.add_node("plan", self._plan)
        g.add_node("solve", self._solve)
        g.add_node("narrate", self._narrate)

        g.add_edge(START, "load_context")
        g.add_edge("load_context", "clarify")
        # clarify 三态：非分析请求已产 outcome → 直接结束（跳过管线与 narrate）
        g.add_conditional_edges(
            "clarify",
            lambda s: "end" if s.get("outcome") is not None else "pipeline",
            {"pipeline": "video_preprocess", "end": END},
        )
        g.add_edge("video_preprocess", "video_result")
        g.add_edge("video_result", "doc_relevance")
        g.add_edge("doc_relevance", "doc_extract")
        g.add_edge("doc_extract", "table_relevance")
        g.add_edge("table_relevance", "plan")
        g.add_edge("plan", "solve")
        g.add_edge("solve", "narrate")
        g.add_edge("narrate", END)
        self._graph = g.compile(checkpointer=checkpointer or MemorySaver())
        return self._graph
