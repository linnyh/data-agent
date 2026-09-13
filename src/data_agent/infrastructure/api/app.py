"""FastAPI 服务：会话 / 上传 / 对话（SSE，含 interrupt-resume）/ 结果。

本地单用户模式（无登录）：所有请求归属固定本地用户；
create_app 是依赖注入根：graph/db/storage 由外层装配传入
（生产走 container.py；测试注入 fake）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from langchain_core.messages import ToolMessage
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from data_agent.domain.models import AnalysisGoal
from data_agent.infrastructure.db import Database
from data_agent.infrastructure.storage import SessionStorage
from data_agent.infrastructure.suggestions import (
    llm_suggestions,
    session_suggestions,
)


# -- 请求体 ------------------------------------------------------------------


class ChatRequest(BaseModel):
    question: str = ""
    resume: str | None = None


class SettingsUpdate(BaseModel):
    values: dict[str, str]


# 设置页可配置的环境变量白名单（与桌面 .env 模板一致）
SETTINGS_KEYS = (
    "MODEL_API_URL",
    "MODEL_API_KEY",
    "MODEL_NAME",
    "VIDEO_MODEL_NAME",
    "VIDEO_MODEL_API_URL",
    "VIDEO_MODEL_API_KEY",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
)


# -- 依赖 --------------------------------------------------------------------


def _is_skipped(node: str, update: dict) -> bool:
    """按节点语义判定是否短路跳过。

    空值 ≠ 跳过：如 table_relevance 的 collapse_keys=set() 语义是
    "全部相关无需折叠"，是有效执行结果。
    """
    if not update:
        return True  # 幂等跳过（追问轮复用 checkpoint）
    if node == "video_preprocess":
        return not update.get("video_parts")
    if node == "video_result":
        advice = update.get("video_result")
        return advice is None or not getattr(advice, "has_video", False)
    if node == "doc_relevance":
        return not update.get("relevant_stems")
    if node == "doc_extract":
        return update.get("doc_extract") is None
    return False


def _summarize(v: Any, depth: int = 0) -> Any:
    """节点状态摘要（progress 事件的 detail 用）：控制体积，防大对象撑爆 SSE。"""
    if depth >= 2:
        s = str(v)
        return s[:300] + ("…" if len(s) > 300 else "")
    if isinstance(v, dict):
        return {str(k): _summarize(x, depth + 1) for k, x in list(v.items())[:12]}
    if isinstance(v, (list, tuple)):
        head = [_summarize(x, depth + 1) for x in v[:3]]
        if len(v) > 3:
            head.append(f"…共 {len(v)} 项")
        return head
    if isinstance(v, Path):
        return str(v)
    if hasattr(v, "model_dump"):
        return _summarize(v.model_dump(), depth + 1)
    s = str(v)
    return s[:300] + ("…" if len(s) > 300 else "")


def _require_session_access(db: Database, session_id: str, user_id: str) -> None:
    """会话归属检查：会话仅本地用户可见（保留框架，公网部署时恢复多用户）。"""
    owner = db.session_owner(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if owner != user_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")


def _persist_env(env_path: Path | None, updates: dict[str, str]) -> None:
    """把更新写回 .env：已有行改值（注释与顺序保留），空值删行，新键追加到末尾。"""
    if env_path is None:
        return
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    updated: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                if updates[key]:
                    out.append(f"{key}={updates[key]}")
                updated.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in updated and value:
            out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


# -- 工厂 --------------------------------------------------------------------


def create_app(
    *,
    db: Database,
    storage: SessionStorage,
    graph,
    llm=None,
) -> FastAPI:
    app = FastAPI(title="data-agent")

    # 本地单用户：所有请求归属固定本地用户（首次请求自动创建）
    def local_user() -> str:
        return db.ensure_local_user()

    # -- 会话 ----------------------------------------------------------------

    @app.post("/sessions")
    def create_session(user_id: str = Depends(local_user)):
        session_id = db.create_session(user_id)
        storage.ensure_task_layout(session_id)
        return {"session_id": session_id}

    @app.get("/sessions")
    async def list_sessions(user_id: str = Depends(local_user)):
        async def _title_for(sid: str) -> str:
            title = storage.first_question(sid)
            if title:
                return title
            # 旧会话回填：标题为空时从 checkpoint 读第一轮分析目标
            try:
                snaps = []
                async for snap in graph.aget_state_history(
                    {"configurable": {"thread_id": sid}}
                ):
                    snaps.append(snap)
                for snap in reversed(snaps):  # 时间正序
                    goal = snap.values.get("goal")
                    if goal is not None and (goal.text or "").strip():
                        text = goal.text.strip()
                        if "\n\n(澄清问答:" in text:
                            text = text.split("\n\n(澄清问答:")[0]
                        storage.set_first_question(sid, text)
                        return text
            except Exception:
                pass
            return ""

        sessions = [
            {"session_id": sid, "title": await _title_for(sid)}
            for sid in db.list_sessions(user_id)
        ]
        return {"sessions": sessions}

    @app.post("/sessions/{session_id}/upload")
    async def upload_file(
        session_id: str,
        file: UploadFile = File(...),
        user_id: str = Depends(local_user),
    ):
        _require_session_access(db, session_id, user_id)
        data = await file.read()
        try:
            dest = storage.save_upload(session_id, file.filename or "unnamed", data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"path": str(dest.relative_to(storage.session_dir(session_id)))}

    @app.get("/sessions/{session_id}/files")
    def list_files(session_id: str, user_id: str = Depends(local_user)):
        _require_session_access(db, session_id, user_id)
        return {"files": storage.list_uploads(session_id)}

    @app.delete("/sessions/{session_id}/files")
    def delete_file(
        session_id: str,
        path: str,
        user_id: str = Depends(local_user),
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
        "plan": "规划解题路径",
        "solve": "执行分析求解",
        "narrate": "生成结果解读",
    }

    @app.post("/sessions/{session_id}/chat")
    def chat(
        session_id: str,
        req: ChatRequest,
        user_id: str = Depends(local_user),
    ):
        _require_session_access(db, session_id, user_id)
        sdir = storage.ensure_task_layout(session_id)
        if req.question.strip() and req.resume is None:
            storage.set_first_question(session_id, req.question.strip())
        config = {"configurable": {"thread_id": session_id}}
        goal = AnalysisGoal(text=req.question)

        # 节点执行轨迹开关：progress 事件附带节点输入/输出摘要（DATA_AGENT_DEBUG_TRACE=1）
        debug_trace = os.environ.get("DATA_AGENT_DEBUG_TRACE", "0").strip().lower() not in (
            "",
            "0",
            "false",
            "no",
            "off",
        )

        async def event_stream() -> AsyncIterator[str]:
            def _ev(payload: dict) -> str:
                return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

            # 收集轨迹条目（debug 时）：流结束后落盘，历史回放可恢复
            trace_entries: list[dict] = []
            pending_tools: list[dict] = []
            completed_with_result = False

            def _push_node(stage: str, detail: dict, skipped: bool) -> None:
                entry = {"stage": stage, "detail": detail, "skipped": skipped}
                if pending_tools:
                    entry["tools"] = list(pending_tools)
                    pending_tools.clear()
                trace_entries.append(entry)

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
                # 逐节点流式：updates 推节点边界（progress），messages 捕获 solve 内部工具调用
                prev_update: dict | None = None
                # resume 续跑从 interrupt 点开始，无前置节点产出；
                # 轨迹的 input 用挂起时的 checkpoint 状态补齐
                if req.resume is not None and debug_trace:
                    try:
                        snap = await graph.aget_state(config)
                        prev_update = dict(snap.values) if snap.values else None
                    except Exception:
                        prev_update = None
                async for mode, data in graph.astream(
                    stream_input, config=config, stream_mode=["updates", "messages"]
                ):
                    if mode == "updates":
                        node = next(iter(data))
                        if node == "__interrupt__":
                            intr = data["__interrupt__"][0]
                            clar = intr.value.get("clarification")
                            yield _ev(
                                {
                                    "type": "clarification",
                                    "question": clar.question if clar else "",
                                }
                            )
                            return
                        update = data[node]
                        detail = None
                        if debug_trace:
                            output = _summarize(update)
                            # table_relevance 输出语义化:collapse_keys 是"被排除的
                            # 无关表"集合,直接展示 key 名会让人误以为是选中文件
                            if node == "table_relevance" and isinstance(update, dict):
                                collapsed = update.get("collapse_keys")
                                if collapsed is None:
                                    output = {"结论": "全部数据表保留参与分析"}
                                else:
                                    output = {
                                        "结论": "以下文件判为与本题无关,已折叠描述(不参与分析)",
                                        "无关文件": sorted(collapsed),
                                    }
                            detail = {
                                "node": node,
                                "input": _summarize(prev_update or {}),
                                "output": output,
                            }
                        prev_update = update
                        skipped = _is_skipped(node, update)
                        if debug_trace:
                            _push_node(_STAGE_LABELS.get(node, node), detail or {}, skipped)
                        yield _ev(
                            {
                                "type": "progress",
                                "stage": _STAGE_LABELS.get(node, node),
                                "detail": detail,
                                "skipped": skipped,
                            }
                        )
                    elif debug_trace:
                        msg, meta = data
                        if meta.get("langgraph_node") != "solve":
                            continue
                        if isinstance(msg, ToolMessage):
                            last = next(
                                (t for t in reversed(pending_tools)
                                 if t["tool"] == msg.name and "content" not in t),
                                None,
                            )
                            target = last or {}
                            target["tool"] = msg.name
                            target["content"] = _summarize(msg.content)
                            if last is None:
                                pending_tools.append(target)
                            yield _ev(
                                {
                                    "type": "tool",
                                    "node": "solve",
                                    "detail": {
                                        "tool": msg.name,
                                        "kind": "result",
                                        "content": target["content"],
                                    },
                                }
                            )
                        elif getattr(msg, "tool_calls", None):
                            call = msg.tool_calls[0]
                            pending_tools.append(
                                {
                                    "tool": call.get("name", ""),
                                    "args": _summarize(call.get("args", {})),
                                }
                            )
                            yield _ev(
                                {
                                    "type": "tool",
                                    "node": "solve",
                                    "detail": {
                                        "tool": call.get("name", ""),
                                        "kind": "call",
                                        "args": _summarize(call.get("args", {})),
                                    },
                                }
                            )
                snapshot = await graph.aget_state(config)
                result = snapshot.values
                outcome = result.get("outcome")
                if outcome is not None and outcome.result is not None:
                    # 完整产出结果的独立轮次才落盘轨迹（中断/失败/澄清续跑轮不写，
                    # 避免与 history 记录按序错位）
                    completed_with_result = req.resume is None
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
            finally:
                # 轨迹落盘：仅完整产出结果的轮次写（中断 abort 时部分轨迹不落盘）
                if debug_trace and completed_with_result and trace_entries:
                    try:
                        storage.save_trace(session_id, trace_entries)
                    except Exception:
                        pass

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/sessions/{session_id}/result")
    async def get_result(session_id: str, user_id: str = Depends(local_user)):
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
    async def get_history(session_id: str, user_id: str = Depends(local_user)):
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
        # 执行轨迹按轮次顺序附加（每轮一条，可能少于轮次数）
        traces = storage.list_traces(session_id)
        for i, rec in enumerate(records):
            rec["trace"] = traces[i] if i < len(traces) else None
        return {"history": records}

    # -- 快捷指令（按上传文件结构生成） ----------------------------------------

    @app.get("/sessions/{session_id}/suggestions")
    async def get_suggestions(
        session_id: str,
        offset: int = 0,
        user_id: str = Depends(local_user),
    ):
        _require_session_access(db, session_id, user_id)
        base = session_suggestions(storage, session_id, max(offset, 0))
        # 规则池耗尽(offset 越界)且有模型 → LLM 增强;失败回退池首循环
        if base["pool_size"] > 0 and base["offset"] >= base["pool_size"]:
            sdir = storage.session_dir(session_id)
            page = await llm_suggestions(llm, storage.list_uploads(session_id), sdir)
            if page:
                return {**base, "suggestions": page, "kind": "llm"}
        return base

    # -- 设置（桌面 App .env 配置页） ------------------------------------------

    @app.get("/settings")
    def get_settings():
        return {k: os.environ.get(k, "") for k in SETTINGS_KEYS}

    @app.put("/settings")
    def update_settings(req: SettingsUpdate):
        unknown = set(req.values) - set(SETTINGS_KEYS)
        if unknown:
            raise HTTPException(status_code=422, detail=f"未知配置项: {', '.join(sorted(unknown))}")
        updates = {k: v.strip() for k, v in req.values.items()}
        # 运行时立即生效（模型工厂每次请求读 os.environ），同时落盘 .env 持久化；
        # 空值 = 删除该配置
        for k, v in updates.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        data_dir = getattr(app.state, "data_dir", None)
        _persist_env(Path(data_dir) / ".env" if data_dir else None, updates)
        return {"ok": True, "saved": sorted(updates)}

    return app
