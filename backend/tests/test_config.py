import pytest

from anvesha.config import Settings


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = Settings(cors_origins=" http://localhost:3000 , https://app.example.org ,")
    assert settings.cors_origin_list == ["http://localhost:3000", "https://app.example.org"]


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANVESHA_DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    assert Settings().database_url == "postgresql+asyncpg://u:p@db:5432/x"


def test_blank_environment_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANVESHA_SECRET_KEY", "")
    monkeypatch.setenv("ANVESHA_OLLAMA_BASE_URL", "  ")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.secret_key is None
    assert settings.ollama_base_url is None
