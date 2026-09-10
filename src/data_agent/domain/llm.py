"""LLM 协议：核心域对模型能力的窄抽象（依赖倒置，不绑定任何 SDK）。"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class ILLM(Protocol):
    """核心域需要的模型能力最小集。

    实现（如 OpenAI 兼容 endpoint 客户端）在 adapters 层提供；
    think/nothink 语义由实现方配置决定，domain 不感知。
    """

    model_name: str

    async def complete_text(self, *, system: str, user: str) -> str:
        """非结构化补全（判定、叙述等）。"""
        ...

    async def complete_structured(
        self, *, system: str, user: str, schema: type[BaseModel]
    ) -> BaseModel:
        """结构化输出，结果受 schema 约束。"""
        ...

    def as_tools(self, tools: list[Any]) -> Any:
        """返回可供 Agent 使用的模型绑定（工具能力由调用方决定）。"""
        ...
