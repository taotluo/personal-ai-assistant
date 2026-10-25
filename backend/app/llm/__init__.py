from ..config import Settings
from .client import BaseLLM, LLMError, ZhipuLLMClient
from .mock import MockLLMClient
from .openai_compat import OpenAICompatLLMClient


def build_llm(settings: Settings) -> BaseLLM:
    if settings.llm == "mock":
        return MockLLMClient()
    if settings.llm == "openai":
        return OpenAICompatLLMClient(
            settings.openai_api_key,
            settings.openai_base_url,
            settings.model_strong,
            settings.model_fast,
        )
    return ZhipuLLMClient(settings.zhipu_api_key, settings.model_strong, settings.model_fast)


__all__ = [
    "BaseLLM",
    "LLMError",
    "ZhipuLLMClient",
    "MockLLMClient",
    "OpenAICompatLLMClient",
    "build_llm",
]
