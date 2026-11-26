from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url


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

    # --- Secrets ---------------------------------------------------------------------
    # Master key that encrypts stored provider API keys (32 bytes, base64url). If unset,
    # a key is generated on first start and kept in ``secret_key_file``.
    secret_key: SecretStr | None = None
    secret_key_file: Path | None = Path(".anvesha/secret.key")
    # Comma-separated retired master keys, still accepted for decryption during rotation.
    previous_secret_keys: SecretStr | None = None

    # --- Model calls -------------------------------------------------------------------
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    # Let custom endpoints (Ollama, OpenAI-compatible servers) point at localhost or
    # private networks. Right for self-hosting; the hosted service turns it off.
    allow_private_network_endpoints: bool = True

    # --- Deployment-wide provider access ------------------------------------------------
    # Optional keys set by whoever runs this server. Used when a workspace hasn't added
    # its own key for that provider. In self-hosting these are simply your own keys.
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    mistral_api_key: SecretStr | None = None
    ollama_base_url: str | None = None
    openai_compatible_base_url: str | None = None
    openai_compatible_api_key: SecretStr | None = None

    # --- Web search ----------------------------------------------------------------------
    # SearXNG is the free, self-hosted default (docker compose runs one). The others are
    # optional paid APIs; workspaces can also add their own keys for them.
    searxng_url: str | None = None
    tavily_api_key: SecretStr | None = None
    brave_api_key: SecretStr | None = None
    exa_api_key: SecretStr | None = None
    # Used when a request doesn't name a provider. None: the first configured one.
    default_search_provider: str | None = None
    search_timeout_seconds: float = 20.0

    # --- Scholarly sources (all free) ---------------------------------------------------
    # A contact address unlocks the faster "polite" pools of OpenAlex, Crossref and NCBI.
    scholar_contact_email: str | None = None
    openalex_api_key: SecretStr | None = None
    semantic_scholar_api_key: SecretStr | None = None
    ncbi_api_key: SecretStr | None = None

    # --- Fetching pages and documents ---------------------------------------------------
    # Sent to websites when fetching pages. Operators can add a contact URL, e.g.
    # "Mozilla/5.0 (compatible; AnveshaBot/0.2; +https://research.example.org/bot)".
    http_user_agent: str = "Mozilla/5.0 (compatible; AnveshaBot/0.2; open-source research agent)"
    # Sent to search and scholarly APIs. Some (NCBI) block anything that looks like a bot.
    api_user_agent: str = "Anvesha/0.2 (open-source research platform)"
    # Product token matched against robots.txt rules.
    robots_user_agent: str = "AnveshaBot"
    fetch_timeout_seconds: float = 30.0
    fetch_max_bytes: int = 25 * 1024 * 1024
    fetch_max_redirects: int = 5
    fetch_max_concurrency: int = 8
    fetch_respect_robots_txt: bool = True
    # Let fetched URLs reach localhost and private networks. Off by default even for
    # self-hosting: agents choose these URLs, and pages can try to steer them inward.
    fetch_allow_private_networks: bool = False
    upload_max_bytes: int = 25 * 1024 * 1024
    pdf_max_pages: int = 1000

    # --- Files ----------------------------------------------------------------------------
    # Uploaded and downloaded originals (PDFs). Extracted text lives in Postgres.
    storage_dir: Path = Path(".anvesha/files")

    # --- Retrieval ---------------------------------------------------------------------
    chunk_max_chars: int = 1200
    chunk_overlap_chars: int = 200
    max_chunks_per_source: int = 4000
    embedding_batch_size: int = 64

    # --- Research runs -----------------------------------------------------------------
    # Spending limit for one run when the request doesn't set one. Runs stop gracefully
    # (with a partial report) when they reach it. None: no cost limit.
    run_default_max_cost_usd: Decimal | None = Decimal("2.00")
    # The most a single request may ask for. None: no ceiling.
    run_max_cost_ceiling_usd: Decimal | None = Decimal("50.00")
    run_default_max_tokens: int | None = None
    # Researcher agents working at the same time within one run.
    run_max_parallel_researchers: int = Field(default=4, ge=1, le=16)
    # A run that takes longer than this is stopped (it can be retried from its checkpoint).
    run_timeout_minutes: int = Field(default=90, ge=1)
    # How many times the worker tries a run that hit a temporary failure (provider outage).
    run_max_attempts: int = Field(default=3, ge=1, le=10)
    # Research runs one worker process executes at the same time.
    worker_concurrency: int = Field(default=4, ge=1, le=64)

    # --- MCP server ------------------------------------------------------------------------
    # Serve Anvesha's research tools to MCP clients (Claude, ChatGPT, Gemini CLI, Cursor) at
    # /mcp. Clients need an access token (Settings → Access tokens, or
    # `python -m anvesha.access_tokens create`).
    mcp_enabled: bool = True
    # The address MCP clients on the internet reach Anvesha at, e.g.
    # "https://anvesha.example.org" (the public app, `create_public_app`, behind a tunnel or
    # reverse proxy). Setting it turns on OAuth sign-in, which claude.ai and ChatGPT
    # connectors use. Must be https unless it is localhost.
    mcp_public_url: str | None = None
    # In single-user mode there are no accounts, so approving a new connection asks for
    # this secret instead. Required when mcp_public_url is set. At least 12 characters.
    mcp_approval_secret: SecretStr | None = Field(default=None, min_length=12)
    mcp_oauth_access_token_minutes: int = Field(default=60, ge=5, le=24 * 60)
    mcp_oauth_refresh_token_days: int = Field(default=30, ge=1, le=365)

    # --- Project chat (Research Assistant) ------------------------------------------------
    # Spending limit for writing one chat reply. None: no limit.
    chat_max_cost_usd: Decimal | None = Decimal("0.25")
    # Model responses (tool-calling steps) the assistant may use for one reply.
    chat_max_steps: int = Field(default=8, ge=2, le=30)
    # A reply still being written after this long is reported as failed.
    chat_reply_timeout_seconds: int = Field(default=240, ge=30)
    # Replies one API process writes at the same time; more wait their turn.
    chat_max_concurrent_replies: int = Field(default=8, ge=1, le=128)

    @field_validator(
        "secret_key",
        "previous_secret_keys",
        "openai_api_key",
        "anthropic_api_key",
        "gemini_api_key",
        "openrouter_api_key",
        "groq_api_key",
        "mistral_api_key",
        "ollama_base_url",
        "openai_compatible_base_url",
        "openai_compatible_api_key",
        "searxng_url",
        "tavily_api_key",
        "brave_api_key",
        "exa_api_key",
        "default_search_provider",
        "scholar_contact_email",
        "openalex_api_key",
        "semantic_scholar_api_key",
        "ncbi_api_key",
        "run_default_max_cost_usd",
        "run_max_cost_ceiling_usd",
        "run_default_max_tokens",
        "chat_max_cost_usd",
        "mcp_public_url",
        "mcp_approval_secret",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value: Any) -> Any:
        # Docker Compose passes unset variables as empty strings.
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def postgres_dsn(self) -> str:
        """The database URL as a plain ``postgresql://`` DSN (for asyncpg's LISTEN)."""
        return (
            make_url(self.database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )

    @property
    def psycopg_database_url(self) -> str:
        """The database URL for libraries that use psycopg (LangGraph, Procrastinate).

        Adds a connect timeout unless one is set, so an unreachable address fails instead
        of hanging (libpq waits indefinitely by default).
        """
        url = make_url(self.database_url).set(drivername="postgresql")
        if "connect_timeout" not in url.query:
            url = url.update_query_dict({"connect_timeout": "10"})
        return url.render_as_string(hide_password=False)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @field_validator("mcp_public_url")
    @classmethod
    def _plain_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        url = make_url_parts(value)
        if url is None:
            raise ValueError("must be an http(s) URL like https://anvesha.example.org")
        return value

    @property
    def mcp_oauth_enabled(self) -> bool:
        return self.mcp_enabled and self.mcp_public_url is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()


def make_url_parts(value: str) -> tuple[str, str] | None:
    """(scheme, host) of an absolute http(s) URL without query or fragment, else None."""
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.query or parsed.fragment:
        return None
    return parsed.scheme, parsed.hostname
