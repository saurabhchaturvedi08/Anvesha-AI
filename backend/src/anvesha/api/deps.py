from collections.abc import AsyncIterator
from typing import Annotated, cast

import httpx2
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.session import Database
from anvesha.events.broker import RunEventBroker
from anvesha.llm.gateway import LLMGateway
from anvesha.sources.service import SourceService
from anvesha.storage import FileStorage
from anvesha.tenancy import Principal
from anvesha.tools import ToolRegistry
from anvesha.worker.queue import JobQueue


def get_settings_dep(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.db)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_database(request).session() as session:
        yield session


def get_principal(request: Request) -> Principal:
    # Local auth mode: everyone is the built-in user. JWT verification replaces this later.
    return cast(Principal, request.app.state.local_principal)


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
