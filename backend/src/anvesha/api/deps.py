from collections.abc import AsyncIterator
from typing import Annotated, cast

import httpx2
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.config import Settings
from anvesha.crypto import SecretBox
from anvesha.db.session import Database
from anvesha.llm.gateway import LLMGateway
from anvesha.tenancy import Principal


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


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DatabaseDep = Annotated[Database, Depends(get_database)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
VaultDep = Annotated[SecretBox | None, Depends(get_vault)]
HttpDep = Annotated[httpx2.AsyncClient, Depends(get_http)]
GatewayDep = Annotated[LLMGateway, Depends(get_gateway)]
