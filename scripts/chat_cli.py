"""终端对话 CLI：上传数据后与数据分析 Agent 多轮对话。

用法:
    export MODEL_API_URL="https://api.deepseek.com"
    export MODEL_API_KEY="sk-..."
    export MODEL_NAME="deepseek-v4-flash"

    uv run python scripts/chat_cli.py --files data.csv report.pdf briefing.mp4

进入对话后输入问题即可; Agent 需要澄清时会提问, 你回答后续跑;
输入空行或 /quit 退出。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import uuid
from pathlib import Path

# 内置资产（fanout/video 链）的裸导入需要其目录在 sys.path 上
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "内置资产" / "data_agent_baseline")
)

from data_agent.infrastructure.env import load_dotenv

# 用仓库根绝对路径读 .env（cwd 可能不在仓库根）
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from langgraph.types import Command

from data_agent.application.graph import PipelineGraphBuilder
from data_agent.domain.models import AnalysisGoal
from data_agent.infrastructure.container import Container
from data_agent.infrastructure.storage import classify_file


def _banner(session_dir: Path, files: list[Path]) -> None:
    print("=" * 60)
    print("数据分析 Agent 对话模式 (LangGraph 重构版)")
    print(f"会话目录: {session_dir}")
    if files:
        print("已上传:")
        for f in files:
            print(f"  - {f.name}")
    print("输入你的分析问题; /upload <文件...> 传文件; /quit 退出。")
    print("=" * 60)


def _save_upload(ctx: Path, f: Path) -> bool:
    """把一个文件按类型落进会话 context; 返回是否成功。"""
    category = classify_file(f.name)
    if category is None:
        print(f"  跳过不支持类型: {f.name}")
        return False
    target_dir = ctx / category
    target_dir.mkdir(parents=True, exist_ok=True)
    name = "briefing.mp4" if category == "video" else f.name
    shutil.copy2(f, target_dir / name)
    print(f"  已上传: {f.name} → context/{category}/{name}")
    return True


async def _run_turn(graph, thread_id: str, task_dir: str, question: str) -> dict:
    """跑一轮对话; 若 Agent 提问则循环回答续跑。"""
    config = {"configurable": {"thread_id": thread_id}}
    result = await graph.ainvoke(
        {"goal": AnalysisGoal(text=question), "task_dir": task_dir},
        config=config,
    )
    while "__interrupt__" in result:
        clar = result["__interrupt__"][0].value.get("clarification")
        print(f"\n🤔 Agent: {clar.question if clar else '(需要补充信息)'}")
        answer = input("你: ").strip()
        if not answer:
            answer = "(未回答, 按原目标继续)"
        result = await graph.ainvoke(Command(resume=answer), config=config)
    return result


def _print_result(result: dict) -> None:
    outcome = result.get("outcome")
    if outcome is None or outcome.result is None:
        print("\n⚠️ 未产出结果:", outcome.error if outcome else "未知错误")
        return
    r = outcome.result
    print(f"\n📊 Agent 结论:\n{r.narration or '(无叙述)'}")
    print(f"\n表格 ({r.table.total_rows} 行):")
    print("  | " + " | ".join(r.table.columns))
    for row in r.table.rows[:20]:
        print("  | " + " | ".join("" if v is None else str(v) for v in row))
    if r.table.total_rows and r.table.total_rows > 20:
        print(f"  ... (仅显示前 20 行)")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", nargs="*", default=[], help="要分析的数据文件路径")
    parser.add_argument("--question", default="", help="单轮模式: 直接问一个问题并退出")
    args = parser.parse_args()

    files = [Path(f).resolve() for f in args.files]
    missing = [f for f in files if not f.is_file()]
    if missing:
        print("文件不存在:", missing)
        return 1

    # 本地单用户会话（数据目录 ./data，与 HTTP 服务不冲突也可复用）
    data_root = Path("./data").resolve()
    session_id = uuid.uuid4().hex[:12]
    session_dir = data_root / "sessions" / session_id
    ctx = session_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (session_dir / "workdir").mkdir(exist_ok=True)
    (session_dir / "task.json").write_text('{"question": ""}', encoding="utf-8")

    for f in files:
        _save_upload(ctx, f)

    container = Container()
    graph = container.build_graph()

    # 上传新文件会开启新一轮分析上下文（thread 换代：旧判定结果不复用）
    generation = 0

    def _thread_id() -> str:
        return f"{session_id}-g{generation}"

    _banner(session_dir, files)
    if args.question:
        print(f"\n你: {args.question}")
        print("Agent 思考中...")
        result = await _run_turn(graph, _thread_id(), str(session_dir), args.question)
        _print_result(result)
        return 0

    while True:
        raw = input("\n你: ").strip()
        if not raw or raw in ("/quit", "/exit", "quit"):
            break
        if raw.startswith("/upload"):
            targets = [Path(t).expanduser() for t in raw.split()[1:]]
            ok = any(_save_upload(ctx, t.resolve()) for t in targets if t.is_file())
            if ok:
                generation += 1
                print("  (已开启新一轮分析上下文, 新文件从下一问生效)")
            continue
        print("Agent 思考中...")
        result = await _run_turn(graph, _thread_id(), str(session_dir), raw)
        _print_result(result)

    print("再见。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
