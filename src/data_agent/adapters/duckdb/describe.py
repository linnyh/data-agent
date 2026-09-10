"""数据源描述生成（IDataSourceDescriber）。

自 内置资产 tools_v2.describe_tool 移植，行为逐字节一致（对齐测试守护）。
纯文件级逻辑，与查询引擎无关。
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

DEFAULT_SAMPLE_ROWS = 2
MAX_VALUE_UNITS = 30
MAX_COLUMN_EXAMPLE_UNITS = 30
MAX_JSON_DICT_PREVIEW_UNITS = 30
MAX_JSON_SCALAR_UNITS = 30
MAX_MARKDOWN_LINE_UNITS = 150
DEFAULT_MARKDOWN_HEAD_LINES = 20
MAX_MARKDOWN_HEADINGS = 15
MAX_JSON_TOPLEVEL_KEYS = 20
CSV_SCAN_ROWS = 1000


def _resolve(base_path: Path, rel_path: str) -> Path:
    rel = rel_path.lstrip(os.sep).lstrip("/")
    return base_path / rel


def _truncate_line(line: str, max_units: int = MAX_MARKDOWN_LINE_UNITS) -> str:
    units: list[str] = []
    buf: list[str] = []

    def _flush():
        if buf:
            units.append("".join(buf))
            buf.clear()

    for ch in line:
        if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿":
            _flush()
            units.append(ch)
        elif ch.isspace():
            _flush()
            units.append(ch)
        else:
            buf.append(ch)
    _flush()

    kept: list[str] = []
    count = 0
    for u in units:
        if count >= max_units and not u.isspace():
            break
        kept.append(u)
        if not u.isspace():
            count += 1

    total = sum(1 for u in units if not u.isspace())
    if total <= max_units:
        return line
    omitted = total - count
    return "".join(kept).rstrip() + f" ... (omitted {omitted} words)"


def _truncate_value(v: Any, max_units: int = MAX_VALUE_UNITS) -> Any:
    if isinstance(v, str):
        return _truncate_line(v, max_units=max_units)
    return v


def _truncate_row(row: Any, max_units: int = MAX_VALUE_UNITS) -> Any:
    if isinstance(row, dict):
        return {k: _truncate_value(v, max_units) for k, v in row.items()}
    if isinstance(row, list):
        return [_truncate_value(v, max_units) for v in row]
    return _truncate_value(row, max_units)


def _infer_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _columns_from_records(records: Iterable[dict]) -> "OrderedDict[str, dict]":
    cols: "OrderedDict[str, dict]" = OrderedDict()
    total = 0
    for rec in records:
        total += 1
        if not isinstance(rec, dict):
            continue
        for k, v in rec.items():
            info = cols.setdefault(k, {"types": set(), "nulls": 0, "examples": []})
            t = _infer_type(v)
            info["types"].add(t)
            if v is None or v == "":
                info["nulls"] += 1
            elif len(info["examples"]) < 3:
                info["examples"].append(v)
    for info in cols.values():
        info["total"] = total
    return cols


def _format_columns_table(
    cols: "OrderedDict[str, dict]",
    max_units: int = MAX_COLUMN_EXAMPLE_UNITS,
) -> str:
    if not cols:
        return "  (no columns)"
    lines = []
    name_w = max(len(c) for c in cols) if cols else 4
    for name, info in cols.items():
        types = "/".join(sorted(t for t in info["types"] if t != "null"))
        if not types:
            types = "null"
        nullable = "null" in info["types"] or info["nulls"] > 0
        ex = info["examples"][0] if info["examples"] else None
        if isinstance(ex, str):
            ex_repr = repr(_truncate_line(ex, max_units=max_units))
        else:
            ex_repr = _truncate_line(repr(ex), max_units=max_units)
        lines.append(
            f"  - {name.ljust(name_w)}  type={types:<12} "
            f"nullable={'Y' if nullable else 'N'}  e.g. {ex_repr}"
        )
    return "\n".join(lines)


def _format_sample_rows(
    rows: list,
    n: int = DEFAULT_SAMPLE_ROWS,
    max_units: int = MAX_VALUE_UNITS,
) -> str:
    out = []
    for i, row in enumerate(rows[:n], 1):
        truncated = _truncate_row(row, max_units)
        out.append(f"  row {i}: {json.dumps(truncated, ensure_ascii=False, default=str)}")
    return "\n".join(out) if out else "  (no rows)"


class FileDescriber:
    """对 context 目录生成人类可读描述，支持软过滤折叠（collapse_keys）。"""

    # -- 各文件类型 -----------------------------------------------------------

    def describe_csv(
        self,
        base_path: Path,
        rel_path: str,
        sample_rows: int = DEFAULT_SAMPLE_ROWS,
        scan_rows: int = CSV_SCAN_ROWS,
    ) -> str:
        full = _resolve(base_path, rel_path)
        size = os.path.getsize(full)
        header_lines = [f"[CSV] {rel_path}", f"  size: {size:,} bytes"]

        with open(full, "r", encoding="utf-8", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect)
            fieldnames = reader.fieldnames or []
            rows: list[dict] = []
            total = 0
            for i, row in enumerate(reader):
                total += 1
                if i < scan_rows:
                    rows.append(row)

        def _infer_cell(v: str) -> str:
            if v is None or v == "":
                return "null"
            try:
                int(v)
                return "int"
            except (TypeError, ValueError):
                pass
            try:
                float(v)
                return "float"
            except (TypeError, ValueError):
                pass
            return "str"

        cols: "OrderedDict[str, dict]" = OrderedDict(
            (c, {"types": set(), "nulls": 0, "examples": []}) for c in fieldnames
        )
        for row in rows:
            for c in fieldnames:
                v = row.get(c)
                t = _infer_cell(v)
                info = cols[c]
                info["types"].add(t)
                if t == "null":
                    info["nulls"] += 1
                elif len(info["examples"]) < 3:
                    info["examples"].append(v)

        header_lines.append(
            f"  rows (scanned/total seen): {len(rows)}/{total}    columns: {len(fieldnames)}"
        )
        header_lines.append(f"  delimiter: {dialect.delimiter!r}")
        header_lines.append("  columns:")
        header_lines.append(_format_columns_table(cols))
        header_lines.append(f"  sample rows (first {sample_rows}):")
        header_lines.append(_format_sample_rows(rows, sample_rows))
        return "\n".join(header_lines)

    def describe_json(self, base_path: Path, rel_path: str, sample_rows: int = DEFAULT_SAMPLE_ROWS) -> str:
        full = _resolve(base_path, rel_path)
        with open(full, "r", encoding="utf-8") as f:
            data = json.load(f)

        size = os.path.getsize(full)
        header = [f"[JSON] {rel_path}", f"  size: {size:,} bytes"]

        if isinstance(data, dict) and isinstance(data.get("records"), list):
            table = data.get("table", os.path.splitext(os.path.basename(rel_path))[0])
            records = data["records"]
            cols = _columns_from_records(records)
            header.append(f"  shape: object with 'records' (table='{table}')")
            header.append(f"  rows: {len(records)}    columns: {len(cols)}")
            header.append("  columns:")
            header.append(_format_columns_table(cols))
            header.append(f"  sample rows (first {sample_rows}):")
            header.append(_format_sample_rows(records, sample_rows))
            return "\n".join(header)

        if isinstance(data, list) and data and isinstance(data[0], dict):
            cols = _columns_from_records(data)
            header.append("  shape: array of objects")
            header.append(f"  rows: {len(data)}    columns: {len(cols)}")
            header.append("  columns:")
            header.append(_format_columns_table(cols))
            header.append(f"  sample rows (first {sample_rows}):")
            header.append(_format_sample_rows(data, sample_rows))
            return "\n".join(header)

        if isinstance(data, list):
            header.append(f"  shape: array (len={len(data)})")
            sample = [_truncate_value(x, MAX_VALUE_UNITS) for x in data[:sample_rows]]
            header.append("  sample: " + json.dumps(sample, ensure_ascii=False, default=str))
            return "\n".join(header)

        if isinstance(data, dict):
            header.append(f"  shape: object with {len(data)} top-level keys")
            for k, v in list(data.items())[:MAX_JSON_TOPLEVEL_KEYS]:
                prev = _truncate_value(v, MAX_JSON_DICT_PREVIEW_UNITS)
                header.append(f"  {k}: {json.dumps(prev, ensure_ascii=False, default=str)}")
            if len(data) > MAX_JSON_TOPLEVEL_KEYS:
                header.append(f"  ... (+{len(data) - MAX_JSON_TOPLEVEL_KEYS} more keys)")
            return "\n".join(header)

        header.append(f"  shape: scalar ({type(data).__name__})")
        header.append(
            "  value: " + json.dumps(_truncate_value(data, MAX_JSON_SCALAR_UNITS), ensure_ascii=False, default=str)
        )
        return "\n".join(header)

    def describe_sqlite(
        self,
        base_path: Path,
        rel_path: str,
        sample_rows: int = DEFAULT_SAMPLE_ROWS,
        collapse_keys: "set[str] | None" = None,
    ) -> str:
        full = _resolve(base_path, rel_path)
        size = os.path.getsize(full)
        out = [f"[SQLite] {rel_path}", f"  size: {size:,} bytes"]
        norm_rel = _norm_key(rel_path)

        conn = sqlite3.connect(f"file:{full}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            tables = [r["name"] for r in cur.fetchall()]
            out.append(f"  tables ({len(tables)}): {', '.join(tables) or '(none)'}")
            for t in tables:
                out.append("")
                if collapse_keys and f"{norm_rel}::{t}" in collapse_keys:
                    out.append(
                        f"  ── Table: {t}  —— (table_relevance 判为无关, schema 已折叠省 token; "
                        f"如需可用 explore_data 查询)"
                    )
                    continue
                out.append(f"  ── Table: {t}")
                try:
                    cur.execute(f'SELECT COUNT(*) AS n FROM "{t}"')
                    n_rows = cur.fetchone()["n"]
                except sqlite3.Error as e:
                    n_rows = f"?? ({e})"
                out.append(f"    rows: {n_rows}")

                try:
                    cur.execute(f'SELECT * FROM "{t}" LIMIT 1')
                    ex_row = cur.fetchone()
                    col_examples: dict = dict(ex_row) if ex_row else {}
                except sqlite3.Error:
                    col_examples = {}

                cur.execute(f'PRAGMA table_info("{t}")')
                info = cur.fetchall()
                out.append(f"    columns: {len(info)}")
                name_w = max((len(c["name"]) for c in info), default=4)
                for c in info:
                    pk = " PK" if c["pk"] else ""
                    nn = " NOT NULL" if c["notnull"] else ""
                    dflt = f" DEFAULT {c['dflt_value']}" if c["dflt_value"] is not None else ""
                    ex_val = col_examples.get(c["name"])
                    if ex_val is not None:
                        eg = f"  e.g. {repr(_truncate_line(str(ex_val), max_units=MAX_COLUMN_EXAMPLE_UNITS))}"
                    else:
                        eg = ""
                    out.append(
                        f"      - {c['name'].ljust(name_w)}  "
                        f"{(c['type'] or '').ljust(8)}{pk}{nn}{dflt}{eg}"
                    )

                try:
                    cur.execute(f'SELECT * FROM "{t}" LIMIT ?', (sample_rows,))
                    rows = [dict(r) for r in cur.fetchall()]
                except sqlite3.Error as e:
                    rows = []
                    out.append(f"    sample error: {e}")
                out.append(f"    sample rows (first {sample_rows}):")
                out.append(
                    "\n".join(
                        f"      row {i}: "
                        + json.dumps(_truncate_row(r, MAX_VALUE_UNITS), ensure_ascii=False, default=str)
                        for i, r in enumerate(rows, 1)
                    )
                    or "      (no rows)"
                )
        finally:
            conn.close()
        return "\n".join(out)

    def describe_markdown(
        self,
        base_path: Path,
        rel_path: str,
        head_lines: int = DEFAULT_MARKDOWN_HEAD_LINES,
        line_max_units: int = MAX_MARKDOWN_LINE_UNITS,
    ) -> str:
        full = _resolve(base_path, rel_path)
        size = os.path.getsize(full)
        with open(full, "r", encoding="utf-8") as f:
            text = f.read()
        lines = text.splitlines()
        headings = [ln for ln in lines if ln.lstrip().startswith("#")]

        ext = os.path.splitext(rel_path)[1].lower().lstrip(".") or "txt"
        out = [
            f"[{ext.upper()}] {rel_path}",
            f"  size: {size:,} bytes    lines: {len(lines)}",
            f"  headings ({len(headings)}):",
        ]
        for h in headings[:MAX_MARKDOWN_HEADINGS]:
            out.append(f"    {h.strip()}")
        if len(headings) > MAX_MARKDOWN_HEADINGS:
            out.append(f"    ... (+{len(headings) - MAX_MARKDOWN_HEADINGS} more)")
        out.append(f"  preview (first {head_lines} lines):")
        for ln in lines[:head_lines]:
            out.append(f"    | {_truncate_line(ln, max_units=line_max_units)}")
        return "\n".join(out)

    # -- 调度与目录 -----------------------------------------------------------

    def describe_file(
        self,
        base_path: Path,
        rel_path: str,
        collapse_keys: "set[str] | None" = None,
        **kwargs,
    ) -> str:
        ext = os.path.splitext(rel_path)[1].lower()
        norm = _norm_key(rel_path)
        if ext == ".json":
            if collapse_keys and norm in collapse_keys:
                return _collapsed_line(rel_path, "JSON")
            return self.describe_json(base_path, rel_path, **kwargs)
        if ext == ".csv":
            if collapse_keys and norm in collapse_keys:
                return _collapsed_line(rel_path, "CSV")
            return self.describe_csv(base_path, rel_path, **kwargs)
        if ext in (".db", ".sqlite", ".sqlite3"):
            return self.describe_sqlite(base_path, rel_path, collapse_keys=collapse_keys, **kwargs)
        if ext in (".md", ".markdown", ".txt"):
            return self.describe_markdown(base_path, rel_path, **kwargs)
        full = _resolve(base_path, rel_path)
        size = os.path.getsize(full) if os.path.exists(full) else -1
        return f"[UNSUPPORTED {ext or 'no-ext'}] {rel_path}  size={size}"

    def describe_context_dir(
        self,
        base_path: Path,
        rel_dir: str = "context",
        *,
        skip_knowledge: bool = False,
        collapse_keys: "set[str] | None" = None,
    ) -> str:
        full_dir = _resolve(base_path, rel_dir)
        chunks = [f"# Context directory: {rel_dir}", ""]
        for root, _dirs, files in os.walk(full_dir):
            for name in sorted(files):
                if name.startswith(".") or name == ".DS_Store":
                    continue
                if skip_knowledge and name == "knowledge.md":
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, base_path)
                try:
                    chunks.append(self.describe_file(base_path, rel, collapse_keys=collapse_keys))
                except Exception as e:
                    chunks.append(f"[ERROR] {rel}: {e!r}")
                chunks.append("")
        return "\n".join(chunks)


def _norm_key(s: str) -> str:
    return s.replace(os.sep, "/")


def _collapsed_line(rel_path: str, kind: str, extra: str = "") -> str:
    return (
        f"[{kind}] {rel_path}  —— (table_relevance 判为与本任务无关, schema 已折叠以省 token; "
        f"如确需可用 explore_data 查询){(' ' + extra) if extra else ''}"
    )
