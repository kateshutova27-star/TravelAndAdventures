from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://travel:travel@localhost:5432/travel_db"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # External API keys
    GEOAPIFY_API_KEY: str = ""
    GRAPHHOPPER_API_KEY: str = ""
    PIXABAY_API_KEY: str = ""

    # Observability
    SENTRY_DSN: Optional[str] = None
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    # Cache
    PLACES_CACHE_TTL: int = 1_209_600  # 2 weeks in seconds

    # Worker
    ARQ_MAX_JOBS: int = 10
    ARQ_JOB_TIMEOUT: int = 180

    @property
    def cors_origins(self) -> list[str]:
        return self.CORS_ORIGINS

    @property
    def sentry_dsn(self) -> str | None:
        return self.SENTRY_DSN

    @property
    def redis_url(self) -> str:
        return self.REDIS_URL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton instance of Settings."""
    return Settings()
