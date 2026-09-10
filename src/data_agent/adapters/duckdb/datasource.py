"""DuckDB 数据源注册实现（IDataSourceRegistry）。

自 内置资产 tools_v2.datasource_runtime 移植，接口化重写：
- 模块级函数 + 状态 dataclass → 实例方法（构造器注入根目录）
- 只读白名单从 explore_tool 并入 run_query（domain 契约要求写操作必须被拒绝）
- 线程锁保留：DuckDB in-process connection 不是线程安全的
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

import duckdb
import pandas as pd

from data_agent.adapters.duckdb.naming import (
    normalize_quote_dialect,
    rewrite_quoted_columns,
    stem_to_ident,
)
from data_agent.domain.datasource import RegisteredTable

_ALLOWED_SQL_PREFIXES = ("select", "with", "pragma", "describe", "desc", "show", "explain")
_BANNED_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|attach|detach|copy|"
    r"vacuum|reindex|grant|revoke|call|export|import|load|install)\b",
    re.IGNORECASE,
)
_META_STMT_KEYWORDS = ("describe", "desc", "pragma", "show", "explain")


def _quote(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _validate_readonly(sql: str) -> tuple[bool, str]:
    """只读白名单：前缀白名单 + 写关键词黑名单。返回 (ok, reason)。"""
    s = sql.strip()
    if not s:
        return False, "empty sql"
    s_no_comment = re.sub(r"--[^\n]*", "", s)
    s_no_comment = re.sub(r"/\*.*?\*/", "", s_no_comment, flags=re.DOTALL)
    head = s_no_comment.lstrip().split(None, 1)
    if not head:
        return False, "empty sql"
    first = head[0].lower()
    if first not in _ALLOWED_SQL_PREFIXES:
        return False, (
            f"only read-only SQL is allowed (got '{first}'); "
            f"allowed prefixes: {', '.join(_ALLOWED_SQL_PREFIXES)}"
        )
    banned = _BANNED_KEYWORDS
    if banned.search(s_no_comment):
        m = banned.search(s_no_comment)
        return False, f"forbidden keyword '{m.group(0)}' in SQL"
    return True, ""


class DuckDBDataSourceRegistry:
    """把 csv/json/sqlite 注册进同一个 DuckDB in-memory 连接，提供归一化只读查询。"""

    def __init__(self) -> None:
        self._conn = duckdb.connect(":memory:")
        self._tables: list[RegisteredTable] = []
        self._df_refs: dict[str, pd.DataFrame] = {}
        self._conn_lock = threading.Lock()
        self._all_columns: set[str] | None = None

    # -- 注册 ----------------------------------------------------------------

    def register_directory(self, root: Path, context_dir: str = "context") -> None:
        ctx = root / context_dir
        if not ctx.is_dir():
            return
        csvs = sorted((ctx / "csv").glob("*.csv")) if (ctx / "csv").is_dir() else []
        csvs += sorted(ctx.glob("*.csv"))
        jsons = sorted((ctx / "json").glob("*.json")) if (ctx / "json").is_dir() else []
        jsons += sorted(ctx.glob("*.json"))
        dbs = []
        if (ctx / "db").is_dir():
            dbs += sorted((ctx / "db").glob("*.db")) + sorted((ctx / "db").glob("*.sqlite"))
        dbs += sorted(ctx.glob("*.db")) + sorted(ctx.glob("*.sqlite"))

        for p in csvs:
            self._register_csv(root, p)
        for p in jsons:
            self._register_json(root, p)
        for p in dbs:
            self._register_sqlite(root, p)

    def _register_csv(self, root: Path, p: Path) -> None:
        canonical = stem_to_ident(p.stem)
        aliases = self._build_aliases(canonical, p.stem)
        self._conn.execute(
            f"CREATE OR REPLACE VIEW {_quote(canonical)} AS "
            f"SELECT * FROM read_csv_auto({_sql_str(str(p))})"
        )
        for a in aliases:
            self._conn.execute(
                f"CREATE OR REPLACE VIEW {_quote(a)} AS SELECT * FROM {_quote(canonical)}"
            )
        self._tables.append(
            RegisteredTable(
                canonical=canonical,
                aliases=aliases,
                source_type="csv",
                source_rel=str(p.relative_to(root)),
            )
        )

    def _register_json(self, root: Path, p: Path) -> None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        if not (isinstance(data, dict) and isinstance(data.get("records"), list)):
            return
        table_name = data.get("table") or p.stem
        canonical = stem_to_ident(table_name)
        aliases = self._build_aliases(canonical, table_name, p.stem)

        df = pd.DataFrame(data["records"])
        register_name = f"_json_{canonical}"
        self._df_refs[register_name] = df
        self._conn.register(register_name, df)
        self._conn.execute(
            f"CREATE OR REPLACE VIEW {_quote(canonical)} AS SELECT * FROM {_quote(register_name)}"
        )
        for a in aliases:
            self._conn.execute(
                f"CREATE OR REPLACE VIEW {_quote(a)} AS SELECT * FROM {_quote(canonical)}"
            )
        self._tables.append(
            RegisteredTable(
                canonical=canonical,
                aliases=aliases,
                source_type="json",
                source_rel=str(p.relative_to(root)),
            )
        )

    def _register_sqlite(self, root: Path, p: Path) -> None:
        db_stem = stem_to_ident(p.stem)
        try:
            conn_ro = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            rows = conn_ro.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            sqlite_tables = [r[0] for r in rows]
            conn_ro.close()
        except Exception:
            return

        attach_alias = f"sqlite_{db_stem}"
        try:
            self._conn.execute("INSTALL sqlite")
            self._conn.execute("LOAD sqlite")
            self._conn.execute(
                f"ATTACH {_sql_str(str(p))} AS {_quote(attach_alias)} (TYPE SQLITE, READ_ONLY)"
            )
        except duckdb.Error:
            return

        for t in sqlite_tables:
            bare = stem_to_ident(t)
            used_cf = {u.casefold() for u in self._used_names()}
            if bare.casefold() in used_cf:
                canonical = f"{db_stem}__{bare}"
            else:
                canonical = bare
            aliases = self._build_aliases(canonical, t)
            qualified = f"{_quote(attach_alias)}.{_quote(t)}"
            self._conn.execute(
                f"CREATE OR REPLACE VIEW {_quote(canonical)} AS SELECT * FROM {qualified}"
            )
            for a in aliases:
                self._conn.execute(
                    f"CREATE OR REPLACE VIEW {_quote(a)} AS SELECT * FROM {_quote(canonical)}"
                )
            self._tables.append(
                RegisteredTable(
                    canonical=canonical,
                    aliases=aliases,
                    source_type="sqlite",
                    source_rel=str(p.relative_to(root)),
                    sqlite_table=t,
                )
            )

    # -- 别名 ----------------------------------------------------------------

    def _used_names(self) -> set[str]:
        used: set[str] = set()
        for t in self._tables:
            used.add(t.canonical)
            used.update(t.aliases)
        return used

    def _build_aliases(self, canonical: str, *original_names: str) -> list[str]:
        """df_<canonical> + 原名 + 原名小写；casefold 去重防 recursive bind view。"""
        canonical_cf = canonical.casefold()
        used_cf = {u.casefold() for u in self._used_names()}

        candidates: list[str] = [f"df_{canonical}"]
        for name in original_names:
            if not name:
                continue
            if name.casefold() == canonical_cf:
                continue
            candidates.append(name)
            low = name.lower()
            if low != name:
                candidates.append(low)

        out: list[str] = []
        seen_cf: set[str] = set()
        for c in candidates:
            if not c:
                continue
            c_cf = c.casefold()
            if c_cf == canonical_cf or c_cf in used_cf or c_cf in seen_cf:
                continue
            seen_cf.add(c_cf)
            out.append(c)
        return out

    # -- 查询 ----------------------------------------------------------------

    def _all_real_columns(self) -> set[str]:
        with self._conn_lock:
            if self._all_columns is None:
                cols: set[str] = set()
                for t in self._tables:
                    try:
                        info = self._conn.execute(f"DESCRIBE {_quote(t.canonical)}").fetchall()
                        cols.update(r[0] for r in info)
                    except Exception:
                        pass
                self._all_columns = cols
            return self._all_columns

    def run_query(self, sql: str) -> pd.DataFrame:
        ok, reason = _validate_readonly(sql)
        if not ok:
            raise ValueError(reason)
        s_no_comment = re.sub(r"--[^\n]*", "", sql)
        is_meta = (
            s_no_comment.lstrip().split(None, 1)[0].lower() in _META_STMT_KEYWORDS
            if s_no_comment.strip()
            else False
        )
        normalized, _ = normalize_quote_dialect(sql)
        if not is_meta:
            normalized, _ = rewrite_quoted_columns(normalized, self._all_real_columns())
        with self._conn_lock:
            return self._conn.execute(normalized).df()

    def tables(self) -> list[RegisteredTable]:
        return list(self._tables)
