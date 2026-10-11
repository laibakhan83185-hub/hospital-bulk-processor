import pytest
from pydantic import ValidationError

from app.config import Settings, load_settings


def test_defaults_are_used_without_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)

    settings = load_settings()

    assert settings.hospital_api_url == "https://hospital-directory.onrender.com"
    assert settings.max_attempts == 3
    assert settings.log_level == "INFO"


def test_environment_overrides_defaults(monkeypatch):
    monkeypatch.setenv("HOSPITAL_API_URL", "http://localhost:9000/")
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = load_settings()

    assert settings.hospital_api_url == "http://localhost:9000"
    assert settings.max_attempts == 5
    assert settings.log_level == "DEBUG"


def test_invalid_values_are_rejected(monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "0")

    with pytest.raises(ValidationError):
        load_settings()

    monkeypatch.setenv("MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError, match="Invalid LOG_LEVEL"):
        load_settings()
