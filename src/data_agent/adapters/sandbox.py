"""求解沙箱实现（ISolverSandbox）：子进程执行 + 资源限制 + 三态自检。

自 内置资产 tools_v2.run_solver_tool 重构：
- 子进程用当前 venv 解释器（sys.executable）+ 注入项目 PYTHONPATH
- 新增 resource 内存限制（ADR 决策 #20：生产多用户下防 LLM 代码吃爆内存）
- traceback 折叠 / schema 噪音折叠 / prediction.csv 自检逻辑原样迁移
"""

from __future__ import annotations

import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd


DEFAULT_TIMEOUT = 120
DEFAULT_HEAD = 5
MAX_OUTPUT_CHARS = 100_000
# 子进程虚拟内存上限（MB）。python+pandas+duckdb 导入约需 1-2GB 虚拟地址空间，
# 上限要留足余量（RLIMIT_AS 限制虚拟内存而非 RSS）；环境变量 SOLVER_MAX_MEM_MB
# 可覆盖（0 = 不限制）。macOS 不强制 RLIMIT_AS，跳过（ADR 决策 #20 的平台分支）。
DEFAULT_MAX_MEM_MB = int(os.environ.get("SOLVER_MAX_MEM_MB", "4096"))

_RESULT_NONE_MARKER = "查询尚未实现 (result is None)"
_SCHEMA_LINE_RE = re.compile(r"^\S.*\d+cols\)(?:\s*\[别名[^\]]*\])?:\s")
_TB_FOLD_OFF = {"0", "false", "no", "off", ""}


def _tb_fold_enabled() -> bool:
    return os.environ.get("DATA_AGENT_SHORT_TB", "1").strip().lower() not in _TB_FOLD_OFF


def _fold_schema_dump(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if _SCHEMA_LINE_RE.match(lines[i]):
            j = i
            while j < n and _SCHEMA_LINE_RE.match(lines[j]):
                j += 1
            block = j - i
            if block >= 2:
                out.append(
                    f"[run_solver] 已折叠 {block} 张表的 schema 打印"
                    f"(需要某表字段/样本时用 explore_data 的 \\schema <表名> 查)\n"
                )
            else:
                out.extend(lines[i:j])
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)


def _post_process_output(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    if _tb_fold_enabled():
        try:
            from data_agent.assets.agents_v2 import tb_fold  # 内置资产（traceback 折叠）

            text = tb_fold.fold_text(text)
        except Exception:
            pass
    try:
        text = _fold_schema_dump(text)
    except Exception:
        pass
    return text


def _inspect_prediction(pred_path: Path, head: int) -> str:
    if not pred_path.exists():
        return (
            "[run_solver] 警告: solver.py 跑通但未找到 workdir/prediction.csv. "
            "请确认 pred_df 已正确赋值并走到 to_csv 分支."
        )
    try:
        df = pd.read_csv(pred_path)
    except Exception as e:
        return f"[run_solver] 自检失败: 无法读取 prediction.csv: {e}"

    n_rows, n_cols = df.shape
    lines = [
        f"[run_solver] OK  prediction.csv: rows={n_rows}, cols={n_cols}",
        f"  columns ({n_cols}): {', '.join(map(str, df.columns))}",
        "  各列非空数 / 总行数:",
    ]
    for col in df.columns:
        non_null = int(df[col].notna().sum())
        lines.append(f"    - {col}: {non_null}/{n_rows}")
    h = max(0, head)
    if h:
        lines.append(f"  前 {min(h, n_rows)} 行:")
        for idx, row in df.head(h).iterrows():
            cells = {c: (None if pd.isna(v) else v) for c, v in row.items()}
            lines.append(f"    row {idx}: {cells}")
    return "\n".join(lines)


class SubprocessSolverSandbox:
    """在子进程中执行 workdir/solver.py（固定命令与路径，无 shell）。"""

    def __init__(self, *, max_mem_mb: int = DEFAULT_MAX_MEM_MB, src_roots: list[str] | None = None) -> None:
        self._max_mem_mb = max_mem_mb
        # solver.py 需要 import data_agent（新包）；src_roots 为需要加入 PYTHONPATH 的目录。
        self._src_roots = src_roots or self._default_src_roots()

    @staticmethod
    def _default_src_roots() -> list[str]:
        """项目 src/ 目录（solver.py 子进程需要 import data_agent）。"""
        repo_root = Path(__file__).resolve().parents[3]
        return [str(repo_root / "src")]

    def prediction_path(self, task_dir: Path) -> Path:
        return Path(task_dir).resolve() / "workdir" / "prediction.csv"

    def execute_solver(self, task_dir: Path, *, timeout_s: float = DEFAULT_TIMEOUT) -> str:
        task_dir = Path(task_dir).resolve()
        solver_path = task_dir / "workdir" / "solver.py"
        pred_path = self.prediction_path(task_dir)

        if not solver_path.exists():
            return (
                f"[run_solver] 错误: 未找到 {solver_path.relative_to(task_dir)}. "
                "正常情况下 scaffold 已生成该文件; 可用 read_solver 确认其内容."
            )

        if timeout_s <= 0:
            timeout_s = DEFAULT_TIMEOUT

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(self._src_roots) + os.pathsep + env.get("PYTHONPATH", "")

        preexec_fn = None
        if self._max_mem_mb > 0 and sys.platform != "darwin" and hasattr(resource, "RLIMIT_AS"):
            limit = self._max_mem_mb * 1024 * 1024

            def _pre_exec():
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

            preexec_fn = _pre_exec

        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, "./workdir/solver.py"],
                cwd=task_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                preexec_fn=preexec_fn,
            )
        except subprocess.TimeoutExpired:
            return (
                f"[run_solver] TIMEOUT: solver.py 执行超过 {timeout_s}s 被终止. "
                "请检查是否有死循环 / 笛卡尔积 join / 超大数据全量物化; "
                "先用 explore_data 验证查询规模再运行."
            )
        except Exception as e:
            return f"[run_solver] 错误: 无法启动 solver.py: {e}"
        elapsed_ms = int((time.time() - t0) * 1000)

        raw_output = (proc.stdout or "") + (proc.stderr or "")
        output = _post_process_output(raw_output)
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n... <输出截断, 原长 {len(output)}>"

        header = f"# run_solver  exit_code={proc.returncode}  elapsed_ms={elapsed_ms}"
        out_block = output.strip()
        out_section = f"\n--- solver.py 输出 ---\n{out_block}" if out_block else ""

        if proc.returncode != 0:
            return f"{header}\n[run_solver] FAILED: solver.py 非零退出, 见下方报错.{out_section}"

        if _RESULT_NONE_MARKER in raw_output:
            return (
                f"{header}\n[run_solver] NO OUTPUT: result 仍为 None, 未写出 prediction.csv. "
                f'请在 solver.py 的 "## 进行查询" 段把 result 赋为真实查询结果后再运行.'
                f"{out_section}"
            )

        inspect_block = _inspect_prediction(pred_path, DEFAULT_HEAD)
        return f"{header}\n{inspect_block}{out_section}"
