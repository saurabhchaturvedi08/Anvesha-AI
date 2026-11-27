import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any, cast

import httpx2
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.auth import JwtVerifier
from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.session import Database
from anvesha.errors import InvalidInputError, UnauthenticatedError
from anvesha.events.broker import RunEventBroker
from anvesha.llm.gateway import LLMGateway
from anvesha.ratelimit import Bucket, check_rate_limit
from anvesha.sources.service import SourceService
from anvesha.storage import FileStorage
from anvesha.tenancy import Principal, ensure_user, principal_for, require_role
from anvesha.tools import ToolRegistry
from anvesha.worker.queue import JobQueue


def get_settings_dep(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.db)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_database(request).session() as session:
        yield session


WORKSPACE_HEADER = "X-Anvesha-Workspace"


def _requested_workspace(request: Request) -> uuid.UUID | None:
    value = request.headers.get(WORKSPACE_HEADER, "").strip()
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise InvalidInputError(f"{WORKSPACE_HEADER} must be a workspace id.") from exc


async def get_principal(request: Request) -> Principal:
    """The signed-in user (or the built-in one) in the workspace the request names.

    Clients pick a workspace with the X-Anvesha-Workspace header; without it, requests
    act in the user's oldest workspace (their personal one).
    """
    state = request.app.state
    settings: Settings = state.settings
    workspace_id = _requested_workspace(request)
    if settings.auth_mode == "local":
        local = cast(Principal, state.local_principal)
        if workspace_id is None or workspace_id == local.workspace_id:
            return local
        async with get_database(request).session() as session:
            return await principal_for(session, local.user_id, workspace_id)

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthenticatedError("Sign in first.")
    verifier = cast(JwtVerifier, state.jwt_verifier)
    identity = await verifier.verify(token.strip())
    async with get_database(request).session() as session:
        user_id = await ensure_user(
            session, subject=identity.subject, email=identity.email, name=identity.name
        )
        return await principal_for(session, user_id, workspace_id)


def get_vault(request: Request) -> SecretBox | None:
    return cast(SecretBox | None, request.app.state.vault)


def get_http(request: Request) -> httpx2.AsyncClient:
    return cast(httpx2.AsyncClient, request.app.state.http)


def get_gateway(request: Request) -> LLMGateway:
    return cast(LLMGateway, request.app.state.gateway)


def get_storage(request: Request) -> FileStorage:
    return cast(FileStorage, request.app.state.storage)


def get_source_service(request: Request) -> SourceService:
    return cast(SourceService, request.app.state.sources)


def get_tools(request: Request) -> ToolRegistry:
    return cast(ToolRegistry, request.app.state.tools)


def get_queue(request: Request) -> JobQueue:
    return cast(JobQueue, request.app.state.queue)


def get_broker(request: Request) -> RunEventBroker:
    return cast(RunEventBroker, request.app.state.broker)


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DatabaseDep = Annotated[Database, Depends(get_database)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
VaultDep = Annotated[SecretBox | None, Depends(get_vault)]
HttpDep = Annotated[httpx2.AsyncClient, Depends(get_http)]
GatewayDep = Annotated[LLMGateway, Depends(get_gateway)]
StorageDep = Annotated[FileStorage, Depends(get_storage)]
SourceServiceDep = Annotated[SourceService, Depends(get_source_service)]
ToolsDep = Annotated[ToolRegistry, Depends(get_tools)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
BrokerDep = Annotated[RunEventBroker, Depends(get_broker)]
def rate_limit(bucket: Bucket) -> Any:
    """A route dependency counting the request against a rate limit bucket."""

    async def check(request: Request, principal: PrincipalDep) -> None:
        await check_rate_limit(get_database(request), request.app.state.settings, bucket, principal)

    return Depends(check)


def admin_principal(principal: PrincipalDep) -> Principal:
    require_role(principal, "admin")
    return principal



# For changes to the workspace's shared setup (keys, models, members): admins and owners.
AdminDep = Annotated[Principal, Depends(admin_principal)]
