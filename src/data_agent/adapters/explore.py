"""只读 SQL 探查工具（绑定 DuckDBDataSourceRegistry）。

自 内置资产 tools_v2.explore_tool 重构：
- LIMIT 包装（CTE）+ 总行数提示、元数据语句直通
- 伪命令 \\tables / \\schema
- 错误压缩（去 Candidate bindings 等噪音）
"""

from __future__ import annotations

import json
import re
import time

from data_agent.adapters.duckdb import DuckDBDataSourceRegistry

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
DEFAULT_MAX_CELL_CHARS = 200
MAX_ERROR_CHARS = 600


def _format_db_error(e: BaseException, max_chars: int = MAX_ERROR_CHARS) -> str:
    msg = str(e).strip()
    msg = re.sub(r"\nLINE \d+:.*?(?:\n\s*\^+)?", "", msg, flags=re.DOTALL)
    msg = re.sub(
        r"\n\s*Candidate (?:bindings|functions):.*?(?=\n\n|\Z)",
        "",
        msg,
        flags=re.DOTALL,
    )
    msg = re.sub(r"\n\s*\n+", "\n", msg).strip()
    if len(msg) > max_chars:
        msg = msg[:max_chars] + f"... <truncated, full_len={len(str(e))}>"
    return msg


def _truncate_cell(v, max_chars: int) -> str:
    s = str(v)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"...(len={len(s)})"


def _df_to_records(df, max_cell_chars: int) -> list[dict]:
    out = []
    for row in df.itertuples(index=False):
        out.append({c: _truncate_cell(v, max_cell_chars) for c, v in zip(df.columns, row)})
    return out


class ExploreTool:
    """对已注册数据源执行只读 SQL 的探查工具。"""

    def __init__(self, registry: DuckDBDataSourceRegistry) -> None:
        self._reg = registry

    def run(self, sql: str, limit: int = DEFAULT_LIMIT, max_cell_chars: int = DEFAULT_MAX_CELL_CHARS) -> str:
        if limit <= 0:
            limit = DEFAULT_LIMIT
        if limit > MAX_LIMIT:
            limit = MAX_LIMIT
        if max_cell_chars <= 0:
            max_cell_chars = DEFAULT_MAX_CELL_CHARS

        # 伪命令
        s = sql.strip()
        if s in (r"\tables", r"\dt", "\\tables", "\\dt"):
            return self._format_tables_listing()
        m = re.match(r"^\\schema\s+(\S+)\s*$", s)
        if m:
            return self._format_schema(m.group(1))

        s_no_comment = re.sub(r"/\*.*?\*/", "", re.sub(r"--[^\n]*", "", sql), flags=re.DOTALL)
        first_kw = s_no_comment.lstrip().split(None, 1)[0].lower() if s_no_comment.strip() else ""
        is_meta_stmt = first_kw in ("describe", "desc", "pragma", "show", "explain")

        t0 = time.time()
        try:
            if is_meta_stmt:
                df = self._reg.run_query(sql)
                total = len(df)
            else:
                wrapped = f"WITH _user_q AS (\n{sql}\n) SELECT * FROM _user_q LIMIT {limit}"
                total = int(
                    self._reg.run_query(f"SELECT count(*) AS n FROM (\n{sql}\n) AS _cnt")["n"].iloc[0]
                )
                df = self._reg.run_query(wrapped)
        except ValueError as e:
            return f"[error] {e}"
        except Exception as e:
            return f"[sql error] {_format_db_error(e)}"
        elapsed_ms = int((time.time() - t0) * 1000)

        records = _df_to_records(df, max_cell_chars)
        truncated_note = ""
        if total > len(records):
            truncated_note = (
                f"  WARNING: result has {total} rows, showing first {len(records)} (limit={limit}); "
                f"add WHERE/aggregation in SQL or raise limit (max {MAX_LIMIT})."
            )

        header = [
            f"# explore_data  rows_returned={len(records)}/{total}  elapsed_ms={elapsed_ms}",
            f"  columns ({len(df.columns)}): {', '.join(map(str, df.columns))}",
        ]
        if truncated_note:
            header.append(truncated_note)
        body = [
            f"  row {i}: {json.dumps(r, ensure_ascii=False, default=str)}"
            for i, r in enumerate(records, 1)
        ]
        if not body:
            body = ["  (no rows)"]
        return "\n".join(header + body)

    def _format_tables_listing(self) -> str:
        lines = ["# explore_data  registered tables:"]
        for t in self._reg.tables():
            alias = f" [别名 df_{t.canonical}]" if f"df_{t.canonical}" in t.aliases else ""
            lines.append(
                f"  - {t.canonical} ({t.source_type}, source={t.source_rel}){alias}"
            )
        return "\n".join(lines) if len(lines) > 1 else "\n".join(lines) + "  (no tables)"

    def _format_schema(self, table: str) -> str:
        try:
            info = self._reg.run_query(f'DESCRIBE "{table}"')
        except Exception as e:
            return f"[error] 表 {table} 不存在或不可读: {_format_db_error(e)}"
        lines = [f"# schema of {table} ({len(info)} columns):"]
        for row in info.itertuples(index=False):
            lines.append(f"  - {row[0]}: {row[1]}")
        try:
            sample = self._reg.run_query(f'SELECT * FROM "{table}" LIMIT 5')
            lines.append("  sample rows:")
            for i, r in enumerate(_df_to_records(sample, DEFAULT_MAX_CELL_CHARS), 1):
                lines.append(f"    row {i}: {json.dumps(r, ensure_ascii=False, default=str)}")
        except Exception:
            pass
        return "\n".join(lines)
