"""快捷指令生成:按上传文件结构出规则模板,换一批耗尽后可选 LLM 增强。

规则池基于列类型(数值/分类/日期)组合模板;LLM 失败或未配置时回退池首循环。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from data_agent.infrastructure.storage import SessionStorage

SUGGESTIONS_PER_PAGE = 4

# 无结构化文件信息时的通用建议(PDF/MD/MP4 等)
GENERIC_POOL = [
    "总结这份资料的核心内容",
    "提取资料中的关键数据",
    "找出资料中的重点结论",
    "分析这份资料的要点并给出建议",
    "梳理资料的结构与脉络",
    "对比资料中提到的关键指标",
]


class _SuggestionList(BaseModel):
    suggestions: list[str]


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _read_csv_columns(path: Path) -> list[tuple[str, str]]:
    """read_csv_auto 推类型,返回 (列名, 类型标签)。"""
    import duckdb

    con = duckdb.connect()
    try:
        rel = con.execute("SELECT * FROM read_csv_auto(?) LIMIT 1", [str(path)])
        return [(d[0], str(d[1]).upper()) for d in rel.description]
    except Exception:
        return []
    finally:
        con.close()


def _read_json_columns(path: Path) -> list[tuple[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    obj = data[0] if isinstance(data, list) and data else data
    if not isinstance(obj, dict):
        return []
    cols = []
    for k, v in obj.items():
        if _is_number(v):
            cols.append((str(k), "NUM"))
        elif isinstance(v, str):
            cols.append((str(k), "TEXT"))
        else:
            cols.append((str(k), "OTHER"))
    return cols


def _read_sqlite_columns(path: Path) -> list[tuple[str, str]]:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        cols: list[tuple[str, str]] = []
        for t in tables[:3]:
            for r in con.execute(f'PRAGMA table_info("{t}")').fetchall():
                cols.append((f"{t}.{r[1]}", r[2].upper()))
        con.close()
        return cols
    except Exception:
        return []


def _classify(kind: str) -> str:
    if any(k in kind for k in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "REAL", "NUM", "BIGINT")):
        return "num"
    if any(k in kind for k in ("DATE", "TIME", "TIMESTAMP")):
        return "date"
    return "cat"


def build_rule_pool(uploads: list[dict], sdir: Path) -> list[str]:
    """按文件列信息生成规则候选池(可多页轮换)。"""
    pool: list[str] = []
    for item in uploads:
        rel = item.get("path", "")
        path = sdir / rel
        if not path.is_file():
            continue
        cols: list[tuple[str, str]] = []
        if rel.lower().endswith(".csv"):
            cols = _read_csv_columns(path)
        elif rel.lower().endswith(".json"):
            cols = _read_json_columns(path)
        elif rel.lower().endswith((".db", ".sqlite", ".sqlite3")):
            cols = _read_sqlite_columns(path)
        if not cols:
            continue
        num = [c for c, t in cols if _classify(t) == "num"]
        cat = [c for c, t in cols if _classify(t) == "cat"]
        date = [c for c, t in cols if _classify(t) == "date"]
        short = lambda n: n if len(n) <= 12 else n[:11] + "…"  # noqa: E731
        n = short(num[0]) if num else "数量"
        # 每文件贡献 1-3 条,聚合后按页轮换
        if num and cat:
            pool.append(f"按 {short(cat[0])} 分组对比 {n}")
            pool.append(f"各 {short(cat[0])} 的 {n} 占比")
        if num:
            pool.append(f"{n} 的汇总统计与分布")
        if date:
            pool.append(f"{n} 随时间的变化趋势")
        if num:
            pool.append(f"找出 {n} 最高的前 10 条记录")
    return list(dict.fromkeys(pool))  # 去重保序


def pick_suggestions(pool: list[str], offset: int, *, generic_fallback: bool) -> tuple[list[str], str]:
    """从池按 offset 取一页;池空时回退通用建议。

    Returns (建议列表, kind): kind ∈ rule|generic
    """
    if pool:
        return pool[offset % len(pool) : (offset % len(pool)) + SUGGESTIONS_PER_PAGE], "rule"
    if generic_fallback:
        return GENERIC_POOL[:SUGGESTIONS_PER_PAGE], "generic"
    return [], "rule"


def schema_summary(uploads: list[dict], sdir: Path) -> str:
    """给 LLM 的文件结构摘要(规则池为空时 LLM 也退化为通用资料建议)。"""
    lines = []
    for item in uploads:
        rel = item.get("path", "")
        path = sdir / rel
        if not path.is_file():
            continue
        cols = []
        if rel.lower().endswith(".csv"):
            cols = _read_csv_columns(path)
        elif rel.lower().endswith(".json"):
            cols = _read_json_columns(path)
        elif rel.lower().endswith((".db", ".sqlite", ".sqlite3")):
            cols = _read_sqlite_columns(path)
        if cols:
            col_str = ", ".join(f"{c}({t})" for c, t in cols[:20])
            lines.append(f"- {rel}: {col_str}")
        else:
            lines.append(f"- {rel}: (非结构化文件)")
    return "\n".join(lines) or "(无文件)"


async def llm_suggestions(llm, uploads: list[dict], sdir: Path) -> list[str]:
    """LLM 生成一页建议;任何失败返回空列表(调用方回退规则池)。"""
    if llm is None:
        return []
    try:
        res = await llm.complete_structured(
            system=(
                "你是数据分析助手。根据用户上传文件的结构,生成 4 条中文快捷分析指令"
                "(每条不超过 20 字,语气自然,如『销售额的时间趋势』),"
                "供用户一键填入输入框发起分析。只返回建议本身,不要编号或解释。"
            ),
            user=schema_summary(uploads, sdir),
            schema=_SuggestionList,
        )
        out = [s.strip() for s in res.suggestions if s.strip()]
        return out[:SUGGESTIONS_PER_PAGE]
    except Exception:
        return []


def session_suggestions(
    storage: SessionStorage, session_id: str, offset: int = 0
) -> dict[str, Any]:
    """同步部分:读文件 → 规则池 → 按 offset 取页。"""
    uploads = storage.list_uploads(session_id)
    sdir = storage.session_dir(session_id)
    pool = build_rule_pool(uploads, sdir)
    page, kind = pick_suggestions(pool, offset, generic_fallback=bool(uploads))
    return {
        "suggestions": page,
        "kind": kind,
        "offset": offset,
        "pool_size": len(pool),
        "has_files": bool(uploads),
    }
