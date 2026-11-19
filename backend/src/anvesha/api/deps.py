from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.db.session import Database
from anvesha.tenancy import Principal


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.db)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_database(request).session() as session:
        yield session


def get_principal(request: Request) -> Principal:
    # Local auth mode: everyone is the built-in user. JWT verification replaces this later.
    return cast(Principal, request.app.state.local_principal)


DatabaseDep = Annotated[Database, Depends(get_database)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
