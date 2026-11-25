import uuid
from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field

from anvesha import access_tokens
from anvesha.api.deps import PrincipalDep, SessionDep
from anvesha.db.models import AccessToken

router = APIRouter(prefix="/api/v1/access-tokens", tags=["access tokens"])


class AccessTokenCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=100, examples=["Claude Code on my laptop"])
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650, description="Leave empty for a token that doesn't expire."
    )


class AccessTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str = Field(description="The token's first characters, to recognise it by.")
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


class AccessTokenCreated(AccessTokenRead):
    token: str = Field(description="The token itself. It is shown only this once.")


@router.get("", response_model=list[AccessTokenRead])
async def list_access_tokens(session: SessionDep, principal: PrincipalDep) -> Sequence[AccessToken]:
    """Tokens that let MCP clients (Claude Code, Cursor, Gemini CLI...) use this workspace."""
    return await access_tokens.list_access_tokens(session, principal)


@router.post("", response_model=AccessTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_access_token(
    payload: AccessTokenCreate, session: SessionDep, principal: PrincipalDep
) -> AccessTokenCreated:
    """Create a token. Send it as ``Authorization: Bearer <token>`` to ``/mcp``."""
    row, token = await access_tokens.create_access_token(
        session, principal, name=payload.name, expires_in_days=payload.expires_in_days
    )
    return AccessTokenCreated(**AccessTokenRead.model_validate(row).model_dump(), token=token)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_access_token(
    token_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    """Revoke a token. Clients using it are refused from their next request."""
    await access_tokens.revoke_access_token(session, principal, token_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
