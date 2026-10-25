"""OpenAI 兼容 LLM 客户端：任何 OpenAI 风格 API 均可接。

适用：DeepSeek / Kimi(Moonshot) / 通义 Qwen(兼容模式) / OpenAI / Ollama 本地 /
vLLM / one-api 等网关。智谱官方也提供 OpenAI 兼容端点
（https://open.bigmodel.cn/api/paas/v4/），因此这个后端同样能接智谱。

注意：OpenAI 兼容协议没有统一的"联网搜索"工具标准，research 类任务的
web_search 请求在此后端会降级为普通回答（记一条日志）。
"""

from __future__ import annotations

import asyncio
import logging

from .client import BaseLLM, LLMError

log = logging.getLogger("paa.llm.openai")


class OpenAICompatLLMClient(BaseLLM):
    def __init__(self, api_key: str, base_url: str, model_strong: str, model_fast: str):
        if not api_key:
            raise LLMError("缺少 API key：请在 backend/.env 设置 PAA_OPENAI_API_KEY")
        from openai import OpenAI  # 延迟导入：zhipu/mock 模式无需安装

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
        self._models = {"strong": model_strong, "fast": model_fast}
        self._search_warned = False

    async def chat(
        self, system: str, user: str, *, tier: str = "strong", web_search: bool = False
    ) -> str:
        if web_search and not self._search_warned:
            self._search_warned = True
            log.info("OpenAI 兼容后端不自带联网搜索工具，本次按模型已有知识回答")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return await self._messages_chat(messages, tier)

    async def _messages_chat(self, messages: list[dict], tier: str) -> str:
        def call() -> str:
            resp = self._client.chat.completions.create(
                model=self._models[tier],
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
            )
            return resp.choices[0].message.content or ""

        return await asyncio.to_thread(call)
