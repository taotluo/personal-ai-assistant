"""极薄的 LLM 调用层 —— 全项目唯一与模型 SDK 对话的模块。

将来更换/新增模型厂商，只需实现一个具备相同 chat()/chat_json() 接口的类，
编排层对此无感知（方案中的"逃生门"）。所有结构化输出走 chat_json：
解析失败会把错误反馈给模型重试一次，仍失败抛 LLMError。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("paa.llm")

T = TypeVar("T", bound=BaseModel)

WEB_SEARCH_TOOLS = [
    {
        "type": "web_search",
        "web_search": {"enable": True, "search_engine": "search_std"},
    }
]


class LLMError(RuntimeError):
    """LLM 调用或结构化解析失败。"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class BaseLLM:
    """所有 LLM 客户端的统一接口。tier: strong=强模型 / fast=便宜模型。"""

    async def chat(
        self, system: str, user: str, *, tier: str = "strong", web_search: bool = False
    ) -> str:
        raise NotImplementedError

    async def _messages_chat(self, messages: list[dict], tier: str) -> str:
        raise NotImplementedError

    async def chat_json(
        self, system: str, user: str, schema: Type[T], *, tier: str = "strong"
    ) -> T:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        raw = await self._messages_chat(messages, tier)
        try:
            return schema.model_validate_json(_strip_fences(raw))
        except ValidationError as err:
            log.warning("结构化输出解析失败，带错误反馈重试一次：%s", err)
            messages += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"上面的输出无法解析为要求的 JSON 结构（错误：{err}）。"
                        "请重新输出，只输出一个合法的 JSON 对象，"
                        "不要任何解释，不要 Markdown 代码围栏。"
                    ),
                },
            ]
            raw = await self._messages_chat(messages, tier)
            try:
                return schema.model_validate_json(_strip_fences(raw))
            except ValidationError as err2:
                raise LLMError(
                    f"模型两次都未输出合法 JSON：{err2}\n最后一次原始输出：{raw[:500]}"
                ) from err2


class ZhipuLLMClient(BaseLLM):
    """智谱 GLM API 客户端（同步 SDK，外面包一层 asyncio.to_thread）。"""

    def __init__(self, api_key: str, model_strong: str, model_fast: str):
        if not api_key:
            raise LLMError(
                "缺少 API key：请在 backend/.env 设置 PAA_ZHIPU_API_KEY，"
                "或用 PAA_LLM=mock 冒烟"
            )
        from zhipuai import ZhipuAI  # 延迟导入：mock 模式无需安装

        self._client = ZhipuAI(api_key=api_key)
        self._models = {"strong": model_strong, "fast": model_fast}

    async def chat(
        self, system: str, user: str, *, tier: str = "strong", web_search: bool = False
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if web_search:
            try:
                return await self._create(messages, tier, tools=WEB_SEARCH_TOOLS)
            except Exception as err:
                log.warning("联网搜索调用失败，降级为普通回答：%s", err)
        return await self._create(messages, tier)

    async def _messages_chat(self, messages: list[dict], tier: str) -> str:
        return await self._create(messages, tier)

    async def _create(self, messages: list[dict], tier: str, tools: list | None = None) -> str:
        def call() -> str:
            kwargs: dict = dict(
                model=self._models[tier],
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
            )
            if tools:
                kwargs["tools"] = tools
            resp = self._client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""

        return await asyncio.to_thread(call)
