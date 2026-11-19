from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables prefixed with ``ANVESHA_``.

    A ``.env`` file in the repository root or the current directory is also read.
    """

    model_config = SettingsConfigDict(
        env_prefix="ANVESHA_",
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://anvesha:anvesha@localhost:5432/anvesha"
    # Comma-separated browser origins allowed to call the API (the web app).
    cors_origins: str = "http://localhost:3000"
    # "local" is single-user mode for self-hosting: every request acts as one built-in
    # user and workspace. Multi-user JWT auth arrives with the hosted SaaS phase.
    auth_mode: Literal["local"] = "local"
    local_user_email: str = "local@anvesha.invalid"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
