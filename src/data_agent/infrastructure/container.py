"""生产装配（依赖注入根）：环境变量 → 全部 Adapter → 图。

模型配置与 内置资产 同名环境变量（MODEL_API_URL/KEY/NAME）保持兼容。
"""

from __future__ import annotations

import os

from data_agent.adapters.judges import DocRelevanceJudge, TableRelevanceJudge
from data_agent.adapters.models import OpenAICompatibleLLM
from data_agent.adapters.pipeline import (
    AssetDocExtractor,
    AssetPlanner,
    AssetVideoPreprocessor,
    AssetVideoResultJudge,
)
from data_agent.adapters.sandbox import SubprocessSolverSandbox
from data_agent.adapters.solver_agent import LangGraphSolver
from data_agent.application.graph import PipelineGraphBuilder


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Container:
    """进程级装配：模型客户端 / judges / 管线 Adapter / 求解器 / 图。"""

    def __init__(self) -> None:
        base_url = _env("MODEL_API_URL") or _env("DEFAULT_MODEL_API_URL") or "http://localhost:8000/v1"
        api_key = _env("MODEL_API_KEY") or _env("DEFAULT_MODEL_API_KEY") or "empty"
        model_name = _env("MODEL_NAME") or _env("DEFAULT_MODEL_NAME") or "qwen3.5-35b-a3b"

        # think / nothink 两实例（共识 #6）
        self.llm_nothink = OpenAICompatibleLLM(
            base_url=base_url, api_key=api_key, model_name=model_name, enable_thinking=False
        )
        self.llm_think = OpenAICompatibleLLM(
            base_url=base_url, api_key=api_key, model_name=model_name, enable_thinking=True
        )

        # 相关性判定（构造器注入 ILLM）
        self.doc_relevance_judge = DocRelevanceJudge(self.llm_nothink)
        self.table_relevance_judge = TableRelevanceJudge(self.llm_nothink)

        # 算法资产 Adapter（模型工厂注入；内置资产 链走同一 endpoint 配置）
        self.doc_extractor = AssetDocExtractor()
        self.video_preprocessor = AssetVideoPreprocessor()
        self.video_result_judge = AssetVideoResultJudge()
        self.planner = AssetPlanner()

        # 求解域
        self.sandbox = SubprocessSolverSandbox()
        self.solver = LangGraphSolver(llm=self.llm_think, sandbox=self.sandbox)

        # 图（对话式：clarify 前置 + narrate 后置）
        self.graph_builder = PipelineGraphBuilder(
            solver=self.solver,
            doc_extractor=self.doc_extractor,
            video_preprocessor=self.video_preprocessor,
            llm=self.llm_nothink,
            planner=self.planner,
            video_result_judge=self.video_result_judge,
            doc_relevance_judge=self.doc_relevance_judge,
            table_relevance_judge=self.table_relevance_judge,
        )

    def build_graph(self, *, checkpointer=None):
        return self.graph_builder.build(checkpointer=checkpointer)


def default_container() -> Container:
    return Container()
