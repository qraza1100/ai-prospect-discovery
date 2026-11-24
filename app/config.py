from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["anthropic", "gemini", "openai", "none"]

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-5-5",
    "gemini": "gemini-3.1-flash-lite",
    "openai": "gpt-6-luna",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Prospect Discovery API"
    environment: Literal["development", "production", "test"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(4000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    search_provider: Literal["serper", "brave"] = "serper"
    serper_api_key: SecretStr | None = None
    brave_api_key: SecretStr | None = None
    youtube_api_key: SecretStr | None = None

    llm_provider: LLMProvider = "anthropic"
    llm_model: str | None = None
    llm_fallback_provider: LLMProvider = "none"
    llm_fallback_model: str | None = None
    llm_effort: Literal["low", "medium", "high", ""] = "low"
    llm_timeout_seconds: float = Field(20.0, gt=0, le=120)
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    search_results_per_query: int = Field(10, ge=1, le=20)
    search_concurrency: int = Field(4, ge=1, le=16)
    search_timeout_seconds: float = Field(10.0, gt=0, le=60)
    youtube_max_channels: int = Field(15, ge=1, le=50)

    enable_website_enrichment: bool = True
    max_websites_per_request: int = Field(8, ge=0, le=25)
    website_timeout_seconds: float = Field(8.0, gt=0, le=30)
    website_max_bytes: int = Field(1_000_000, ge=10_000, le=5_000_000)
    enable_cross_platform_lookup: bool = True
    max_cross_platform_lookups: int = Field(4, ge=0, le=10)
    enrichment_timeout_seconds: float = Field(15.0, gt=0, le=60)

    max_prospects: int = Field(25, ge=1, le=100)
    relevance_batch_size: int = Field(20, ge=1, le=50)
    request_timeout_seconds: float = Field(90.0, gt=0, le=300)

    cache_ttl_seconds: int = Field(900, ge=0)
    intent_cache_ttl_seconds: int = Field(86_400, ge=0)
    cache_max_entries: int = Field(1024, ge=16)

    rate_limit_per_minute: int = Field(10, ge=0)
    api_auth_token: SecretStr | None = None
    cors_allow_origins: list[str] = Field(default_factory=list)
    user_agent: str = "ProspectDiscoveryBot/1.0"

    @property
    def resolved_llm_model(self) -> str | None:
        if self.llm_provider == "none":
            return None
        return self.llm_model or DEFAULT_MODELS[self.llm_provider]

    @property
    def resolved_fallback_model(self) -> str | None:
        if self.llm_fallback_provider == "none":
            return None
        return self.llm_fallback_model or DEFAULT_MODELS[self.llm_fallback_provider]


@lru_cache
def get_settings() -> Settings:
    return Settings()
