"""LLM 后端路由：mock / zhipu / openai 兼容。"""

import pytest

from app.config import Settings
from app.llm import (
    LLMError,
    MockLLMClient,
    OpenAICompatLLMClient,
    ZhipuLLMClient,
    build_llm,
)


def test_build_mock():
    assert isinstance(build_llm(Settings(llm="mock")), MockLLMClient)


def test_build_openai_compat_no_network_at_init():
    llm = build_llm(
        Settings(
            llm="openai",
            openai_api_key="sk-test",
            openai_base_url="http://localhost:9999/v1",
            model_strong="m-strong",
            model_fast="m-fast",
        )
    )
    assert isinstance(llm, OpenAICompatLLMClient)


def test_openai_missing_key_fails_fast():
    with pytest.raises(LLMError):
        build_llm(Settings(llm="openai", openai_api_key=""))


def test_build_zhipu():
    llm = build_llm(Settings(llm="zhipu", zhipu_api_key="k"))
    assert isinstance(llm, ZhipuLLMClient)
