"""全局配置：环境变量前缀 PAA_，支持 backend/.env。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8600
    token: str = "dev-token"          # WS 认证令牌，公网部署前务必修改
    llm: str = "zhipu"              # zhipu | openai（任何 OpenAI 兼容 API）| mock
    zhipu_api_key: str = ""
    openai_api_key: str = ""        # PAA_LLM=openai 时使用
    openai_base_url: str = "https://api.openai.com/v1"
    model_strong: str = "glm-4.6"     # 规划 / 审核 / 总结
    model_fast: str = "glm-4.5-flash" # 简单子任务（分级调用接口预留）
    data_dir: Path = Path("data")
    agent_timeout: float = 600.0      # 单个子任务最长执行秒数
    max_retries: int = 2              # 每个子任务自动重试次数上限（执行失败+审核不通过共用预算）
    max_review_rounds: int = 2        # 审核-重跑循环轮数上限

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"


settings = Settings()
