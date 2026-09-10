"""solver.py 专用读写工具（路径写死，hashline 定位 + AST 语法体检回滚）。

自 内置资产 tools_v2.solver_file_tool 重构：
- hashline 底层复用 内置资产 依赖（pydantic_ai_backends + 纯字母 hash patch）
- 路径写死 workdir/solver.py，不接受任何其他路径
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# hashline patch 必须先于 hashline 使用加载（纯字母 hash，弱模型不把 hash 当 int）
from data_agent.assets.agents_v2 import hashline_patch  # noqa: F401
from pydantic_ai_backends import hashline

_SOLVER_REL = "workdir/solver.py"
_EDIT_SNAPSHOT_CONTEXT = 3

_HASH_MISMATCH_RE = re.compile(
    r"Hash mismatch at line (\d+): expected '([^']*)', got '([^']*)'"
)


def _rewrite_hash_mismatch(error: str) -> str:
    m = _HASH_MISMATCH_RE.search(error)
    if not m:
        return f"Error: {error}"
    line_no, given, actual = m.group(1), m.group(2), m.group(3)
    return (
        f"Error: hash 校验失败 at line {line_no}: 你给的 hash='{given}', "
        f"但该行当前真实 hash='{actual}'.\n"
        f"  常见原因: 范围编辑时 end_hash 取成了邻近行的 hash (行号→hash 取串), "
        f"文件本身未被改动.\n"
        f"  → 直接把该处 hash 改成 '{actual}' 重发本次编辑即可, 无需 read_solver 重读.\n"
        f"  (仅当你怀疑行号 {line_no} 本身取错、或文件确被改动时, 才需 read_solver 重读.)"
    )


def _render_syntax_error_window(new_text: str, err_line: int, ctx: int = 3) -> str:
    lines = new_text.split("\n")
    lo = max(1, err_line - ctx)
    hi = min(len(lines), err_line + ctx)
    width = len(str(hi))
    out = []
    for n in range(lo, hi + 1):
        mark = ">>" if n == err_line else "  "
        out.append(f"  {mark} {str(n).rjust(width)}| {lines[n - 1]}")
    return "\n".join(out)


class SolverFileTool:
    """只读写 workdir/solver.py 的窄文件工具。"""

    def __init__(self, task_dir: Path) -> None:
        self._solver_path = Path(task_dir).resolve() / _SOLVER_REL

    def read(self, offset: int = 0, limit: int = 2000) -> str:
        """读取 solver.py，行首带 ``行号:hash|`` 标记。"""
        if not self._solver_path.exists():
            return (
                "Error: 未找到 ./workdir/solver.py. 正常情况下 scaffold 已生成该文件, "
                "请确认任务初始化是否成功."
            )
        content = self._solver_path.read_text()
        return hashline.format_hashline_output(content, offset=offset, limit=limit)

    def edit(
        self,
        start_line: int,
        start_hash: str,
        new_content: str,
        end_line: int | None = None,
        end_hash: str | None = None,
        insert_after: bool = False,
    ) -> str:
        """按 行号:hash 定位编辑；写入前 AST 语法体检，失败回滚并报出错窗口。"""
        if not self._solver_path.exists():
            return "Error: 未找到 ./workdir/solver.py, 无法编辑."
        content = self._solver_path.read_text()
        new_text, error, summary = hashline.apply_hashline_edit_with_summary(
            content,
            start_line,
            start_hash,
            new_content,
            end_line=end_line,
            end_hash=end_hash,
            insert_after=insert_after,
        )
        if error is not None:
            return _rewrite_hash_mismatch(error)

        try:
            ast.parse(new_text, filename=str(self._solver_path))
        except SyntaxError as e:
            err_line = e.lineno or 0
            window = _render_syntax_error_window(new_text, err_line) if err_line else ""
            return (
                f"Error: 编辑被回滚 — solver.py 出现 SyntaxError (line {e.lineno}, "
                f"col {e.offset}): {e.msg}\n"
                f"以下是【你这次编辑若应用后】出错行附近的内容 (行号为编辑后坐标, "
                f"仅供定位; 文件并未真正改动):\n"
                f"{window}\n"
                f"提示:\n"
                f"  - 上面 `>>` 标出的就是报错行, 对照你提交的 new_content 修正后重发;\n"
                f"  - solver.py 是 Python 不是 Markdown, 多行注释每行都要以 `#` 开头;\n"
                f"  - 文件已回滚, 磁盘内容未变, 旧 行号:hash 仍然有效, 无需 re-read."
            )

        self._solver_path.write_text(new_text)

        n_new = 0 if not new_content else len(new_content.split("\n"))
        if insert_after:
            region_start = start_line + 1
            region_end = start_line + n_new
        else:
            region_start = start_line
            region_end = start_line + max(n_new, 1) - 1
        offset = max(0, region_start - 1 - _EDIT_SNAPSHOT_CONTEXT)
        limit = (region_end - region_start + 1) + 2 * _EDIT_SNAPSHOT_CONTEXT
        snapshot = hashline.format_hashline_output(new_text, offset=offset, limit=limit)
        return (
            f"Edited ./workdir/solver.py: {summary}\n"
            f"--- 编辑后该区域最新 行号:hash (可据此继续编辑, 无需 re-read) ---\n"
            f"{snapshot}"
        )
