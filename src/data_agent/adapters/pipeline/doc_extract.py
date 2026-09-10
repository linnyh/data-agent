"""doc 抽取 Adapter：fanout_struct / doc_prepare 算法资产接入（ADR-0002）。

桥接模式：构造器注入**模型工厂**（返回 内置资产 pydantic-ai 兼容模型对），
默认工厂走 内置资产 general_tools 环境变量路径；测试注入 fake 工厂。
内置资产 资产（fanout 六阶段引擎、pdf reflow、缓存）零改动。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from data_agent.domain.pipeline import DocExtractResult

# 内置资产入口
from data_agent.assets.doc_tools.doc_prepare import prepare_doc_tables_async

ModelFactory = Callable[[], tuple[Any, Any]]  # () -> (model_nothink, model_think)


def default_model_factory() -> tuple[Any, Any]:
    """默认工厂：内置资产 环境变量配置（与内置资产同口径）。"""
    from data_agent.assets.tools_v2.general_tools import build_model, build_model_nothink

    return build_model_nothink(), build_model()


class AssetDocExtractor:
    """fanout 引擎的 Adapter（构造器注入模型工厂）。"""

    def __init__(self, model_factory: ModelFactory | None = None) -> None:
        self._factory = model_factory or default_model_factory

    async def extract(
        self,
        *,
        task_dir: Path,
        log_dir: Path,
        relevant_stems: set[str] | None = None,
    ) -> DocExtractResult:
        model_nothink, model_think = self._factory()
        try:
            prep = await prepare_doc_tables_async(
                task_dir=task_dir,
                log_dir=log_dir,
                model_nothink=model_nothink,
                model_think=model_think,
                relevant_stems=relevant_stems,
            )
        except Exception as e:
            return DocExtractResult(error=f"{type(e).__name__}: {e}")

        return DocExtractResult(
            db_path=prep.db_path,
            tables=[t.name for t in prep.tables if not t.error],
        )
