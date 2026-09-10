"""相关性判定的候选构造：doc 文件扫描预览 / 注册表 schema 预览。

自 内置资产 doc/table_relevance_agent 的输入准备逻辑重写；
pdf 预览复用 内置资产 pdf_reflow（算法资产，ADR-0002）。
"""

from __future__ import annotations

from pathlib import Path

from data_agent.domain.datasource import IDataSourceRegistry
from data_agent.domain.judges import RelevanceCandidate

DOC_PREVIEW_LINES = 40
DOC_PREVIEW_LINE_CHARS = 400
TABLE_SAMPLE_ROWS = 2
TABLE_CELL_CHARS = 80


def _truncate(s, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + "…"


def build_doc_candidates(doc_dir: Path) -> list[RelevanceCandidate]:
    """扫描 doc 目录（md/txt 优先于 pdf），每 doc 一个候选（读前 40 行预览）。

    任意失败返回占位文本，绝不抛异常（判定阶段不阻塞主流程）。
    """
    if not doc_dir.is_dir():
        return []

    stem_map: dict[str, list[Path]] = {}
    for p in sorted(doc_dir.iterdir()):
        if p.name.startswith(".") or p.is_dir():
            continue
        if p.suffix.lower() not in (".md", ".markdown", ".pdf", ".txt"):
            continue
        stem_map.setdefault(p.stem, []).append(p)
    for stem in stem_map:
        stem_map[stem].sort(key=lambda p: 0 if p.suffix.lower() != ".pdf" else 1)

    out: list[RelevanceCandidate] = []
    for stem, paths in stem_map.items():
        out.append(
            RelevanceCandidate(key=stem, name=stem, preview=_read_doc_preview(paths))
        )
    return out


def _read_doc_preview(paths: list[Path]) -> str:
    for p in paths:
        try:
            if p.suffix.lower() == ".pdf":
                from data_agent.assets.tools_v2.pdf_reflow import is_text_pdf, reflow_pdf_to_text

                if not is_text_pdf(p):
                    continue
                text = reflow_pdf_to_text(p)
            else:
                text = p.read_text(encoding="utf-8")
        except Exception:
            continue

        lines = text.splitlines()
        preview = []
        for ln in lines[:DOC_PREVIEW_LINES]:
            ln = ln.rstrip()
            if len(ln) > DOC_PREVIEW_LINE_CHARS:
                ln = ln[:DOC_PREVIEW_LINE_CHARS] + " …(truncated)"
            preview.append(ln)
        body = "\n".join(preview).strip()
        suffix = (
            ""
            if len(lines) <= DOC_PREVIEW_LINES
            else f"\n…(共 {len(lines)} 行, 仅展示前 {DOC_PREVIEW_LINES} 行)"
        )
        return body + suffix if body else "(空文档)"

    return "(无法读取预览: 可能是扫描件/图片型 pdf)"


def build_table_candidates(registry: IDataSourceRegistry) -> list[RelevanceCandidate]:
    """对注册的每张表渲染 schema + 样例行预览（经只读 run_query，不触碰连接内部）。"""
    out: list[RelevanceCandidate] = []
    for t in registry.tables():
        canonical = t.canonical
        try:
            cols = registry.run_query(f'DESCRIBE "{canonical}"')
            col_pairs = list(cols.itertuples(index=False))
            col_line = ", ".join(f"{r[0]}:{r[1]}" for r in col_pairs)
            lines = [f"  columns ({len(col_pairs)}): {col_line}"]
        except Exception as e:
            lines = [f"  columns: (无法读取: {e})"]
        else:
            try:
                rows = registry.run_query(f'SELECT * FROM "{canonical}" LIMIT {TABLE_SAMPLE_ROWS}')
                names = [r[0] for r in col_pairs]
                for i, row in enumerate(rows.itertuples(index=False), 1):
                    cells = {n: _truncate(v, TABLE_CELL_CHARS) for n, v in zip(names, row)}
                    lines.append(f"  sample{i}: {cells}")
            except Exception:
                pass
        out.append(
            RelevanceCandidate(key=canonical, name=canonical, preview="\n".join(lines))
        )
    return out
