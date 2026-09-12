"""OpenAI 兼容 endpoint 的 LLM 实现（ILLM 协议）。"""

from __future__ import annotations

from pydantic import BaseModel
from langchain_openai import ChatOpenAI



class OpenAICompatibleLLM:
    """自托管 OpenAI 兼容协议模型（DeepSeek）。

    think/nothink 语义通过构造参数表达：``enable_thinking`` 非 None 时以
    DeepSeek 风格 ``thinking: {"type": enabled|disabled}`` 注入 extra_body；
    其他厂商需换对应参数格式。
    """

    model_name: str

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        enable_thinking: bool | None = None,
        temperature: float = 0.0,
        timeout_s: float = 120.0,
    ) -> None:
        kwargs: dict = dict(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout_s,
            max_retries=1,
        )
        if enable_thinking is not None:
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled" if enable_thinking else "disabled"}
            }
        self.model_name = model_name
        self._chat = ChatOpenAI(**kwargs)

    async def complete_text(self, *, system: str, user: str) -> str:
        resp = await self._chat.ainvoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        return str(resp.content)

    async def complete_structured(
        self, *, system: str, user: str, schema: type[BaseModel]
    ) -> BaseModel:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # method="function_calling": DeepSeek 等端点不支持 response_format=json_schema
        # （报 "response_format type is unavailable"），工具调用方式通用性最好
        try:
            structured = self._chat.with_structured_output(schema, method="function_calling")
            return await structured.ainvoke(messages)
        except Exception as e:
            if "InvalidRequestError" not in type(e).__name__:
                raise
            # thinking 模型不支持强制 tool_choice（"Thinking mode does not support
            # this tool_choice"）→ 回退：文本 + JSON 解析（json_repair 容错）
            import json_repair

            fields = ", ".join(
                f'"{name}"' for name in schema.model_fields
            )
            text = await self.complete_text(
                system=(
                    system
                    + "\n\n你必须以合法 JSON 回复, 且只包含以下字段(无其他内容): "
                    + fields
                ),
                user=user,
            )
            return schema.model_validate(json_repair.loads(text))
