from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Aveti Transcripts"
    database_url: str = "sqlite:///./grammar.db"
    khan_country_code: str = "US"
    khan_cookie: str | None = None
    request_delay_seconds: float = 0.25
    worker_poll_seconds: float = 2.0
    max_videos_per_job: int | None = None
    youtube_fallback_enabled: bool = True
    youtube_languages: str = "en,en-IN,hi"
    audio_transcription_enabled: bool = True
    audio_transcription_max_duration_seconds: int = 1800
    faster_whisper_model: str = "tiny"
    faster_whisper_device: str = "cpu"
    faster_whisper_compute_type: str = "int8"
    embedding_dimensions: int = 128
    pgvector_enabled: bool = False
    display_timezone: str = "Asia/Kolkata"
    admin_key: str = "admin"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
