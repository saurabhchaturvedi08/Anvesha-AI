"""Workspace credentials: storage, lookup, and resolving which access a call should use."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.config import Settings
from anvesha.crypto import DecryptionError, SecretBox
from anvesha.db.models import ModelPreference, ProviderCredential
from anvesha.llm.catalog import ProviderSpec, deployment_access, get_provider
from anvesha.llm.errors import ModelNotConfiguredError
from anvesha.llm.types import ModelRole, ModelRoute, ProviderAccess


def credential_aad(workspace_id: uuid.UUID, provider: str) -> str:
    """Associated data binding an encrypted key to its workspace and provider."""
    return f"provider-credential:{workspace_id}:{provider}"


def key_hint(api_key: str) -> str:
    return f"…{api_key[-4:]}" if len(api_key) >= 8 else "…"


async def get_credential(
    session: AsyncSession, workspace_id: uuid.UUID, provider: str
) -> ProviderCredential | None:
    return await session.scalar(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.provider == provider,
        )
    )


def access_from_credential(
    spec: ProviderSpec, credential: ProviderCredential, vault: SecretBox | None
) -> ProviderAccess:
    api_key = None
    if credential.encrypted_api_key is not None:
        if vault is None:
            raise ModelNotConfiguredError(
                "Stored provider keys can't be decrypted because no secret key is configured."
            )
        try:
            api_key = vault.decrypt(
                credential.encrypted_api_key,
                credential_aad(credential.workspace_id, credential.provider),
            )
        except DecryptionError as exc:
            raise ModelNotConfiguredError(
                f"The stored {spec.name} key can't be decrypted (the server's secret key "
                "changed). Enter the key again."
            ) from exc
    api_base = credential.api_base or spec.default_api_base
    if api_base is None:
        raise ModelNotConfiguredError(f"{spec.name} has no API base URL configured.")
    return ProviderAccess(
        provider=spec.id, api_base=api_base.rstrip("/"), api_key=api_key, source="workspace"
    )


async def resolve_access(
    session: AsyncSession,
    settings: Settings,
    vault: SecretBox | None,
    workspace_id: uuid.UUID,
    provider: str,
) -> ProviderAccess | None:
    """The workspace's own credential if it has one, else the deployment's, else None."""
    spec = get_provider(provider)
    if spec is None:
        return None
    credential = await get_credential(session, workspace_id, provider)
    if credential is not None:
        return access_from_credential(spec, credential, vault)
    deployment = deployment_access(spec, settings)
    if deployment is None:
        return None
    api_base, api_key = deployment
    return ProviderAccess(provider=spec.id, api_base=api_base, api_key=api_key, source="deployment")


async def resolve_route(
    session: AsyncSession,
    settings: Settings,
    vault: SecretBox | None,
    workspace_id: uuid.UUID,
    role: ModelRole,
) -> ModelRoute:
    preference = await session.get(ModelPreference, (workspace_id, role))
    if preference is None:
        raise ModelNotConfiguredError(
            f"No model is selected for the {role} role. "
            f"Choose one with PUT /api/v1/model-preferences/{role}."
        )
    access = await resolve_access(session, settings, vault, workspace_id, preference.provider)
    if access is None:
        raise ModelNotConfiguredError(
            f"The {role} role uses {preference.provider}, which has no credentials. "
            f"Add them with PUT /api/v1/providers/{preference.provider}/credential."
        )
    return ModelRoute(access=access, model=preference.model)
