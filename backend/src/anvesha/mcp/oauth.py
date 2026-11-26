"""OAuth 2.1 sign-in for MCP clients (claude.ai, ChatGPT, and others that use connectors).

Anvesha is its own authorization server. The MCP SDK serves the protocol endpoints
(``/authorize``, ``/token``, ``/register``, ``/revoke`` and the metadata documents) and
checks PKCE, redirect URIs and grant ownership; this module stores clients, codes and
tokens, and asks the user for consent.

Consent: ``/authorize`` sends the browser to ``/oauth/consent``. The pending request
travels in the URL, sealed (encrypted and authenticated) with the deployment's master
key and valid for ten minutes, so nothing is stored until the user approves. In
single-user mode there are no accounts to sign in to, so approving asks for the
operator's approval secret (``ANVESHA_MCP_APPROVAL_SECRET``); repeated wrong guesses
lock approval for a while.

Tokens: codes are single-use and live five minutes. Access tokens are rows in
``access_tokens`` tied to the client (an hour by default); refresh tokens are replaced on
every use. Only hashes of codes and tokens are stored.
"""

import hmac
import html
import json
import logging
import secrets
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken as McpAccessToken,
)
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import (
    build_resource_metadata_url,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl, BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from anvesha.access_tokens import DISPLAY_CHARS, generate_token, hash_token, verify_access_token
from anvesha.config import Settings
from anvesha.crypto import DecryptionError, SecretBox
from anvesha.db.models import AccessToken, OAuthAuthorizationCode, OAuthClient, OAuthRefreshToken
from anvesha.db.session import Database
from anvesha.errors import NotFoundError
from anvesha.tenancy import Principal

logger = logging.getLogger(__name__)

CONSENT_PATH = "/oauth/consent"
CONSENT_TTL_SECONDS = 10 * 60
CODE_TTL_SECONDS = 5 * 60
REFRESH_PREFIX = "anvr_"
_CONSENT_AAD = "oauth-consent"
_MAX_CLIENT_NAME = 200


def client_secret_aad(client_id: str) -> str:
    return f"oauth-client:{client_id}"


@dataclass(frozen=True)
class OAuthDeps:
    settings: Settings
    db: Database
    vault: SecretBox
    # Who approves sign-ins. In single-user mode, the built-in user.
    principal: Principal


# Grants carry the workspace and user they were approved for.
class StoredCode(AuthorizationCode):
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    code_hash: str


class StoredRefreshToken(RefreshToken):
    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    access_token_id: uuid.UUID | None


class StoredAccessToken(McpAccessToken):
    id: uuid.UUID


class ApprovalThrottle:
    """Locks approval after too many wrong secrets in a window (per process)."""

    def __init__(self, max_failures: int = 10, window_seconds: float = 15 * 60) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._failures and now - self._failures[0] > self.window_seconds:
            self._failures.popleft()

    def locked(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._prune(now)
        return len(self._failures) >= self.max_failures

    def fail(self, now: float | None = None) -> None:
        self._failures.append(time.monotonic() if now is None else now)

    def reset(self) -> None:
        self._failures.clear()


class AnveshaOAuthProvider(
    OAuthAuthorizationServerProvider[StoredCode, StoredRefreshToken, StoredAccessToken]
):
    """Implements the MCP SDK's ``OAuthAuthorizationServerProvider`` on Postgres."""

    def __init__(self, deps: Callable[[], OAuthDeps]) -> None:
        self.deps = deps
        self.throttle = ApprovalThrottle()

    # --- Clients --------------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if len(client_id) > 64:
            return None
        deps = self.deps()
        async with deps.db.session() as session:
            row = await session.get(OAuthClient, client_id)
        if row is None:
            return None
        secret = (
            deps.vault.decrypt(row.secret_encrypted, client_secret_aad(client_id))
            if row.secret_encrypted
            else None
        )
        return OAuthClientInformationFull.model_validate(
            {**row.client_metadata, "client_id": client_id, "client_secret": secret}
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        deps = self.deps()
        metadata = client_info.model_dump(
            mode="json", exclude={"client_id", "client_secret"}, exclude_none=True
        )
        name = (client_info.client_name or "").strip()[:_MAX_CLIENT_NAME] or None
        async with deps.db.session() as session:
            session.add(
                OAuthClient(
                    client_id=client_info.client_id,
                    client_name=name,
                    client_metadata=metadata,
                    secret_encrypted=(
                        deps.vault.encrypt(
                            client_info.client_secret, client_secret_aad(client_info.client_id)
                        )
                        if client_info.client_secret
                        else None
                    ),
                )
            )
            await session.commit()

    # --- Authorization ----------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        deps = self.deps()
        sealed = seal_consent_request(deps.vault, client.client_id, params)
        return f"{deps.settings.mcp_public_url}{CONSENT_PATH}?{urlencode({'request': sealed})}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> StoredCode | None:
        if len(authorization_code) > 200:
            return None
        async with self.deps().db.session() as session:
            row = await session.get(OAuthAuthorizationCode, hash_token(authorization_code))
        if row is None or row.client_id != client.client_id:
            return None
        return StoredCode(
            code=authorization_code,
            code_hash=row.code_hash,
            scopes=list(row.scopes),
            expires_at=row.expires_at.timestamp(),
            client_id=row.client_id,
            code_challenge=row.code_challenge,
            redirect_uri=AnyUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
            resource=row.resource,
            subject=str(row.user_id),
            workspace_id=row.workspace_id,
            user_id=row.user_id,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: StoredCode
    ) -> OAuthToken:
        async with self.deps().db.session() as session:
            used = await session.execute(
                delete(OAuthAuthorizationCode).where(
                    OAuthAuthorizationCode.code_hash == authorization_code.code_hash
                )
            )
            if used.rowcount == 0:  # type: ignore[attr-defined]
                # Exchanged a moment ago by a concurrent request: codes work once.
                raise TokenError("invalid_grant", "authorization code does not exist")
            token = await self._issue(
                session,
                client,
                Principal(
                    user_id=authorization_code.user_id,
                    workspace_id=authorization_code.workspace_id,
                ),
                authorization_code.scopes,
                authorization_code.resource,
            )
            await session.commit()
        return token

    # --- Refresh --------------------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> StoredRefreshToken | None:
        if not refresh_token.startswith(REFRESH_PREFIX) or len(refresh_token) > 200:
            return None
        async with self.deps().db.session() as session:
            row = await session.scalar(
                select(OAuthRefreshToken).where(
                    OAuthRefreshToken.token_hash == hash_token(refresh_token)
                )
            )
        if row is None or row.client_id != client.client_id:
            return None
        return StoredRefreshToken(
            token=refresh_token,
            client_id=row.client_id,
            scopes=list(row.scopes),
            expires_at=int(row.expires_at.timestamp()),
            resource=row.resource,
            subject=str(row.user_id),
            id=row.id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            access_token_id=row.access_token_id,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: StoredRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        async with self.deps().db.session() as session:
            used = await session.execute(
                delete(OAuthRefreshToken).where(OAuthRefreshToken.id == refresh_token.id)
            )
            if used.rowcount == 0:  # type: ignore[attr-defined]
                raise TokenError("invalid_grant", "refresh token does not exist")
            if refresh_token.access_token_id is not None:
                await session.execute(
                    delete(AccessToken).where(AccessToken.id == refresh_token.access_token_id)
                )
            token = await self._issue(
                session,
                client,
                Principal(user_id=refresh_token.user_id, workspace_id=refresh_token.workspace_id),
                scopes,
                refresh_token.resource,
            )
            await session.commit()
        return token

    # --- Access tokens and revocation -------------------------------------------------------

    async def load_access_token(self, token: str) -> StoredAccessToken | None:
        verified = await verify_access_token(self.deps().db, token, track_use=False)
        if verified is None:
            return None
        return StoredAccessToken(
            token=token,
            id=verified.token_id,
            # Personal tokens belong to no client, so no client can revoke them.
            client_id=verified.client_id or "",
            scopes=list(verified.scopes),
            expires_at=int(verified.expires_at.timestamp()) if verified.expires_at else None,
            resource=verified.resource,
            subject=str(verified.principal.user_id),
        )

    async def revoke_token(self, token: StoredAccessToken | StoredRefreshToken) -> None:
        async with self.deps().db.session() as session:
            if isinstance(token, StoredRefreshToken):
                await session.execute(
                    delete(OAuthRefreshToken).where(OAuthRefreshToken.id == token.id)
                )
                if token.access_token_id is not None:
                    await session.execute(
                        delete(AccessToken).where(AccessToken.id == token.access_token_id)
                    )
            else:
                await session.execute(
                    delete(OAuthRefreshToken).where(OAuthRefreshToken.access_token_id == token.id)
                )
                await session.execute(
                    delete(AccessToken).where(
                        AccessToken.id == token.id, AccessToken.client_id.is_not(None)
                    )
                )
            await session.commit()

    async def _issue(
        self,
        session: AsyncSession,
        client: OAuthClientInformationFull,
        principal: Principal,
        scopes: Sequence[str],
        resource: str | None,
    ) -> OAuthToken:
        settings = self.deps().settings
        now = datetime.now(UTC)
        access = generate_token()
        access_lifetime = timedelta(minutes=settings.mcp_oauth_access_token_minutes)
        row = AccessToken(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            client_id=client.client_id,
            name=(client.client_name or "OAuth client")[:100],
            token_hash=hash_token(access),
            prefix=access[:DISPLAY_CHARS],
            scopes=list(scopes),
            resource=resource,
            expires_at=now + access_lifetime,
        )
        session.add(row)
        await session.flush()
        refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
        session.add(
            OAuthRefreshToken(
                token_hash=hash_token(refresh),
                client_id=client.client_id,
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
                access_token_id=row.id,
                scopes=list(scopes),
                resource=resource,
                expires_at=now + timedelta(days=settings.mcp_oauth_refresh_token_days),
            )
        )
        return OAuthToken(
            access_token=access,
            expires_in=int(access_lifetime.total_seconds()),
            scope=" ".join(scopes) or None,
            refresh_token=refresh,
        )

    async def create_code(self, request: "ConsentRequest", principal: Principal) -> str:
        code = secrets.token_urlsafe(32)
        async with self.deps().db.session() as session:
            session.add(
                OAuthAuthorizationCode(
                    code_hash=hash_token(code),
                    client_id=request.client_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    scopes=request.scopes,
                    code_challenge=request.code_challenge,
                    redirect_uri=request.redirect_uri,
                    redirect_uri_provided_explicitly=request.redirect_uri_provided_explicitly,
                    resource=request.resource,
                    expires_at=datetime.now(UTC) + timedelta(seconds=CODE_TTL_SECONDS),
                )
            )
            await session.commit()
        return code


# --- The pending request, sealed into the consent page's URL -----------------------------


@dataclass(frozen=True)
class ConsentRequest:
    client_id: str
    scopes: list[str]
    code_challenge: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    state: str | None
    resource: str | None
    expires_at: float


def seal_consent_request(
    vault: SecretBox, client_id: str, params: AuthorizationParams, *, now: float | None = None
) -> str:
    payload = {
        "client_id": client_id,
        "scopes": params.scopes or [],
        "code_challenge": params.code_challenge,
        "redirect_uri": str(params.redirect_uri),
        "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
        "state": params.state,
        "resource": params.resource,
        "expires_at": (time.time() if now is None else now) + CONSENT_TTL_SECONDS,
    }
    return vault.encrypt(json.dumps(payload), _CONSENT_AAD)


def open_consent_request(vault: SecretBox, sealed: str) -> ConsentRequest | None:
    """The request, or None if it was tampered with or has expired."""
    if not sealed or len(sealed) > 8000:
        return None
    try:
        payload = json.loads(vault.decrypt(sealed, _CONSENT_AAD))
        request = ConsentRequest(**payload)
    except (DecryptionError, ValueError, TypeError):
        return None
    return request if request.expires_at > time.time() else None


# --- The consent page ----------------------------------------------------------------------


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{title} · Anvesha</title>
<style>
:root {{ color-scheme: light dark; --fg: #1c1b19; --muted: #66625b; --line: #d9d5cd;
  --bg: #faf8f4; --card: #fff; --accent: #2f5d50; --warn: #8a4b0f; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg: #ece9e3; --muted: #a39e95;
  --line: #3a3833; --bg: #161513; --card: #1f1e1b; --accent: #7fb7a4; --warn: #e0a463; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px;
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--fg);
  background: var(--bg); }}
main {{ width: 100%; max-width: 440px; background: var(--card); border: 1px solid var(--line);
  border-radius: 12px; padding: 28px; }}
.brand {{ font-weight: 600; letter-spacing: .02em; color: var(--muted); font-size: 13px; }}
h1 {{ font-size: 20px; line-height: 1.3; margin: 8px 0 14px; }}
p, li {{ margin: 0 0 10px; }}
ul {{ padding-left: 20px; margin: 0 0 16px; }}
.muted {{ color: var(--muted); font-size: 13px; }}
.where {{ font-family: ui-monospace, monospace; font-size: 13px; overflow-wrap: anywhere; }}
.error {{ color: var(--warn); font-weight: 500; }}
label {{ display: block; font-weight: 500; margin: 18px 0 6px; }}
input[type=password] {{ width: 100%; padding: 9px 11px; border: 1px solid var(--line);
  border-radius: 8px; background: transparent; color: inherit; font: inherit; }}
.actions {{ display: flex; gap: 10px; margin-top: 18px; }}
button {{ flex: 1; padding: 10px; border-radius: 8px; font: inherit; font-weight: 600;
  cursor: pointer; border: 1px solid var(--line); background: transparent; color: inherit; }}
button.primary {{ background: var(--accent); border-color: var(--accent); color: var(--card); }}
button:focus-visible, input:focus-visible {{ outline: 2px solid var(--accent);
  outline-offset: 2px; }}
</style></head>
<body><main><div class="brand">ANVESHA</div>{body}</main></body></html>"""


def _page(
    title: str, body: str, *, status: int = 200, form_target: str | None = None
) -> HTMLResponse:
    # The browser follows the form's redirect to the client's callback, so form-action
    # has to allow that origin too (Chromium enforces form-action on redirects).
    form_action = "'self'" + (f" {form_target}" if form_target else "")
    return HTMLResponse(
        _PAGE.format(title=html.escape(title), body=body),
        status_code=status,
        headers={
            "Content-Security-Policy": (
                f"default-src 'none'; style-src 'unsafe-inline'; form-action {form_action}; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _origin(uri: str) -> str | None:
    parts = urlsplit(uri)
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    # Native apps use their own scheme (e.g. cursor://); CSP allows a scheme source.
    return f"{parts.scheme}:" if parts.scheme and parts.scheme.isascii() else None


def _invalid_request_page() -> HTMLResponse:
    return _page(
        "Sign-in link expired",
        "<h1>This sign-in link has expired</h1><p>Start connecting again from the app you "
        "were using. Links work for ten minutes.</p>",
        status=400,
    )


class ConsentEndpoints:
    def __init__(self, provider: AnveshaOAuthProvider) -> None:
        self.provider = provider

    async def _load(self, sealed: str) -> tuple[ConsentRequest, OAuthClientInformationFull] | None:
        deps = self.provider.deps()
        request = open_consent_request(deps.vault, sealed)
        if request is None:
            return None
        client = await self.provider.get_client(request.client_id)
        return None if client is None else (request, client)

    def _render(
        self,
        sealed: str,
        request: ConsentRequest,
        client: OAuthClientInformationFull,
        *,
        error: str | None = None,
        status: int = 200,
    ) -> HTMLResponse:
        name = html.escape(client.client_name or "An unnamed application")
        redirect = urlsplit(request.redirect_uri)
        where = html.escape(redirect.netloc or f"{redirect.scheme}:")
        error_html = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        body = f"""
<h1>{name} wants to use Anvesha for you</h1>
<p>If you approve, it can:</p>
<ul>
<li>start and steer research runs, which spend your model credits within each run's limit</li>
<li>read your projects, reports, sources and evidence</li>
<li>search the web and literature, and save pages to your projects</li>
</ul>
<p class="muted">After approval you'll return to <span class="where">{where}</span>.
The app named itself; only approve if you started connecting it just now.</p>
<form method="post" action="{CONSENT_PATH}">
<input type="hidden" name="request" value="{html.escape(sealed)}">
{error_html}
<label for="secret">Approval secret</label>
<input id="secret" name="secret" type="password" autocomplete="current-password" autofocus>
<p class="muted">Set by whoever runs this server, as ANVESHA_MCP_APPROVAL_SECRET.</p>
<div class="actions">
<button type="submit" name="decision" value="deny">Deny</button>
<button type="submit" name="decision" value="allow" class="primary">Approve</button>
</div>
</form>"""
        return _page(
            "Approve connection",
            body,
            status=status,
            form_target=_origin(request.redirect_uri),
        )

    async def show(self, request: Request) -> Response:
        sealed = request.query_params.get("request", "")
        loaded = await self._load(sealed)
        if loaded is None:
            return _invalid_request_page()
        return self._render(sealed, *loaded)

    async def decide(self, request: Request) -> Response:
        form = await request.form()
        sealed = str(form.get("request") or "")
        loaded = await self._load(sealed)
        if loaded is None:
            return _invalid_request_page()
        pending, client = loaded
        if form.get("decision") != "allow":
            return RedirectResponse(
                construct_redirect_uri(
                    pending.redirect_uri,
                    error="access_denied",
                    error_description="The user denied access.",
                    state=pending.state,
                ),
                status_code=303,
            )
        throttle = self.provider.throttle
        if throttle.locked():
            return self._render(
                sealed,
                pending,
                client,
                error="Too many wrong attempts. Approval is paused for 15 minutes.",
                status=429,
            )
        deps = self.provider.deps()
        expected = deps.settings.mcp_approval_secret
        given = str(form.get("secret") or "")
        if expected is None or not hmac.compare_digest(
            given.encode(), expected.get_secret_value().encode()
        ):
            throttle.fail()
            logger.warning(
                "OAuth approval refused: wrong approval secret (client %s)", client.client_id
            )
            return self._render(
                sealed, pending, client, error="That approval secret is not right.", status=401
            )
        throttle.reset()
        code = await self.provider.create_code(pending, deps.principal)
        logger.info("OAuth client %s approved", client.client_id)
        return RedirectResponse(
            construct_redirect_uri(pending.redirect_uri, code=code, state=pending.state),
            status_code=303,
        )


class _CanonicalUrl(BaseModel):
    # Keep a path-less URL as written: RFC 8414 compares issuers as exact strings, and
    # pydantic would otherwise add a trailing slash ("https://host/").
    model_config = ConfigDict(url_preserve_empty_path=True)

    url: AnyHttpUrl


def _url(value: str) -> AnyHttpUrl:
    return _CanonicalUrl(url=value).url


def mcp_resource_url(settings: Settings) -> str:
    return f"{settings.mcp_public_url}/mcp"


def resource_metadata_url(settings: Settings) -> AnyHttpUrl:
    return build_resource_metadata_url(_url(mcp_resource_url(settings)))


def oauth_routes(provider: AnveshaOAuthProvider, settings: Settings) -> list[Route]:
    """The authorization server's endpoints, the resource metadata, and the consent page."""
    issuer = _url(settings.mcp_public_url or "")
    consent = ConsentEndpoints(provider)
    return [
        *create_auth_routes(
            provider,
            issuer_url=issuer,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
        *create_protected_resource_routes(
            resource_url=_url(mcp_resource_url(settings)),
            authorization_servers=[issuer],
            resource_name="Anvesha",
        ),
        Route(CONSENT_PATH, consent.show, methods=["GET"]),
        Route(CONSENT_PATH, consent.decide, methods=["POST"]),
    ]


# --- Connections: the clients a user approved ------------------------------------------


@dataclass(frozen=True)
class Connection:
    client_id: str
    client_name: str | None
    redirect_hosts: list[str]
    connected_at: datetime
    last_used_at: datetime | None


async def list_connections(session: AsyncSession, principal: Principal) -> list[Connection]:
    owner = (OAuthRefreshToken.workspace_id == principal.workspace_id) & (
        OAuthRefreshToken.user_id == principal.user_id
    )
    grants = (
        select(
            OAuthRefreshToken.client_id,
            func.min(OAuthRefreshToken.created_at).label("connected_at"),
        )
        .where(owner)
        .group_by(OAuthRefreshToken.client_id)
        .subquery()
    )
    used = (
        select(AccessToken.client_id, func.max(AccessToken.last_used_at).label("last_used_at"))
        .where(
            AccessToken.workspace_id == principal.workspace_id,
            AccessToken.user_id == principal.user_id,
            AccessToken.client_id.is_not(None),
        )
        .group_by(AccessToken.client_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(OAuthClient, grants.c.connected_at, used.c.last_used_at)
            .join(grants, grants.c.client_id == OAuthClient.client_id)
            .outerjoin(used, used.c.client_id == OAuthClient.client_id)
            .order_by(grants.c.connected_at.desc())
        )
    ).all()
    connections = []
    for client, connected_at, last_used_at in rows:
        hosts: list[str] = []
        for uri in client.client_metadata.get("redirect_uris") or []:
            parts = urlsplit(str(uri))
            host = parts.netloc or f"{parts.scheme}:"
            if host not in hosts:
                hosts.append(host)
        connections.append(
            Connection(
                client_id=client.client_id,
                client_name=client.client_name,
                redirect_hosts=hosts,
                connected_at=connected_at,
                last_used_at=last_used_at,
            )
        )
    return connections


async def disconnect(session: AsyncSession, principal: Principal, client_id: str) -> None:
    """Revoke everything a client was granted by this user."""
    mine: list[Any] = [
        OAuthRefreshToken.client_id == client_id,
        OAuthRefreshToken.workspace_id == principal.workspace_id,
        OAuthRefreshToken.user_id == principal.user_id,
    ]
    removed = await session.execute(delete(OAuthRefreshToken).where(*mine))
    tokens = await session.execute(
        delete(AccessToken).where(
            AccessToken.client_id == client_id,
            AccessToken.workspace_id == principal.workspace_id,
            AccessToken.user_id == principal.user_id,
        )
    )
    await session.execute(
        delete(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.client_id == client_id,
            OAuthAuthorizationCode.user_id == principal.user_id,
        )
    )
    if removed.rowcount == 0 and tokens.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFoundError("Connection not found")
    await session.commit()
