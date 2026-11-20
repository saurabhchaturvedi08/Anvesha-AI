"""Validate provider credentials and list available models without spending tokens.

A key is checked by calling the provider's (free) models endpoint, which also gives the
list of models to choose from.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2
from anthropic._constants import ANTHROPIC_API_VERSION

from anvesha.llm.catalog import ProviderSpec

CHECK_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class CredentialCheck:
    status: Literal["valid", "invalid", "unreachable"]
    message: str | None = None
    models: list[str] = field(default_factory=list)


def _headers(spec: ProviderSpec, api_key: str | None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if spec.api_style == "anthropic":
        # Same API version the SDK sends, so key checks and real calls agree.
        headers["anthropic-version"] = ANTHROPIC_API_VERSION
    return headers


def _model_ids(payload: Any) -> list[str]:
    items = payload.get("data") if isinstance(payload, dict) else None
    ids: set[str] = set()
    for item in items if isinstance(items, list) else []:
        model_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(model_id, str) and model_id:
            # Gemini's OpenAI-compatible list prefixes ids with "models/".
            ids.add(model_id.removeprefix("models/"))
    return sorted(ids)


def _error_message(response: httpx2.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(payload, list) and payload:
        payload = payload[0]  # Gemini wraps errors in a list
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return str(error["message"])[:300]
    if isinstance(error, str):
        return error[:300]
    return f"HTTP {response.status_code}"


async def check_credential(
    http: httpx2.AsyncClient, spec: ProviderSpec, api_base: str, api_key: str | None
) -> CredentialCheck:
    base = api_base.rstrip("/")
    headers = _headers(spec, api_key)
    try:
        response = await http.get(
            f"{base}{spec.key_check_path}", headers=headers, timeout=CHECK_TIMEOUT_SECONDS
        )
        # Gemini answers a bad key with 400 rather than 401.
        if response.status_code in (400, 401, 403):
            return CredentialCheck("invalid", _error_message(response))
        if response.status_code != 200:
            return CredentialCheck("unreachable", _error_message(response))
        models_response = response
        if spec.key_check_path != "/models" and spec.api_style == "openai":
            models_response = await http.get(
                f"{base}/models", headers=headers, timeout=CHECK_TIMEOUT_SECONDS
            )
            models_response.raise_for_status()
        return CredentialCheck("valid", models=_model_ids(models_response.json()))
    except (httpx2.HTTPError, ValueError) as exc:
        return CredentialCheck("unreachable", f"{exc.__class__.__name__}: {exc}"[:300])
