"""M5 验收：API 集成（会话/上传/chat 全流程，本地单用户无认证）。

图与模型均用 fake 注入（不依赖真实 endpoint）；存储走真实实现。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_agent.application.graph import PipelineGraphBuilder
from data_agent.domain.models import Result, TableData
from data_agent.domain.pipeline import DocExtractResult, VideoPartsResult
from data_agent.domain.solver import SolveOutcome
from data_agent.infrastructure.api.app import create_app
from data_agent.infrastructure.db import Database
from data_agent.infrastructure.storage import SessionStorage


class FakeLLM:
    def __init__(self, need_clarification: bool = False):
        self.need_clarification = need_clarification

    async def complete_structured(self, *, system: str, user: str, schema):
        if schema.__name__ == "ClarifyDecision":
            return schema(need_clarification=self.need_clarification, question="要哪列？")
        if schema.__name__ == "NarrateDecision":
            from data_agent.domain.models import ChartSeries, ChartSpec

            return schema(
                narration="叙述：共 2 行。",
                chart=ChartSpec(
                    type="bar",
                    title="t",
                    x=["a", "b"],
                    series=[ChartSeries(name="", data=[1, 2])],
                ),
            )
        raise ValueError(f"unexpected schema: {schema.__name__}")

    async def complete_text(self, *, system: str, user: str) -> str:
        return "叙述：共 2 行。"


class FakeSolver:
    def __init__(self):
        self.last_plan = ""

    async def solve(self, *, goal, task_dir, knowledge="", history="", plan="", max_attempts=5) -> SolveOutcome:
        self.last_plan = plan
        return SolveOutcome(
            status="ok",
            result=Result(
                narration="",
                table=TableData(columns=["value"], rows=[[1], [2]], total_rows=2),
            ),
            attempts=1,
        )


class FakePlanner:
    PLAN = "## 解题规划\n- 先按月份分组聚合"

    def __init__(self):
        self.calls = 0

    async def plan(self, *, goal, task_dir, knowledge=""):
        self.calls += 1
        return self.PLAN


class FakeDedupJudger:
    ADVICE = "## 去重口径建议\n最终结果**应**按目标输出列去重。"

    def __init__(self):
        self.calls = 0

    async def judge(self, *, goal, task_dir, knowledge=""):
        self.calls += 1
        return self.ADVICE


class FakePreAgent:
    EXTRACT = "## 输出形态建议\n任务类型: aggregate; 预期输出列: month, orders"

    def __init__(self):
        self.calls = 0

    async def extract(self, *, goal, task_dir, knowledge=""):
        self.calls += 1
        return self.EXTRACT


class FakeDocExtractor:
    async def extract(self, *, task_dir, log_dir, relevant_stems=None) -> DocExtractResult:
        return DocExtractResult(db_path=task_dir / "context" / "db", tables=["a"])


class FakeVideoPreprocessor:
    def preprocess(self, *, task_dir, question="", knowledge="", log_dir=None) -> VideoPartsResult:
        return VideoPartsResult(parts=[], has_video=False)


@pytest.fixture()
def client(tmp_path: Path):
    graph = PipelineGraphBuilder(
        solver=FakeSolver(),
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(),
        llm=FakeLLM(need_clarification=False),
    ).build()

    db = Database(tmp_path / "app.db")
    db.connect()
    storage = SessionStorage(tmp_path / "sessions")
    app = create_app(db=db, storage=storage, graph=graph)
    app.state.data_dir = tmp_path
    with TestClient(app) as c:
        yield c


def _sse_events(response_text: str) -> list[dict]:
    events = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_full_chat_flow(client: TestClient):

    sid = client.post("/sessions").json()["session_id"]

    # 上传 csv
    up = client.post(
        f"/sessions/{sid}/upload",
        files={"file": ("t.csv", b"id,value\n1,10\n2,20\n", "text/csv")},
    )
    assert up.status_code == 200

    # 不支持的类型
    bad = client.post(
        f"/sessions/{sid}/upload",
        files={"file": ("x.exe", b"binary", "application/octet-stream")},
    )
    assert bad.status_code == 400

    # chat（fake 图直通求解）
    r = client.post(f"/sessions/{sid}/chat", json={"question": "列出 value"})
    assert r.status_code == 200
    events = _sse_events(r.text)
    assert events[-1]["type"] == "result"
    assert events[-1]["rows"] == [[1], [2]]
    # 图表规格随结果事件与历史下发
    assert events[-1]["chart"]["type"] == "bar"
    assert events[-1]["chart"]["x"] == ["a", "b"]
    # 执行阶段进度事件（节点级，最后一个为 narrate）
    stages = [e["stage"] for e in events if e["type"] == "progress"]
    assert stages, "应推送 progress 阶段事件"
    assert stages[-1] == "生成结果解读"

    # result 端点
    rr = client.get(f"/sessions/{sid}/result")
    assert rr.status_code == 200
    assert rr.json()["status"] == "ok"


def test_chat_clarify_interrupt_and_resume(tmp_path: Path):
    """需要澄清：chat 返回 clarification 事件 → resume 续跑出结果。"""
    graph = PipelineGraphBuilder(
        solver=FakeSolver(),
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(),
        llm=FakeLLM(need_clarification=True),
    ).build()
    db = _make_db(tmp_path / "app.db")
    storage = SessionStorage(tmp_path / "sessions")
    app = create_app(db=db, storage=storage, graph=graph)

    with TestClient(app) as c:
        sid = c.post("/sessions").json()["session_id"]

        r1 = c.post(f"/sessions/{sid}/chat", json={"question": "查一下数据"})
        events = _sse_events(r1.text)
        assert events[-1]["type"] == "clarification"
        assert events[-1]["question"] == "要哪列？"

        # resume 续跑
        r2 = c.post(
            f"/sessions/{sid}/chat",
                json={"question": "查一下数据", "resume": "value 列"},
        )
        events2 = _sse_events(r2.text)
        assert events2[-1]["type"] == "result"
        assert events2[-1]["rows"] == [[1], [2]]


def test_session_history(client: TestClient):
    """会话历史（ADR-0005）：每轮对话一条记录，checkpoint 为事实源。"""
    sid = client.post("/sessions").json()["session_id"]

    # 空历史
    r0 = client.get(f"/sessions/{sid}/history")
    assert r0.json()["history"] == []

    # 两轮对话
    for q in ["列出 value", "换个口径重算"]:
        r = client.post(f"/sessions/{sid}/chat", json={"question": q})
        assert _sse_events(r.text)[-1]["type"] == "result"

    hist = client.get(f"/sessions/{sid}/history").json()["history"]
    assert [rec["question"] for rec in hist] == ["列出 value", "换个口径重算"]
    assert hist[0]["rows"] == [[1], [2]]
    assert hist[0]["narration"] == "叙述：共 2 行。"
    assert hist[0]["chart"]["type"] == "bar"



def test_plan_node_feeds_solver(tmp_path: Path):
    """规划节点在求解前并发产出规划/去重建议/输出形态建议并注入 solver；追问轮复用不重复调用。"""
    solver = FakeSolver()
    planner = FakePlanner()
    dedup_judger = FakeDedupJudger()
    pre_agent = FakePreAgent()
    graph = PipelineGraphBuilder(
        solver=solver,
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(),
        llm=FakeLLM(need_clarification=False),
        planner=planner,
        dedup_judger=dedup_judger,
        pre_agent=pre_agent,
    ).build()
    db = _make_db(tmp_path / "app.db")
    storage = SessionStorage(tmp_path / "sessions")
    app = create_app(db=db, storage=storage, graph=graph)

    with TestClient(app) as c:
        sid = c.post("/sessions").json()["session_id"]

        r1 = c.post(f"/sessions/{sid}/chat", json={"question": "每月订单量"})
        assert _sse_events(r1.text)[-1]["type"] == "result"
        assert planner.calls == 1
        assert dedup_judger.calls == 1
        assert pre_agent.calls == 1
        assert solver.last_plan == (
            f"{FakePlanner.PLAN}\n\n{FakeDedupJudger.ADVICE}\n\n{FakePreAgent.EXTRACT}"
        )

        # 追问：同 thread 复用 checkpoint 中的规划，不再调用三个建议 agent
        r2 = c.post(f"/sessions/{sid}/chat", json={"question": "换个口径重算"})
        assert _sse_events(r2.text)[-1]["type"] == "result"
        assert planner.calls == 1
        assert dedup_judger.calls == 1
        assert pre_agent.calls == 1


def test_is_skipped_node_semantics():
    """跳过判定按节点语义：空集合≠跳过（table_relevance 全相关是有效结果）。"""
    from data_agent.infrastructure.api.app import _is_skipped

    assert _is_skipped("table_relevance", {"collapse_keys": set()}) is False
    assert _is_skipped("doc_relevance", {"relevant_stems": set()}) is True
    assert _is_skipped("video_preprocess", {"video_parts": [], "video_result": None}) is True
    assert _is_skipped("doc_extract", {"doc_extract": None}) is True
    assert _is_skipped("solve", {}) is True  # 幂等跳过


def test_summarize_limits_volume():
    """节点轨迹摘要：深度与列表截断，防大对象撑爆 SSE。"""
    from data_agent.infrastructure.api.app import _summarize

    s = _summarize({"a": {"b": {"c": "x" * 1000}}, "rows": list(range(100))})
    assert len(str(s["a"]["b"])) <= 310  # depth>=2 转字符串并截断
    assert "…共 100 项" in s["rows"][-1]


def test_session_title_from_first_question(client: TestClient):
    """会话标题 = 首条分析目标；追问轮不覆盖。"""
    sid = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{sid}/chat", json={"question": "每月订单量趋势"})
    client.post(f"/sessions/{sid}/chat", json={"question": "换个口径重算"})

    lst = client.get("/sessions").json()["sessions"]
    assert lst[0]["session_id"] == sid
    assert lst[0]["title"] == "每月订单量趋势"


def test_files_list_and_delete(tmp_path: Path):
    """文件列表端点 + 按路径删除：列表随上传/删除更新；路径穿越被拒。"""
    graph = PipelineGraphBuilder(
        solver=FakeSolver(),
        doc_extractor=FakeDocExtractor(),
        video_preprocessor=FakeVideoPreprocessor(),
        llm=FakeLLM(need_clarification=False),
    ).build()
    db = _make_db(tmp_path / "app.db")
    storage = SessionStorage(tmp_path / "sessions")
    app = create_app(db=db, storage=storage, graph=graph)

    with TestClient(app) as c:
        sid = c.post("/sessions").json()["session_id"]

        # 空列表
        empty = c.get(f"/sessions/{sid}/files")
        assert empty.status_code == 200
        assert empty.json()["files"] == []

        up = c.post(
            f"/sessions/{sid}/upload",
                files={"file": ("t.csv", b"id,value\n1,10\n", "text/csv")},
        )
        assert up.status_code == 200
        csv_path = tmp_path / "sessions" / sid / "context" / "csv" / "t.csv"
        assert csv_path.exists()

        # 列表反映上传（路径相对会话目录）
        lst = c.get(f"/sessions/{sid}/files").json()["files"]
        assert [f["filename"] for f in lst] == ["t.csv"]
        assert lst[0]["path"] == "context/csv/t.csv"

        # 按路径删除
        r = c.delete(f"/sessions/{sid}/files", params={"path": "context/csv/t.csv"})
        assert r.status_code == 200
        assert not csv_path.exists()
        assert c.get(f"/sessions/{sid}/files").json()["files"] == []

        # 路径穿越拒绝
        evil = c.delete(f"/sessions/{sid}/files", params={"path": "../t.csv"})
        assert evil.status_code == 400


def _make_db(path: Path) -> Database:
    db = Database(path)
    db.connect()
    return db


def test_settings_get_and_put(client: TestClient, tmp_path):
    # GET 返回白名单键(未配置为空串)
    settings = client.get("/settings").json()
    assert set(settings) == {
        "MODEL_API_URL", "MODEL_API_KEY", "MODEL_NAME",
        "VIDEO_MODEL_NAME", "VIDEO_MODEL_API_URL", "VIDEO_MODEL_API_KEY",
        "LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT",
    }

    # PUT 保存:运行时生效 + 落盘 .env
    r = client.put("/settings", json={"values": {"MODEL_NAME": "m-x", "LANGSMITH_TRACING": "true"}})
    assert r.status_code == 200
    assert r.json()["saved"] == ["LANGSMITH_TRACING", "MODEL_NAME"]
    assert os.environ["MODEL_NAME"] == "m-x"
    env_file = tmp_path / ".env"
    assert env_file.is_file()
    assert "MODEL_NAME=m-x" in env_file.read_text(encoding="utf-8")

    # 空值 = 删除配置(环境变量与文件行)
    r = client.put("/settings", json={"values": {"LANGSMITH_TRACING": ""}})
    assert r.status_code == 200
    assert "LANGSMITH_TRACING" not in os.environ
    assert "LANGSMITH_TRACING" not in env_file.read_text(encoding="utf-8")

    # 未知键拒绝
    r = client.put("/settings", json={"values": {"HACK": "1"}})
    assert r.status_code == 422
