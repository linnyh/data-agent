"""数据源文件名/标识符/方言归一规则（自 内置资产 tools_v2.naming 移植，行为逐字节一致）。

explore 注册的表名与 solver.py 的 DataFrame 变量名必须用同一命名规则，
否则"探查通过即执行通过"会被表名错配打破。
"""

from __future__ import annotations

import re


def stem_to_ident(stem: str) -> str:
    """文件名 stem → 合法 SQL/Python 标识符（lower）。

    保留中文/字母/数字/下划线（Unicode 语义），标点/空格替换为 ``_``，
    数字开头加 ``t_`` 前缀。
    """
    name = re.sub(r"[^\w]", "_", stem, flags=re.UNICODE)
    if name and name[0].isdigit():
        name = "t_" + name
    return name.lower()


# 单引号字符串字面量（含 '' 转义）；扫描时跳过其内容。
_SQUOTE_RE = re.compile(r"'(?:[^']|'')*'")
_BACKTICK_RE = re.compile(r"`([^`\n]*)`")
_BRACKET_RE = re.compile(r"(.?)\[([^\]\n]*)\]")
_IDENT_LIKE_RE = re.compile(r"[A-Za-z一-鿿]")


def _quote_ident(inner: str) -> str:
    return '"' + inner.replace('"', '""') + '"'


def _bracket_is_index_context(prev_char: str) -> bool:
    """方括号紧贴值/标识符结尾则为 list 索引（如 ``arr[1]``），不改写。"""
    return bool(prev_char) and (prev_char.isalnum() or prev_char in '_)]"一')


def normalize_quote_dialect(sql: str) -> tuple[str, list[tuple[str, str]]]:
    """反引号/方括号标识符 → DuckDB 双引号。单引号字符串内部不动。"""
    changes: list[tuple[str, str]] = []
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            m = _SQUOTE_RE.match(sql, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if j + 1 < n and sql[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(sql[i:j])
            i = j
            continue
        if ch == "`":
            m = _BACKTICK_RE.match(sql, i)
            if m:
                inner = m.group(1)
                repl = _quote_ident(inner)
                changes.append(("`" + inner + "`", repl))
                out.append(repl)
                i = m.end()
                continue
            out.append(ch)
            i += 1
            continue
        if ch == "[":
            end = sql.find("]", i)
            if end != -1 and "\n" not in sql[i:end]:
                inner = sql[i + 1 : end]
                prev = sql[i - 1] if i > 0 else ""
                ident_like = (
                    inner != ""
                    and ":" not in inner
                    and _IDENT_LIKE_RE.search(inner) is not None
                )
                if ident_like and not _bracket_is_index_context(prev):
                    repl = _quote_ident(inner)
                    changes.append(("[" + inner + "]", repl))
                    out.append(repl)
                    i = end + 1
                    continue
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), changes


_DQUOTED_RE = re.compile(r'"((?:[^"]|"")*)"')


def _norm_col(name: str) -> str:
    return re.sub(r"\s+", "", name).replace("　", "")


def rewrite_quoted_columns(sql: str, real_columns) -> tuple[str, list[tuple[str, str]]]:
    """双引号标识符去空格后唯一匹配真实列 → 改写成真实列名。

    原样匹配 / 0 个 / 多个候选（歧义）时一律不动。
    """
    real_set = set(real_columns)
    norm_map: dict[str, str | None] = {}
    for c in real_set:
        k = _norm_col(c)
        if k in norm_map and norm_map[k] != c:
            norm_map[k] = None
        else:
            norm_map.setdefault(k, c)

    changes: list[tuple[str, str]] = []

    def _sub(m: re.Match) -> str:
        inner = m.group(1).replace('""', '"')
        if inner in real_set:
            return m.group(0)
        target = norm_map.get(_norm_col(inner))
        if target and target != inner:
            changes.append((inner, target))
            return '"' + target.replace('"', '""') + '"'
        return m.group(0)

    return _DQUOTED_RE.sub(_sub, sql), changes
