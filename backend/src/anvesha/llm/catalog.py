"""Supported model providers.

Most providers speak the OpenAI chat-completions protocol, so one adapter covers them;
Anthropic gets a native adapter. Adding an OpenAI-compatible provider is one entry here.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

from anvesha.config import Settings


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    api_style: Literal["openai", "anthropic"]
    default_api_base: str | None
    requires_api_key: bool
    # Whether a workspace may set its own base URL (local servers, self-hosted gateways).
    custom_api_base: bool
    # Provider id in the genai-prices database; None when prices are unknown or zero.
    pricing_provider: str | None
    # Settings fields holding deployment-wide access for this provider.
    settings_key_field: str | None
    settings_base_field: str | None = None
    # Path checked to validate a key, relative to the API base (default: the models list).
    key_check_path: str = "/models"
    free: bool = False
    docs_url: str = ""


PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in (
        ProviderSpec(
            id="openai",
            name="OpenAI",
            api_style="openai",
            default_api_base="https://api.openai.com/v1",
            requires_api_key=True,
            custom_api_base=False,
            pricing_provider="openai",
            settings_key_field="openai_api_key",
            docs_url="https://platform.openai.com/api-keys",
        ),
        ProviderSpec(
            id="anthropic",
            name="Anthropic (Claude)",
            api_style="anthropic",
            default_api_base="https://api.anthropic.com",
            requires_api_key=True,
            custom_api_base=False,
            pricing_provider="anthropic",
            settings_key_field="anthropic_api_key",
            key_check_path="/v1/models?limit=1000",
            docs_url="https://console.anthropic.com/settings/keys",
        ),
        ProviderSpec(
            id="gemini",
            name="Google Gemini",
            api_style="openai",
            default_api_base="https://generativelanguage.googleapis.com/v1beta/openai",
            requires_api_key=True,
            custom_api_base=False,
            pricing_provider="google",
            settings_key_field="gemini_api_key",
            docs_url="https://aistudio.google.com/apikey",
        ),
        ProviderSpec(
            id="openrouter",
            name="OpenRouter",
            api_style="openai",
            default_api_base="https://openrouter.ai/api/v1",
            requires_api_key=True,
            custom_api_base=False,
            pricing_provider="openrouter",
            settings_key_field="openrouter_api_key",
            # The models list is public; this endpoint actually checks the key.
            key_check_path="/key",
            docs_url="https://openrouter.ai/settings/keys",
        ),
        ProviderSpec(
            id="groq",
            name="Groq",
            api_style="openai",
            default_api_base="https://api.groq.com/openai/v1",
            requires_api_key=True,
            custom_api_base=False,
            pricing_provider="groq",
            settings_key_field="groq_api_key",
            docs_url="https://console.groq.com/keys",
        ),
        ProviderSpec(
            id="mistral",
            name="Mistral",
            api_style="openai",
            default_api_base="https://api.mistral.ai/v1",
            requires_api_key=True,
            custom_api_base=False,
            pricing_provider="mistral",
            settings_key_field="mistral_api_key",
            docs_url="https://console.mistral.ai/api-keys",
        ),
        ProviderSpec(
            id="ollama",
            name="Ollama (local models)",
            api_style="openai",
            default_api_base="http://localhost:11434/v1",
            requires_api_key=False,
            custom_api_base=True,
            pricing_provider=None,
            settings_key_field=None,
            settings_base_field="ollama_base_url",
            free=True,
            docs_url="https://ollama.com/download",
        ),
        ProviderSpec(
            id="openai_compatible",
            name="Custom OpenAI-compatible endpoint",
            api_style="openai",
            default_api_base=None,
            requires_api_key=False,
            custom_api_base=True,
            pricing_provider=None,
            settings_key_field="openai_compatible_api_key",
            settings_base_field="openai_compatible_base_url",
            docs_url="",
        ),
    )
}


def get_provider(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider_id)


def deployment_access(spec: ProviderSpec, settings: Settings) -> tuple[str, str | None] | None:
    """(api_base, api_key) configured server-wide for this provider, or None if not set up."""
    key_value = getattr(settings, spec.settings_key_field) if spec.settings_key_field else None
    api_key = key_value.get_secret_value() if isinstance(key_value, SecretStr) else None
    base_value = getattr(settings, spec.settings_base_field) if spec.settings_base_field else None
    api_base = base_value or spec.default_api_base

    if spec.requires_api_key and not api_key:
        return None
    # Providers with a custom base count as configured only when the base was set
    # explicitly; otherwise every install would claim a local Ollama exists.
    if spec.custom_api_base and not base_value:
        return None
    if api_base is None:
        return None
    return api_base.rstrip("/"), api_key
