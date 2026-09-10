"""内置资产 导入冒烟：迁移零回归的第一道防线（M0 验收）。

导入全部会被 Adapter 复用的 内置资产 模块。不导入 zz_agent_v2 顶层
（它会读环境并构造模型客户端，属于运行时行为，由真实 task 冒烟覆盖）。
"""

import pytest

LEGACY_MODULES = [
    # tools_v2
    "data_agent.assets.tools_v2.datasource_runtime",
    "data_agent.assets.tools_v2.describe_tool",
    "data_agent.assets.tools_v2.explore_tool",
    "data_agent.assets.tools_v2.scaffold_tool",
    "data_agent.assets.tools_v2.run_solver_tool",
    "data_agent.assets.tools_v2.solver_file_tool",
    "data_agent.assets.tools_v2.schema_inference",
    "data_agent.assets.tools_v2.doc_extractor",
    "data_agent.assets.tools_v2.doc_struct_v2",
    "data_agent.assets.tools_v2.pdf_reflow",
    "data_agent.assets.tools_v2.doc_prepare",
    "data_agent.assets.tools_v2.table_profile",
    "data_agent.assets.tools_v2.naming",
    "data_agent.assets.tools_v2.timing",
    "data_agent.assets.tools_v2.general_tools",
    # agents_v2
    "data_agent.assets.agents_v2.hashline_patch",
    "data_agent.assets.agents_v2.duckdb_dialect",
    "data_agent.assets.agents_v2.tb_fold",
    "data_agent.assets.agents_v2.agent_util",
    "data_agent.assets.agents_v2.solver_agent",
    "data_agent.assets.agents_v2.doc_relevance_agent",
    "data_agent.assets.agents_v2.table_relevance_agent",
    "data_agent.assets.agents_v2.video_result_agent",
    "data_agent.assets.agents_v2.dedup_judger_agent",
    "data_agent.assets.agents_v2.plan_agent",
    "data_agent.assets.agents_v2.pre_agent",
    # doc_tools
    "data_agent.assets.doc_tools.fanout_struct",
    "data_agent.assets.doc_tools.doc_prepare",
    # video
    "data_agent.assets.video.build_video_input",
    "data_agent.assets.video.slide_coarse_det",
    "data_agent.assets.video.audio_transcribe",
    "data_agent.assets.video.frame_html_tool",
    "data_agent.assets.video.asr_init_prompt",
    # asr
    "data_agent.assets.asr.server",
    "data_agent.assets.asr.remote_whisper",
    "data_agent.assets.asr.gen_init_prompt",
    # 工具脚本（可 import，不执行 main）
    "data_agent.assets.run_compare_to_gt",
]


@pytest.mark.parametrize("module_name", LEGACY_MODULES)
def test_assets_module_imports(module_name: str):
    __import__(module_name)


# doc_agent / split_subagent 顶层 `from configs import ...` 依赖一个从未提交进仓库
# 的 configs 模块（原状如此，非迁移引入；两模块也无主流程调用方）。M3 若启用
# doc_agent 链再决定 shim 策略，内置资产 保持零改动。
@pytest.mark.parametrize(
    "module_name",
    ["agents_v2.doc_agent", "agents_v2.split_subagent"],
)
@pytest.mark.xfail(reason="内置资产 缺失未提交的 configs 模块（原仓库即如此）", strict=True)
def test_assets_module_imports_known_missing(module_name: str):
    __import__(module_name)
