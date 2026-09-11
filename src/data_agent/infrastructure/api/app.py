"""FastAPI 服务：认证 / 会话 / 上传 / 对话（SSE，含 interrupt-resume）/ 结果。

create_app 是依赖注入根：graph/db/storage 由外层装配传入
（生产走 container.py；测试注入 fake）。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from data_agent.domain.models import AnalysisGoal
from data_agent.infrastructure.auth import (
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)
from data_agent.infrastructure.db import Database
from data_agent.infrastructure.storage import SessionStorage

_bearer = HTTPBearer(auto_error=False)


# -- 请求体 ------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    question: str = ""
    resume: str | None = None


# -- 依赖 --------------------------------------------------------------------


def _require_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if cred is None:
        raise HTTPException(status_code=401, detail="未认证")
    payload = decode_token(cred.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="token 无效或已过期")
    return payload.user_id


def _require_session_access(db: Database, session_id: str, user_id: str) -> None:
    """用户级隔离（共识 #23）：会话仅创建者可见。"""
    owner = db.session_owner(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if owner != user_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")


# -- 工厂 --------------------------------------------------------------------


def create_app(
    *,
    db: Database,
    storage: SessionStorage,
    graph,
) -> FastAPI:
    app = FastAPI(title="data-agent")

    # -- 认证 ----------------------------------------------------------------

    @app.post("/auth/register")
    def register(req: RegisterRequest):
        if len(req.username) < 3 or len(req.password) < 6:
            raise HTTPException(status_code=400, detail="用户名至少 3 字符, 密码至少 6 字符")
        user_id = db.create_user(req.username, hash_password(req.password))
        if user_id is None:
            raise HTTPException(status_code=409, detail="用户名已存在")
        token = issue_token(user_id, req.username)
        return {"token": token, "user_id": user_id}

    @app.post("/auth/login")
    def login(req: RegisterRequest):
        user = db.get_user_by_username(req.username)
        if user is None or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = issue_token(user["user_id"], user["username"])
        return {"token": token, "user_id": user["user_id"]}

    # -- 会话 ----------------------------------------------------------------

    @app.post("/sessions")
    def create_session(user_id: str = Depends(_require_user)):
        session_id = db.create_session(user_id)
        storage.ensure_task_layout(session_id)
        return {"session_id": session_id}

    @app.get("/sessions")
    def list_sessions(user_id: str = Depends(_require_user)):
        return {"sessions": db.list_sessions(user_id)}

    @app.post("/sessions/{session_id}/upload")
    async def upload_file(
        session_id: str,
        file: UploadFile = File(...),
        user_id: str = Depends(_require_user),
    ):
        _require_session_access(db, session_id, user_id)
        data = await file.read()
        try:
            dest = storage.save_upload(session_id, file.filename or "unnamed", data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"path": str(dest.relative_to(storage.session_dir(session_id)))}

    @app.get("/sessions/{session_id}/files")
    def list_files(session_id: str, user_id: str = Depends(_require_user)):
        _require_session_access(db, session_id, user_id)
        return {"files": storage.list_uploads(session_id)}

    @app.delete("/sessions/{session_id}/files")
    def delete_file(
        session_id: str,
        path: str,
        user_id: str = Depends(_require_user),
    ):
        _require_session_access(db, session_id, user_id)
        try:
            storage.delete_upload(session_id, path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True}

    # -- 对话 ----------------------------------------------------------------

    # 节点 → 用户可见阶段名（SSE progress 事件）
    _STAGE_LABELS = {
        "load_context": "加载上下文",
        "clarify": "理解需求",
        "video_preprocess": "视频预处理",
        "video_result": "视频内容分析",
        "doc_relevance": "筛选相关文档",
        "doc_extract": "文档结构化抽取",
        "table_relevance": "筛选数据表",
        "solve": "执行分析求解",
        "narrate": "生成结果解读",
    }

    @app.post("/sessions/{session_id}/chat")
    def chat(
        session_id: str,
        req: ChatRequest,
        user_id: str = Depends(_require_user),
    ):
        _require_session_access(db, session_id, user_id)
        sdir = storage.ensure_task_layout(session_id)
        config = {"configurable": {"thread_id": session_id}}
        goal = AnalysisGoal(text=req.question)

        async def event_stream() -> AsyncIterator[str]:
            def _ev(payload: dict) -> str:
                return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            try:
                if req.resume is not None:
                    from langgraph.types import Command

                    stream_input: object = Command(resume=req.resume)
                else:
                    stream_input = {
                        "goal": goal,
                        "task_dir": str(sdir),
                        "session_id": session_id,
                    }
                # 逐节点流式：每个节点完成推一条 progress（用户可见执行阶段）
                async for chunk in graph.astream(
                    stream_input, config=config, stream_mode="updates"
                ):
                    node = next(iter(chunk))
                    if node == "__interrupt__":
                        intr = chunk["__interrupt__"][0]
                        clar = intr.value.get("clarification")
                        yield _ev(
                            {
                                "type": "clarification",
                                "question": clar.question if clar else "",
                            }
                        )
                        return
                    yield _ev(
                        {"type": "progress", "stage": _STAGE_LABELS.get(node, node)}
                    )
                snapshot = await graph.aget_state(config)
                result = snapshot.values
                outcome = result.get("outcome")
                if outcome is not None and outcome.result is not None:
                    yield _ev(
                        {
                            "type": "result",
                            "narration": outcome.result.narration,
                            "columns": outcome.result.table.columns,
                            "rows": outcome.result.table.rows,
                            "total_rows": outcome.result.table.total_rows,
                            "attempts": outcome.attempts,
                            "chart": _chart_payload(outcome.result.chart),
                        }
                    )
                else:
                    yield _ev(
                        {
                            "type": "error",
                            "detail": outcome.error if outcome else "无结果",
                        }
                    )
            except Exception as e:
                yield _ev({"type": "error", "detail": f"{type(e).__name__}: {e}"})

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/sessions/{session_id}/result")
    async def get_result(session_id: str, user_id: str = Depends(_require_user)):
        _require_session_access(db, session_id, user_id)
        # 结果以图状态为准：拉最新 checkpoint 状态
        try:
            snapshot = await graph.aget_state(config={"configurable": {"thread_id": session_id}})
            state = snapshot.values
        except Exception:
            state = {}
        outcome = state.get("outcome")
        if outcome is None or outcome.result is None:
            return {"status": "pending"}
        return {
            "status": outcome.status,
            "narration": outcome.result.narration,
            "columns": outcome.result.table.columns,
            "rows": outcome.result.table.rows,
            "total_rows": outcome.result.table.total_rows,
            "attempts": outcome.attempts,
            "chart": _chart_payload(outcome.result.chart),
        }

    def _chart_payload(chart) -> dict | None:
        return chart.model_dump() if chart is not None else None

    def _outcome_payload(outcome) -> dict:
        return {
            "status": outcome.status,
            "narration": outcome.result.narration,
            "columns": outcome.result.table.columns,
            "rows": outcome.result.table.rows,
            "total_rows": outcome.result.table.total_rows,
            "attempts": outcome.attempts,
            "chart": _chart_payload(outcome.result.chart),
        }

    @app.get("/sessions/{session_id}/history")
    async def get_history(session_id: str, user_id: str = Depends(_require_user)):
        """会话历史（ADR-0005：checkpoint 状态为事实源，不建消息表）。

        每轮对话一条记录 {question, ...result}；narrate 前后两个快照按同 goal 归并
        取最新（含叙述的完整版）；interrupt 挂起态（outcome=None）跳过。
        """
        _require_session_access(db, session_id, user_id)
        records: list[dict] = []
        try:
            snapshots = []
            async for snap in graph.aget_state_history(
                {"configurable": {"thread_id": session_id}}
            ):
                snapshots.append(snap)
        except Exception:
            snapshots = []

        # 快照按时间正序（aget_state_history 默认最新在前）
        for snap in reversed(snapshots):
            state = snap.values
            goal = state.get("goal")
            outcome = state.get("outcome")
            if goal is None or outcome is None or outcome.result is None:
                continue
            question = goal.text
            if records and records[-1]["question"] == question:
                records[-1].update(_outcome_payload(outcome))
            else:
                records.append({"question": question, **_outcome_payload(outcome)})
        return {"history": records}

    return app
