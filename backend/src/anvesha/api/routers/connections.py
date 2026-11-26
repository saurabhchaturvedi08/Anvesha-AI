from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Response, status
from pydantic import BaseModel, Field

from anvesha.api.deps import PrincipalDep, SessionDep, SettingsDep
from anvesha.mcp import oauth

router = APIRouter(prefix="/api/v1/connections", tags=["access tokens"])


class ConnectionRead(BaseModel):
    client_id: str
    client_name: str | None = Field(description="The name the app registered with (unverified).")
    redirect_hosts: list[str] = Field(description="Where the app receives sign-in results.")
    connected_at: datetime
    last_used_at: datetime | None


class McpInfo(BaseModel):
    enabled: bool
    oauth_enabled: bool = Field(description="Whether claude.ai / ChatGPT style sign-in is on.")
    public_url: str | None = Field(description="The MCP endpoint internet clients use.")


@router.get("", response_model=list[ConnectionRead])
async def list_connections(session: SessionDep, principal: PrincipalDep) -> list[ConnectionRead]:
    """Apps approved through OAuth sign-in (claude.ai, ChatGPT...), newest first."""
    return [
        ConnectionRead(**connection.__dict__)
        for connection in await oauth.list_connections(session, principal)
    ]


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    client_id: Annotated[str, Path(max_length=64)], session: SessionDep, principal: PrincipalDep
) -> Response:
    """Revoke an app's access. It has to be approved again to reconnect."""
    await oauth.disconnect(session, principal, client_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/mcp", response_model=McpInfo)
async def mcp_info(settings: SettingsDep) -> McpInfo:
    """How MCP clients reach this server (shown in the web app's settings)."""
    return McpInfo(
        enabled=settings.mcp_enabled,
        oauth_enabled=settings.mcp_oauth_enabled,
        public_url=f"{settings.mcp_public_url}/mcp" if settings.mcp_oauth_enabled else None,
    )
