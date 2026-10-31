from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    
    gemini_api_key: SecretStr
    llm_model: str = "gemini-2.5-flash"
    llm_max_tokens: int = 2048
    llm_timeout_s: float = 60.0
    llm_retries: int = 3
    
    db_path: str = "data/parser.db"
    arxiv_user_agent: str = "arxiv-qa/0.1 (contact: abhishek@example.com)"
    
    max_history_turns: int = 10
    cors_origins: list[str] = ["http://localhost:3000"]  # env: JSON list
    ingest_rate_per_min: int = 5     # per IP
    chat_rate_per_min: int = 20      # per IP
    
    max_paper_tokens: int = 150_000
    
@lru_cache
def get_settings() -> Settings:
    return Settings()