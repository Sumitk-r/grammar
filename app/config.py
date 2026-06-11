from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Khan Transcript Library"
    database_url: str = "sqlite:///./grammar.db"
    khan_country_code: str = "US"
    khan_cookie: str | None = None
    request_delay_seconds: float = 0.25
    worker_poll_seconds: float = 2.0
    max_videos_per_job: int | None = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

