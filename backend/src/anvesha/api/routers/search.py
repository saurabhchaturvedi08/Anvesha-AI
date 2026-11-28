from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status

from anvesha.api.deps import (
    AdminDep,
    DatabaseDep,
    HttpDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    VaultDep,
    rate_limit,
)
from anvesha.api.schemas.search import (
    ScholarSearchRequest,
    ScholarSearchResponseRead,
    ScholarSourceRead,
    SearchCredentialWrite,
    SearchProviderRead,
    WebSearchRequest,
    WebSearchResponseRead,
)
from anvesha.db.models import ProviderCredential
from anvesha.errors import AppError
from anvesha.llm.credentials import credential_aad, get_credential, key_hint
from anvesha.scholar.providers import DEFAULT_SCHOLAR_SOURCES, SCHOLAR_SOURCES
from anvesha.scholar.service import scholar_search
from anvesha.search.providers import (
    SEARCH_PROVIDERS,
    SearchAccess,
    SearchError,
    deployment_search_access,
    run_search,
)
from anvesha.search.service import choose_search_access, get_search_provider, web_search

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.get("/providers", response_model=list[SearchProviderRead])
async def list_search_providers(
    session: SessionDep, principal: PrincipalDep, settings: SettingsDep, vault: VaultDep
) -> list[SearchProviderRead]:
    try:
        default_spec, _ = await choose_search_access(
            session, settings, vault, principal.workspace_id, None
        )
        default_id: str | None = default_spec.id
    except AppError:  # nothing configured, or a stored key can't be decrypted
        default_id = None
    result = []
    for spec in SEARCH_PROVIDERS.values():
        credential = (
            await get_credential(session, principal.workspace_id, spec.id)
            if spec.workspace_configurable
            else None
        )
        has_deployment = deployment_search_access(spec, settings) is not None
        result.append(
            SearchProviderRead(
                id=spec.id,
                name=spec.name,
                requires_api_key=spec.requires_api_key,
                workspace_configurable=spec.workspace_configurable,
                free=spec.free,
                docs_url=spec.docs_url,
                configured=credential is not None or has_deployment,
                source="workspace" if credential else "deployment" if has_deployment else None,
                status=credential.status if credential else None,
                key_hint=credential.key_hint if credential else None,
                last_checked_at=credential.last_checked_at if credential else None,
                last_error=credential.last_error if credential else None,
                is_default=spec.id == default_id,
            )
        )
    return result


@router.put("/providers/{provider}/credential", response_model=SearchProviderRead)
async def save_search_credential(
    provider: str,
    payload: SearchCredentialWrite,
    session: SessionDep,
    principal: AdminDep,
    settings: SettingsDep,
    vault: VaultDep,
    http: HttpDep,
) -> SearchProviderRead:
    """Save a workspace's own key for a paid search API, after a one-result test search."""
    spec = get_search_provider(provider)
    if not spec.workspace_configurable or spec.default_api_base is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{spec.name} is configured by the server operator, not per workspace.",
        )
    if vault is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Keys can't be stored: no secret key is configured (set ANVESHA_SECRET_KEY).",
        )
    api_key = payload.api_key.get_secret_value().strip()
    access = SearchAccess(spec.id, spec.default_api_base, api_key, "workspace")
    check_status, check_error = "valid", None
    try:
        await run_search(http, spec, access, "open source research", 1, settings)
    except SearchError as exc:
        if exc.code == "search_auth_failed":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{spec.name} rejected the key."
            ) from exc
        # Rate limits or outages don't prove the key is bad; keep it, marked unverified.
        check_status, check_error = "unverified", str(exc)

    credential = await get_credential(session, principal.workspace_id, spec.id)
    if credential is None:
        credential = ProviderCredential(workspace_id=principal.workspace_id, provider=spec.id)
        session.add(credential)
    credential.encrypted_api_key = vault.encrypt(
        api_key, credential_aad(principal.workspace_id, spec.id)
    )
    credential.key_hint = key_hint(api_key)
    credential.api_base = None
    credential.status = check_status
    credential.last_error = check_error
    credential.last_checked_at = datetime.now(UTC)
    await session.commit()
    providers = await list_search_providers(session, principal, settings, vault)
    return next(p for p in providers if p.id == spec.id)


@router.delete("/providers/{provider}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search_credential(
    provider: str, session: SessionDep, principal: AdminDep
) -> Response:
    spec = get_search_provider(provider)
    credential = await get_credential(session, principal.workspace_id, spec.id)
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No saved key for {spec.name}.")
    await session.delete(credential)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/web", response_model=WebSearchResponseRead, dependencies=[rate_limit("tools")])
async def search_web(
    payload: WebSearchRequest,
    db: DatabaseDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    vault: VaultDep,
    http: HttpDep,
) -> WebSearchResponseRead:
    response = await web_search(
        db=db,
        http=http,
        settings=settings,
        vault=vault,
        workspace_id=principal.workspace_id,
        query=payload.query,
        limit=payload.limit,
        provider=payload.provider,
    )
    return WebSearchResponseRead.model_validate(response, from_attributes=True)


@router.get("/scholarly/sources", response_model=list[ScholarSourceRead])
async def list_scholar_sources() -> list[ScholarSourceRead]:
    return [
        ScholarSourceRead(
            id=spec.id,
            name=spec.name,
            description=spec.description,
            default=spec.id in DEFAULT_SCHOLAR_SOURCES,
        )
        for spec in SCHOLAR_SOURCES.values()
    ]


@router.post(
    "/scholarly",
    response_model=ScholarSearchResponseRead,
    dependencies=[rate_limit("tools")],
)
async def search_scholarly(
    payload: ScholarSearchRequest, settings: SettingsDep, http: HttpDep
) -> ScholarSearchResponseRead:
    response = await scholar_search(
        http=http,
        settings=settings,
        query=payload.query,
        sources=payload.sources,
        limit=payload.limit,
        years=(payload.year_from, payload.year_to),
    )
    return ScholarSearchResponseRead.model_validate(response, from_attributes=True)
