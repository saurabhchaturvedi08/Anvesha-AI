from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from anvesha.api.deps import PrincipalDep, SessionDep, SettingsDep, VaultDep
from anvesha.api.schemas.llm import ModelPreferenceRead, ModelPreferenceWrite
from anvesha.db.models import ModelPreference
from anvesha.llm.catalog import get_provider
from anvesha.llm.credentials import resolve_access
from anvesha.llm.types import MODEL_ROLES, ModelRole

router = APIRouter(prefix="/api/v1/model-preferences", tags=["model preferences"])


@router.get("", response_model=list[ModelPreferenceRead])
async def list_model_preferences(
    session: SessionDep, principal: PrincipalDep
) -> list[ModelPreferenceRead]:
    rows = await session.scalars(
        select(ModelPreference).where(ModelPreference.workspace_id == principal.workspace_id)
    )
    by_role = {row.role: row for row in rows}
    return [
        ModelPreferenceRead(
            role=role,
            provider=by_role[role].provider if role in by_role else None,
            model=by_role[role].model if role in by_role else None,
        )
        for role in MODEL_ROLES
    ]


@router.put("/{role}", response_model=ModelPreferenceRead)
async def set_model_preference(
    role: ModelRole,
    payload: ModelPreferenceWrite,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    vault: VaultDep,
) -> ModelPreferenceRead:
    spec = get_provider(payload.provider)
    if spec is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown provider {payload.provider!r}"
        )
    if role == "embedding" and spec.api_style != "openai":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{spec.name} has no embeddings API. Choose another provider for embeddings.",
        )
    if await resolve_access(session, settings, vault, principal.workspace_id, spec.id) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{spec.name} isn't configured. Add credentials with "
            f"PUT /api/v1/providers/{spec.id}/credential first.",
        )
    preference = await session.get(ModelPreference, (principal.workspace_id, role))
    if preference is None:
        preference = ModelPreference(workspace_id=principal.workspace_id, role=role)
        session.add(preference)
    preference.provider = spec.id
    preference.model = payload.model
    await session.commit()
    return ModelPreferenceRead(role=role, provider=spec.id, model=payload.model)


@router.delete("/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_model_preference(
    role: ModelRole, session: SessionDep, principal: PrincipalDep
) -> Response:
    preference = await session.get(ModelPreference, (principal.workspace_id, role))
    if preference is not None:
        await session.delete(preference)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
