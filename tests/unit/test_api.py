"""M5 验收：API 集成（注册/登录/会话/上传/chat 全流程 + 用户隔离）。

图与模型均用 fake 注入（不依赖真实 endpoint）；认证/权限/存储走真实实现。
"""

from __future__ import annotations

import json
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
    async def solve(self, *, goal, task_dir, knowledge="", history="", max_attempts=5) -> SolveOutcome:
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
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, name: str) -> dict:
    r = client.post("/auth/register", json={"username": name, "password": "secret123"})
    assert r.status_code == 200, r.text
    return r.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _sse_events(response_text: str) -> list[dict]:
    events = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_register_login_and_duplicate(client: TestClient):
    r = client.post("/auth/register", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200
    # 重复注册
    r2 = client.post("/auth/register", json={"username": "alice", "password": "secret123"})
    assert r2.status_code == 409

    # 登录：正确与错误密码
    ok = client.post("/auth/login", json={"username": "alice", "password": "secret123"})
    assert ok.status_code == 200
    bad = client.post("/auth/login", json={"username": "alice", "password": "wrongpass"})
    assert bad.status_code == 401

    # 未认证访问
    r3 = client.get("/sessions")
    assert r3.status_code == 401
    r4 = client.get("/sessions", headers=_auth_headers("badtoken"))
    assert r4.status_code == 401


def test_full_chat_flow(client: TestClient):
    alice = _register(client, "alice")
    h = _auth_headers(alice["token"])

    sid = client.post("/sessions", headers=h).json()["session_id"]

    # 上传 csv
    up = client.post(
        f"/sessions/{sid}/upload",
        headers=h,
        files={"file": ("t.csv", b"id,value\n1,10\n2,20\n", "text/csv")},
    )
    assert up.status_code == 200

    # 不支持的类型
    bad = client.post(
        f"/sessions/{sid}/upload",
        headers=h,
        files={"file": ("x.exe", b"binary", "application/octet-stream")},
    )
    assert bad.status_code == 400

    # chat（fake 图直通求解）
    r = client.post(f"/sessions/{sid}/chat", headers=h, json={"question": "列出 value"})
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
    rr = client.get(f"/sessions/{sid}/result", headers=h)
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
        alice = _register(c, "alice")
        h = _auth_headers(alice["token"])
        sid = c.post("/sessions", headers=h).json()["session_id"]

        r1 = c.post(f"/sessions/{sid}/chat", headers=h, json={"question": "查一下数据"})
        events = _sse_events(r1.text)
        assert events[-1]["type"] == "clarification"
        assert events[-1]["question"] == "要哪列？"

        # resume 续跑
        r2 = c.post(
            f"/sessions/{sid}/chat",
            headers=h,
            json={"question": "查一下数据", "resume": "value 列"},
        )
        events2 = _sse_events(r2.text)
        assert events2[-1]["type"] == "result"
        assert events2[-1]["rows"] == [[1], [2]]


def test_session_history(client: TestClient):
    """会话历史（ADR-0005）：每轮对话一条记录，checkpoint 为事实源。"""
    alice = _register(client, "alice")
    h = _auth_headers(alice["token"])
    sid = client.post("/sessions", headers=h).json()["session_id"]

    # 空历史
    r0 = client.get(f"/sessions/{sid}/history", headers=h)
    assert r0.json()["history"] == []

    # 两轮对话
    for q in ["列出 value", "换个口径重算"]:
        r = client.post(f"/sessions/{sid}/chat", headers=h, json={"question": q})
        assert _sse_events(r.text)[-1]["type"] == "result"

    hist = client.get(f"/sessions/{sid}/history", headers=h).json()["history"]
    assert [rec["question"] for rec in hist] == ["列出 value", "换个口径重算"]
    assert hist[0]["rows"] == [[1], [2]]
    assert hist[0]["narration"] == "叙述：共 2 行。"
    assert hist[0]["chart"]["type"] == "bar"

    # 隔离：B 不能读 A 的历史
    bob = _register(client, "bob")
    r403 = client.get(f"/sessions/{sid}/history", headers=_auth_headers(bob["token"]))
    assert r403.status_code == 403


def test_user_isolation(client: TestClient):
    """用户 B 不能访问用户 A 的会话（共识 #23）。"""
    alice = _register(client, "alice")
    bob = _register(client, "bob")
    ha, hb = _auth_headers(alice["token"]), _auth_headers(bob["token"])

    sid = client.post("/sessions", headers=ha).json()["session_id"]

    for method, path in [
        ("GET", f"/sessions/{sid}/result"),
        ("POST", f"/sessions/{sid}/chat"),
    ]:
        if method == "GET":
            r = client.get(path, headers=hb)
        else:
            r = client.post(path, headers=hb, json={"question": "x"})
        assert r.status_code == 403, path

    up = client.post(
        f"/sessions/{sid}/upload",
        headers=hb,
        files={"file": ("t.csv", b"a\n1\n", "text/csv")},
    )
    assert up.status_code == 403

    # 不存在的会话 → 404
    r404 = client.get("/sessions/nonexistent/result", headers=ha)
    assert r404.status_code == 404


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
        alice = _register(c, "alice")
        h = _auth_headers(alice["token"])
        sid = c.post("/sessions", headers=h).json()["session_id"]

        # 空列表
        empty = c.get(f"/sessions/{sid}/files", headers=h)
        assert empty.status_code == 200
        assert empty.json()["files"] == []

        up = c.post(
            f"/sessions/{sid}/upload",
            headers=h,
            files={"file": ("t.csv", b"id,value\n1,10\n", "text/csv")},
        )
        assert up.status_code == 200
        csv_path = tmp_path / "sessions" / sid / "context" / "csv" / "t.csv"
        assert csv_path.exists()

        # 列表反映上传（路径相对会话目录）
        lst = c.get(f"/sessions/{sid}/files", headers=h).json()["files"]
        assert [f["filename"] for f in lst] == ["t.csv"]
        assert lst[0]["path"] == "context/csv/t.csv"

        # 按路径删除
        r = c.delete(f"/sessions/{sid}/files", headers=h, params={"path": "context/csv/t.csv"})
        assert r.status_code == 200
        assert not csv_path.exists()
        assert c.get(f"/sessions/{sid}/files", headers=h).json()["files"] == []

        # 路径穿越拒绝
        evil = c.delete(f"/sessions/{sid}/files", headers=h, params={"path": "../t.csv"})
        assert evil.status_code == 400


def _make_db(path: Path) -> Database:
    db = Database(path)
    db.connect()
    return db
