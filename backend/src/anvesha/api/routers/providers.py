from datetime import UTC, datetime

import httpx2
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.api.deps import AdminDep, HttpDep, PrincipalDep, SessionDep, SettingsDep, VaultDep
from anvesha.api.schemas.llm import (
    CredentialSaved,
    CredentialWrite,
    OAuthComplete,
    OAuthStartRequest,
    OAuthStartResponse,
    ProviderRead,
)
from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.models import ProviderCredential
from anvesha.llm import openrouter_oauth
from anvesha.llm.catalog import PROVIDERS, ProviderSpec, deployment_access, get_provider
from anvesha.llm.credentials import credential_aad, get_credential, key_hint, resolve_access
from anvesha.llm.discovery import check_credential
from anvesha.net import UnsafeURLError, ensure_public_url, require_http_url
from anvesha.tenancy import Principal

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


def _spec_or_404(provider: str) -> ProviderSpec:
    spec = get_provider(provider)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown provider {provider!r}")
    return spec


def _read(
    spec: ProviderSpec, credential: ProviderCredential | None, has_deployment: bool
) -> ProviderRead:
    return ProviderRead(
        id=spec.id,
        name=spec.name,
        requires_api_key=spec.requires_api_key,
        custom_api_base=spec.custom_api_base,
        default_api_base=spec.default_api_base,
        docs_url=spec.docs_url,
        configured=credential is not None or has_deployment,
        source="workspace" if credential else "deployment" if has_deployment else None,
        status=credential.status if credential else "unverified" if has_deployment else None,
        key_hint=credential.key_hint if credential else None,
        api_base=credential.api_base if credential else None,
        last_checked_at=credential.last_checked_at if credential else None,
        last_error=credential.last_error if credential else None,
    )


@router.get("", response_model=list[ProviderRead])
async def list_providers(
    session: SessionDep, principal: PrincipalDep, settings: SettingsDep
) -> list[ProviderRead]:
    result = []
    for spec in PROVIDERS.values():
        credential = await get_credential(session, principal.workspace_id, spec.id)
        result.append(_read(spec, credential, deployment_access(spec, settings) is not None))
    return result


@router.put("/{provider}/credential", response_model=CredentialSaved)
async def save_credential(
    provider: str,
    payload: CredentialWrite,
    session: SessionDep,
    principal: AdminDep,
    settings: SettingsDep,
    vault: VaultDep,
    http: HttpDep,
) -> CredentialSaved:
    """Store a key (encrypted) after checking it with the provider. Invalid keys are rejected."""
    spec = _spec_or_404(provider)
    api_key = payload.api_key.get_secret_value() if payload.api_key else None
    if spec.requires_api_key and not api_key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{spec.name} needs an API key.")
    if payload.api_base and not spec.custom_api_base:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{spec.name} doesn't accept a custom API base."
        )
    api_base = (payload.api_base or spec.default_api_base or "").rstrip("/")
    if not api_base:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{spec.name} needs an API base URL."
        )
    try:
        require_http_url(api_base)
        if spec.custom_api_base and not settings.allow_private_network_endpoints:
            await ensure_public_url(api_base)
    except UnsafeURLError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if api_key and vault is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Keys can't be stored: no secret key is configured (set ANVESHA_SECRET_KEY).",
        )
    return await _check_and_store(
        session, principal, settings, vault, http, spec, api_key, api_base, payload.api_base
    )


async def _check_and_store(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    vault: SecretBox | None,
    http: httpx2.AsyncClient,
    spec: ProviderSpec,
    api_key: str | None,
    api_base: str,
    custom_api_base: str | None,
) -> CredentialSaved:
    """Check a key with the provider, then store it (encrypted). Invalid keys are refused."""
    check = await check_credential(http, spec, api_base, api_key)
    if check.status == "invalid":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{spec.name} rejected the key: {check.message}",
        )

    credential = await get_credential(session, principal.workspace_id, spec.id)
    if credential is None:
        credential = ProviderCredential(workspace_id=principal.workspace_id, provider=spec.id)
        session.add(credential)
    credential.encrypted_api_key = (
        vault.encrypt(api_key, credential_aad(principal.workspace_id, spec.id))
        if api_key and vault
        else None
    )
    credential.key_hint = key_hint(api_key) if api_key else None
    credential.api_base = custom_api_base.rstrip("/") if custom_api_base else None
    # An unreachable provider might just be down, so keep the key and say it's unverified.
    credential.status = "valid" if check.status == "valid" else "unverified"
    credential.last_error = check.message
    credential.last_checked_at = datetime.now(UTC)
    await session.commit()

    read = _read(spec, credential, deployment_access(spec, settings) is not None)
    return CredentialSaved(**read.model_dump(), models=check.models)


@router.post("/openrouter/oauth/start", response_model=OAuthStartResponse)
async def start_openrouter_oauth(
    payload: OAuthStartRequest, principal: AdminDep, settings: SettingsDep, vault: VaultDep
) -> OAuthStartResponse:
    """Begin "Connect OpenRouter": returns where to send the user, and a state to keep.

    OpenRouter sends the user back to ``callback_url`` with ``?code=``; finish with
    ``/openrouter/oauth/complete``. The key is created on the user's own OpenRouter
    account, so usage is billed there.
    """
    if vault is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Keys can't be stored: no secret key is configured (set ANVESHA_SECRET_KEY).",
        )
    callback = payload.callback_url
    try:
        require_http_url(callback)
    except UnsafeURLError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if settings.auth_mode == "jwt" and not callback.startswith(settings.web_url + "/"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The callback must be a page of the web app ({settings.web_url}).",
        )
    started = openrouter_oauth.start(vault, principal, callback)
    return OAuthStartResponse(authorization_url=started.authorization_url, state=started.state)


@router.post("/openrouter/oauth/complete", response_model=CredentialSaved)
async def complete_openrouter_oauth(
    payload: OAuthComplete,
    session: SessionDep,
    principal: AdminDep,
    settings: SettingsDep,
    vault: VaultDep,
    http: HttpDep,
) -> CredentialSaved:
    """Finish "Connect OpenRouter": trade the code for a key and save it like a pasted key."""
    if vault is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No secret key is configured.")
    verifier = openrouter_oauth.open_state(vault, principal, payload.state)
    api_key = await openrouter_oauth.exchange(http, payload.code, verifier)
    spec = _spec_or_404("openrouter")
    assert spec.default_api_base
    return await _check_and_store(
        session, principal, settings, vault, http, spec, api_key, spec.default_api_base, None
    )


@router.delete("/{provider}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(provider: str, session: SessionDep, principal: AdminDep) -> Response:
    spec = _spec_or_404(provider)
    credential = await get_credential(session, principal.workspace_id, spec.id)
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No saved credential for {spec.name}.")
    await session.delete(credential)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{provider}/models", response_model=list[str])
async def list_models(
    provider: str,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    vault: VaultDep,
    http: HttpDep,
) -> list[str]:
    """Live model list from the provider. Also re-verifies a saved workspace key."""
    spec = _spec_or_404(provider)
    access = await resolve_access(session, settings, vault, principal.workspace_id, spec.id)
    if access is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{spec.name} isn't configured yet.")
    check = await check_credential(http, spec, access.api_base, access.api_key)

    if access.source == "workspace":
        credential = await get_credential(session, principal.workspace_id, spec.id)
        if credential is not None:
            credential.status = "unverified" if check.status == "unreachable" else check.status
            credential.last_error = check.message
            credential.last_checked_at = datetime.now(UTC)
            await session.commit()

    if check.status != "valid":
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"{spec.name} is {check.status}: {check.message}"
        )
    return check.models
