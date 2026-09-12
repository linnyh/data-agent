"""LangGraph 主求解 Agent（ISolver）：create_react_agent + 4 窄工具 + attempt 循环。

对应共识决策 #8/#9：单一大图中 solver 节点内部用 Python 显式循环表达 attempt
（与原方案同构）；ReAct 循环委托 langgraph prebuilt。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool
# ponytail: create_react_agent 在 langgraph 1.x 已弃用（2.0 移除，届时迁 langchain.agents.create_agent）；
# 现版本可用，暂不引入完整 langchain 包依赖
from langgraph.prebuilt import create_react_agent

from data_agent.adapters.duckdb import DuckDBDataSourceRegistry, FileDescriber
from data_agent.adapters.explore import ExploreTool
from data_agent.adapters.models import OpenAICompatibleLLM
from data_agent.adapters.sandbox import SubprocessSolverSandbox
from data_agent.adapters.scaffold import ScaffoldGenerator
from data_agent.adapters.solver_files import SolverFileTool
from data_agent.domain.models import AnalysisGoal, Result, TableData
from data_agent.domain.solver import SolveOutcome

# 内置资产 system prompt 文本资产（经长期调优，原样复用，ADR-0002）
from data_agent.assets.agents_v2.solver_agent import _SYSTEM_INSTRUCTION_STATIC
from data_agent.assets.agents_v2.duckdb_dialect import DUCKDB_DIALECT_NOTE

SOLVER_REQUEST_LIMIT = 80


def _generate_dir_tree(task_dir: Path) -> str:
    """生成任务目录树文本（自 内置资产 describe_tool.generate_dir_tree 移植）。"""
    lines: list[str] = ["./"]

    def _tree_lines(directory: Path, prefix: str) -> list[str]:
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return []
        result = []
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")
            if entry.is_dir():
                result.append(f"{prefix}{connector}{entry.name}/")
                result.extend(_tree_lines(entry, child_prefix))
            else:
                result.append(f"{prefix}{connector}{entry.name}")
        return result

    lines.extend(_tree_lines(task_dir, ""))
    return "\n".join(lines)


class LangGraphSolver:
    """主求解 Agent：每 task 构造一次（system prompt 注入真实目录树）。"""

    def __init__(
        self,
        *,
        llm: OpenAICompatibleLLM,
        sandbox: SubprocessSolverSandbox | None = None,
        scaffold: ScaffoldGenerator | None = None,
        request_limit: int = SOLVER_REQUEST_LIMIT,
    ) -> None:
        self._llm = llm
        self._sandbox = sandbox or SubprocessSolverSandbox()
        self._scaffold = scaffold or ScaffoldGenerator()
        self._request_limit = request_limit

    # -- 工具构造（绑定 task 状态） -------------------------------------------

    def _build_tools(self, registry: DuckDBDataSourceRegistry, file_tool: SolverFileTool, task_dir: Path):
        explore = ExploreTool(registry)

        @tool
        def explore_data(sql: str, limit: int = 50) -> str:
            """对已注册结构化数据(csv/json/sqlite)执行只读 SQL 探查。

            写 solver.py 之前用它确认 schema、看样本、验证 join 与过滤逻辑。
            支持伪命令: \\tables 列全部表; \\schema <表名> 看字段+5行样本。
            只读白名单: SELECT/WITH/PRAGMA/DESCRIBE/SHOW/EXPLAIN。
            默认返回 50 行(上限 500), 返回含"总行数/已返回行数"。
            验证通过的 SQL 写进 solver.py 的 run_sql(...) 即可执行通过。
            """
            return explore.run(sql, limit=limit)

        @tool
        def run_solver(inspect: bool = True, head: int = 5, timeout: int = 120) -> str:
            """运行 ./workdir/solver.py 并自检产出 prediction.csv。

            固定执行 python3 ./workdir/solver.py (cwd=任务根目录, 无法指定其他命令)。
            三态返回: FAILED(报错, 含折叠 traceback) / NO OUTPUT(跑通但未产出) /
            OK(附 shape/列名/各列非空数/前几行自检)。
            """
            return self._sandbox.execute_solver(task_dir, timeout_s=timeout)

        @tool
        def read_solver(offset: int = 0, limit: int = 2000) -> str:
            """读取 ./workdir/solver.py (唯一可读文件), 行首带 "行号:行hash|" 标记。

            修改前必须先读取拿到 行号:hash; 连续编辑后需重新读取(行号/hash 会变)。
            """
            return file_tool.read(offset=offset, limit=limit)

        @tool
        def edit_solver(
            start_line: int,
            start_hash: str,
            new_content: str,
            end_line: int | None = None,
            end_hash: str | None = None,
            insert_after: bool = False,
        ) -> str:
            """编辑 ./workdir/solver.py (唯一可写文件), 按 行号:hash 定位。

            start_line/start_hash 必须来自最近一次 read_solver 输出且严格对应。
            new_content 置空=删除; insert_after=True=在 start_line 后插入。
            多行注释每行都要以 # 开头 (这是 Python 不是 Markdown)。
            成功后返回编辑区域最新 行号:hash 快照, 可据此继续编辑。
            """
            return file_tool.edit(
                start_line=start_line,
                start_hash=start_hash,
                new_content=new_content,
                end_line=end_line,
                end_hash=end_hash,
                insert_after=insert_after,
            )

        return [explore_data, run_solver, read_solver, edit_solver]

    # -- 求解主流程 ------------------------------------------------------------

    async def solve(
        self,
        *,
        goal: AnalysisGoal,
        task_dir: Path,
        knowledge: str = "",
        history: str = "",
        plan: str = "",
        max_attempts: int = 5,
    ) -> SolveOutcome:
        task_dir = Path(task_dir).resolve()
        workdir = task_dir / "workdir"
        workdir.mkdir(exist_ok=True)

        # 注册数据源（explore 工具用）
        registry = DuckDBDataSourceRegistry()
        registry.register_directory(task_dir)

        # 脚手架 + 快照（attempt 恢复用）
        scaffold_path = self._scaffold.generate(task_dir)
        try:
            scaffold_pristine = scaffold_path.read_text(encoding="utf-8")
        except Exception:
            scaffold_pristine = None

        file_tool = SolverFileTool(task_dir)
        tools = self._build_tools(registry, file_tool, task_dir)

        # system prompt: 内置资产 文本 + 真实目录树 + DuckDB 方言注记
        dir_tree = _generate_dir_tree(task_dir)
        system_instruction = _SYSTEM_INSTRUCTION_STATIC.format(dir_tree=dir_tree) + DUCKDB_DIALECT_NOTE

        agent = create_react_agent(
            model=self._llm._chat,  # langchain chat model（OpenAI 兼容）
            tools=tools,
            prompt=system_instruction,
        )

        desc_str = FileDescriber().describe_context_dir(task_dir, "context", skip_knowledge=True)
        hist_block = f"\n\n{history.strip()}\n" if history.strip() else ""
        plan_block = f"## 解题规划\n{plan.strip()}\n\n" if plan.strip() else ""
        user_input = (
            "You have following context files:\n\n"
            f"{desc_str}\n\n"
            f"`context/knowledge.md` 描述任务涉及的字段和相关知识,内容如下:\n{knowledge}\n"
            f"{hist_block}"
            f"{plan_block}"
            f"通过完成 `solver.py` 完成任务:\n{goal.text}\n\n"
            "注意:`workdir/solver.py` 已经预生成了脚手架代码,包含正确的输入输出路径、"
            "所有数据文件的读取逻辑(已加载为 DataFrame)、以及最终保存到 `workdir/prediction.csv` "
            "的样板. **请直接在该文件已有的 `## 进行查询` 段补全 SQL 逻辑并替换 `pred_df`**, "
            "不要修改路径定义、读数据 block 和最后的保存行,也不要新建 solver.py.\n"
        )

        prediction_file = workdir / "prediction.csv"
        last_error = ""
        run_ok = False

        for attempt in range(1, max_attempts + 1):
            # 重试前恢复脚手架原貌 + 清 schema flag + 删残留 prediction
            if attempt > 1 and scaffold_pristine is not None:
                try:
                    scaffold_path.write_text(scaffold_pristine, encoding="utf-8")
                    (scaffold_path.parent / "_schema_shown.flag").unlink(missing_ok=True)
                except Exception:
                    pass
            prediction_file.unlink(missing_ok=True)
            run_ok = False
            try:
                await agent.ainvoke(
                    {"messages": [{"role": "user", "content": user_input}]},
                    config={"recursion_limit": 2 * self._request_limit + 5},
                )
                run_ok = True
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"

            if run_ok and prediction_file.exists():
                break

        # 兜底：全部失败时子进程直跑 scaffold（起码产出合法列名）
        if not prediction_file.exists():
            try:
                self._sandbox.execute_solver(task_dir)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"

        if not prediction_file.exists():
            return SolveOutcome(status="failed", attempts=attempt, error=last_error or "no prediction produced")

        result = self._load_result(prediction_file)
        return SolveOutcome(status="ok", result=result, attempts=attempt)

    @staticmethod
    def _load_result(prediction_file: Path) -> Result:
        import pandas as pd

        df = pd.read_csv(prediction_file)
        table = TableData(
            columns=[str(c) for c in df.columns],
            rows=df.where(pd.notna(df), None).values.tolist(),
            total_rows=len(df),
        )
        return Result(narration="", table=table)
