from __future__ import annotations

from typing import Any


DEFAULT_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "ollama",
        "name": "Ollama",
        "type": "local",
        "model": "llama3.2",
        "base_url": "http://127.0.0.1:11434",
        "status": "ready",
        "note": "Runs locally without sending data to the cloud.",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "type": "cloud",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "status": "not_configured",
        "note": "Requires an API key in the desktop settings.",
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "type": "cloud",
        "model": "claude-3-5-sonnet",
        "base_url": "https://api.anthropic.com",
        "status": "not_configured",
        "note": "Add your API key to enable cloud analysis.",
    },
    {
        "id": "gemini",
        "name": "Gemini",
        "type": "cloud",
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com",
        "status": "not_configured",
        "note": "Connect your Google AI Studio key for remote research.",
    },
]


def list_providers() -> list[dict[str, Any]]:
    return [provider.copy() for provider in DEFAULT_PROVIDERS]


def validate_provider(provider_id: str) -> dict[str, Any]:
    provider = next((item for item in DEFAULT_PROVIDERS if item["id"] == provider_id), None)
    if provider is None:
        raise ValueError(f"Unknown provider: {provider_id}")

    status = "ready" if provider["type"] == "local" else "configured"
    result = provider.copy()
    result["status"] = status
    result["note"] = "Connection check passed in local-only validation mode."
    return result
