"""Approving an MCP client's sign-in from the web app (multi-user mode).

When claude.ai or ChatGPT asks to connect, Anvesha's authorization endpoint sends the
user to the web app's /connect page with the pending request sealed into the URL. That
page, where the user is signed in, shows what is asking and calls these endpoints; the
connection is made for the signed-in user in their current workspace.
"""

from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request
from mcp.server.auth.provider import construct_redirect_uri
from pydantic import BaseModel, ConfigDict, Field

from anvesha.api.deps import PrincipalDep, SettingsDep
from anvesha.errors import InvalidInputError, NotConfiguredError
from anvesha.mcp.oauth import AnveshaOAuthProvider, ConsentRequest

router = APIRouter(prefix="/api/v1/oauth", tags=["connections"])


class ConsentDetails(BaseModel):
    client_id: str
    client_name: str | None
    # Where the user returns after deciding: shown so they can tell who is asking.
    redirect_host: str
    expires_at: datetime


class ConsentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=8000)
    decision: Literal["allow", "deny"]


class ConsentResult(BaseModel):
    redirect_url: str = Field(description="Send the browser here to finish.")


def _provider(request: Request, settings: SettingsDep) -> AnveshaOAuthProvider:
    provider = request.app.state.oauth_provider
    if provider is None or settings.auth_mode != "jwt":
        # Single-user mode approves on the public app's own page, with the approval secret.
        raise NotConfiguredError(
            "Connections are approved here only in multi-user mode with OAuth sign-in on "
            "(ANVESHA_AUTH_MODE=jwt and ANVESHA_MCP_PUBLIC_URL)."
        )
    return cast(AnveshaOAuthProvider, provider)


async def _load(provider: AnveshaOAuthProvider, sealed: str) -> tuple[ConsentRequest, str | None]:
    loaded = await provider.load_consent(sealed)
    if loaded is None:
        raise InvalidInputError(
            "This connection request has expired or isn't valid. Start connecting again "
            "from the app you were using.",
            code="invalid_consent_request",
        )
    pending, client = loaded
    return pending, client.client_name


@router.get("/consent", response_model=ConsentDetails)
async def consent_details(
    request: Request,
    settings: SettingsDep,
    principal: PrincipalDep,  # signed in
    sealed: str = Query(alias="request", min_length=1, max_length=8000),
) -> ConsentDetails:
    pending, client_name = await _load(_provider(request, settings), sealed)
    redirect = urlsplit(pending.redirect_uri)
    return ConsentDetails(
        client_id=pending.client_id,
        client_name=client_name,
        redirect_host=redirect.netloc or f"{redirect.scheme}:",
        expires_at=datetime.fromtimestamp(pending.expires_at, UTC),
    )


@router.post("/consent", response_model=ConsentResult)
async def decide_consent(
    payload: ConsentDecision, request: Request, settings: SettingsDep, principal: PrincipalDep
) -> ConsentResult:
    """Approve (for you, in your current workspace) or deny the connection."""
    provider = _provider(request, settings)
    pending, _ = await _load(provider, payload.request)
    if payload.decision == "deny":
        return ConsentResult(
            redirect_url=construct_redirect_uri(
                pending.redirect_uri,
                error="access_denied",
                error_description="The user denied access.",
                state=pending.state,
            )
        )
    code = await provider.create_code(pending, principal)
    return ConsentResult(
        redirect_url=construct_redirect_uri(pending.redirect_uri, code=code, state=pending.state)
    )
