import os

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    """Application settings, read from environment variables.

    Every setting has a default, so the app runs without any configuration.
    """

    hospital_api_url: str = "https://hospital-directory.onrender.com"
    request_timeout_seconds: float = Field(60.0, gt=0)
    connect_timeout_seconds: float = Field(30.0, gt=0)
    max_attempts: int = Field(3, ge=1)
    retry_base_delay_seconds: float = Field(1.0, ge=0)
    log_level: str = "INFO"

    @field_validator("hospital_api_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid LOG_LEVEL: {value}")
        return value


def load_settings() -> Settings:
    """Build settings from environment variables (unset ones use defaults)."""
    env = {
        field: os.environ[field.upper()]
        for field in Settings.model_fields
        if field.upper() in os.environ
    }
    return Settings(**env)


settings = load_settings()
