from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://filings:filings@localhost:5433/filings"
    openai_api_key: str | None = None
    llm_model: str = "gpt-5-mini"
    embedding_model: str = "text-embedding-3-small"
    sec_user_agent: str = "FilingQA contact@example.com"
    narrative_chunk_chars: int = 2200


@lru_cache
def settings() -> Settings:
    return Settings()
