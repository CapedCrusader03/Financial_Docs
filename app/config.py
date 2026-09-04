from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://filings:filings@localhost:5433/filings"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    embedding_model: str = "all-MiniLM-L6-v2"
    sec_user_agent: str = "FilingQA contact@example.com"
    narrative_chunk_chars: int = 2200


@lru_cache
def settings() -> Settings:
    return Settings()
