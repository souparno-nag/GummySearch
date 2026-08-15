"""Application settings.

This is the only module permitted to read secrets from the environment
(Constitution, Technology and Data Constraints). Every other module that needs
a credential or a connection string imports `settings` from here rather than
calling `os.getenv` directly.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Reddit — consumed only by backend/app/reddit/ (Constitution I)
    reddit_client_id: str = Field(alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(alias="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = Field(alias="REDDIT_USER_AGENT")

    # Database — async driver required by Constitution VI
    database_url: str = Field(alias="DATABASE_URL")

    # Redis — cache and Celery broker
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # LLM — Groq for completions, local sentence-transformers for embeddings (R1).
    # Model identifiers are pinned here explicitly; a floating alias is prohibited
    # by Constitution VII.
    groq_api_key: str = Field(alias="GROQ_API_KEY")
    groq_model: str = Field(alias="GROQ_MODEL")
    embedding_model_name: str = Field(alias="EMBEDDING_MODEL_NAME")

    # Deployment exposure (FR-078). Binding beyond loopback requires this explicit opt-in;
    # the startup bind guard built in T017-T018 reads this flag rather than the raw env var.
    allow_remote_exposure: bool = Field(default=False, alias="ALLOW_REMOTE_EXPOSURE")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings instance, constructed once and cached."""
    return Settings()


settings = get_settings()
