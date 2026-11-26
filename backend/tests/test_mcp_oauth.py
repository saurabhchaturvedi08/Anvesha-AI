"""OAuth sign-in for MCP clients, end to end: discovery, dynamic registration, consent with
the approval secret, PKCE code exchange, refresh rotation, revocation, connections, and
the public app that exposes only MCP."""

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select, update

from anvesha.access_tokens import prune_expired_credentials
from anvesha.api.app import create_app, create_public_app
from anvesha.config import Settings
from anvesha.db.models import AccessToken, OAuthClient, OAuthRefreshToken
from anvesha.mcp.oauth import CONSENT_PATH
from tests.conftest import make_settings
from tests.test_mcp import _call, _mcp

PUBLIC_URL = "http://localhost"
SECRET = "correct horse battery staple"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return make_settings(
        database_url=database_url, mcp_public_url=PUBLIC_URL, mcp_approval_secret=SECRET
    )


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.decode().rstrip("=")


async def _register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "client_name": "Claude",
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "client_secret_post",
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _local(url: str) -> str:
    """The path and query of an absolute URL on this server."""
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == PUBLIC_URL, url
    return f"{parts.path}?{parts.query}"


async def _consent_url(client: AsyncClient, registered: dict[str, Any], challenge: str) -> str:
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-123",
            "resource": f"{PUBLIC_URL}/mcp",
        },
    )
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{PUBLIC_URL}{CONSENT_PATH}?request=")
    return _local(location)


def _sealed(consent_url: str) -> str:
    return parse_qs(urlsplit(consent_url).query)["request"][0]


async def _decide(
    client: AsyncClient, consent_url: str, *, secret: str = SECRET, decision: str = "allow"
) -> Response:
    return await client.post(
        CONSENT_PATH,
        data={"request": _sealed(consent_url), "secret": secret, "decision": decision},
    )


def _redirect_params(response: Response) -> dict[str, str]:
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith(REDIRECT_URI + "?"), location
    return {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}


async def _token(client: AsyncClient, registered: dict[str, Any], **form: str) -> Response:
    return await client.post(
        "/token",
        data={
            "client_id": registered["client_id"],
            "client_secret": registered["client_secret"],
            **form,
        },
    )


async def _sign_in(client: AsyncClient, registered: dict[str, Any]) -> dict[str, Any]:
    verifier, challenge = _pkce()
    consent = await _consent_url(client, registered, challenge)
    code = _redirect_params(await _decide(client, consent))["code"]
    response = await _token(
        client,
        registered,
        grant_type="authorization_code",
        code=code,
        redirect_uri=REDIRECT_URI,
        code_verifier=verifier,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _mcp_works(app: FastAPI, token: str) -> bool:
    try:
        async with _mcp(app, token) as session:
            return not (await _call(session, "list_projects")).is_error
    except Exception:
        return False


async def test_discovery_documents_and_the_401_pointer(client: AsyncClient) -> None:
    server = (await client.get("/.well-known/oauth-authorization-server")).json()
    assert server["issuer"] == PUBLIC_URL
    assert server["authorization_endpoint"] == f"{PUBLIC_URL}/authorize"
    assert server["token_endpoint"] == f"{PUBLIC_URL}/token"
    assert server["registration_endpoint"] == f"{PUBLIC_URL}/register"
    assert server["revocation_endpoint"] == f"{PUBLIC_URL}/revoke"
    assert "S256" in server["code_challenge_methods_supported"]

    resource = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert resource["resource"] == f"{PUBLIC_URL}/mcp"
    assert resource["authorization_servers"] == [PUBLIC_URL]

    refused = await client.post("/mcp", json={})
    assert refused.status_code == 401
    assert (
        f'resource_metadata="{PUBLIC_URL}/.well-known/oauth-protected-resource/mcp"'
        in refused.headers["www-authenticate"]
    )


async def test_sign_in_refresh_and_revoke(app: FastAPI, client: AsyncClient) -> None:
    registered = await _register(client)
    assert registered["client_secret"]
    async with app.state.db.session() as session:
        row = await session.get(OAuthClient, registered["client_id"])
        assert row is not None and row.client_name == "Claude"
        # The secret is stored encrypted, never as-is.
        assert row.secret_encrypted and registered["client_secret"] not in row.secret_encrypted

    verifier, challenge = _pkce()
    consent = await _consent_url(client, registered, challenge)

    page = await client.get(consent)
    assert page.status_code == 200
    assert "Claude wants to use Anvesha for you" in page.text and "claude.ai" in page.text
    policy = page.headers["content-security-policy"]
    assert "form-action 'self' https://claude.ai" in policy and "frame-ancestors 'none'" in policy
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"

    wrong = await _decide(client, consent, secret="guess")
    assert wrong.status_code == 401 and "not right" in wrong.text
    assert "location" not in wrong.headers

    params = _redirect_params(await _decide(client, consent))
    assert params["state"] == "state-123" and params["code"]

    bad_verifier = await _token(
        client,
        registered,
        grant_type="authorization_code",
        code=params["code"],
        redirect_uri=REDIRECT_URI,
        code_verifier=secrets.token_urlsafe(48),
    )
    assert bad_verifier.status_code == 400 and bad_verifier.json()["error"] == "invalid_grant"

    exchanged = await _token(
        client,
        registered,
        grant_type="authorization_code",
        code=params["code"],
        redirect_uri=REDIRECT_URI,
        code_verifier=verifier,
    )
    assert exchanged.status_code == 200, exchanged.text
    tokens = exchanged.json()
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert tokens["access_token"].startswith("anv_")
    assert tokens["refresh_token"].startswith("anvr_")

    # Codes work once.
    replay = await _token(
        client,
        registered,
        grant_type="authorization_code",
        code=params["code"],
        redirect_uri=REDIRECT_URI,
        code_verifier=verifier,
    )
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"

    assert await _mcp_works(app, tokens["access_token"])
    async with app.state.db.session() as session:
        issued = await session.scalar(
            select(AccessToken).where(AccessToken.client_id == registered["client_id"])
        )
        assert issued is not None and issued.resource == f"{PUBLIC_URL}/mcp"

    # OAuth tokens are connections, not personal tokens.
    assert (await client.get("/api/v1/access-tokens")).json() == []
    connections = (await client.get("/api/v1/connections")).json()
    assert [(c["client_id"], c["client_name"]) for c in connections] == [
        (registered["client_id"], "Claude")
    ]
    assert connections[0]["redirect_hosts"] == ["claude.ai"]
    assert connections[0]["last_used_at"] is not None

    # Refreshing replaces both tokens.
    refreshed = await _token(
        client, registered, grant_type="refresh_token", refresh_token=tokens["refresh_token"]
    )
    assert refreshed.status_code == 200, refreshed.text
    renewed = refreshed.json()
    assert renewed["refresh_token"] != tokens["refresh_token"]
    assert not await _mcp_works(app, tokens["access_token"])
    assert await _mcp_works(app, renewed["access_token"])
    reused = await _token(
        client, registered, grant_type="refresh_token", refresh_token=tokens["refresh_token"]
    )
    assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"

    # Another client can't use or revoke this client's tokens.
    other = await _register(client, client_name="Other")
    stolen = await _token(
        client, other, grant_type="refresh_token", refresh_token=renewed["refresh_token"]
    )
    assert stolen.status_code == 400
    await client.post(
        "/revoke",
        data={
            "token": renewed["access_token"],
            "client_id": other["client_id"],
            "client_secret": other["client_secret"],
        },
    )
    assert await _mcp_works(app, renewed["access_token"])

    # Revoking the access token revokes its refresh token too.
    revoked = await client.post(
        "/revoke",
        data={
            "token": renewed["access_token"],
            "client_id": registered["client_id"],
            "client_secret": registered["client_secret"],
        },
    )
    assert revoked.status_code == 200
    assert not await _mcp_works(app, renewed["access_token"])
    after = await _token(
        client, registered, grant_type="refresh_token", refresh_token=renewed["refresh_token"]
    )
    assert after.status_code == 400
    assert (await client.get("/api/v1/connections")).json() == []


async def test_denying_and_disconnecting(app: FastAPI, client: AsyncClient) -> None:
    registered = await _register(client)
    _, challenge = _pkce()
    consent = await _consent_url(client, registered, challenge)
    denied = _redirect_params(await _decide(client, consent, decision="deny", secret=""))
    assert denied["error"] == "access_denied" and denied["state"] == "state-123"
    assert "code" not in denied

    tokens = await _sign_in(client, registered)
    assert await _mcp_works(app, tokens["access_token"])
    gone = await client.delete(f"/api/v1/connections/{registered['client_id']}")
    assert gone.status_code == 204
    assert not await _mcp_works(app, tokens["access_token"])
    refresh = await _token(
        client, registered, grant_type="refresh_token", refresh_token=tokens["refresh_token"]
    )
    assert refresh.status_code == 400
    assert (
        await client.delete(f"/api/v1/connections/{registered['client_id']}")
    ).status_code == 404


async def test_public_client_with_pkce_only(app: FastAPI, client: AsyncClient) -> None:
    registered = await _register(
        client,
        client_name="Cursor",
        redirect_uris=["cursor://anysphere.cursor-retrieval/oauth/callback"],
        token_endpoint_auth_method="none",
    )
    assert registered.get("client_secret") is None
    verifier, challenge = _pkce()
    response = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": "cursor://anysphere.cursor-retrieval/oauth/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    consent = _local(response.headers["location"])
    page = await client.get(consent)
    assert "form-action 'self' cursor:" in page.headers["content-security-policy"]
    approved = await _decide(client, consent)
    assert approved.status_code == 303
    location = approved.headers["location"]
    assert location.startswith("cursor://anysphere.cursor-retrieval/oauth/callback?code=")
    code = parse_qs(urlsplit(location).query)["code"][0]
    exchanged = await client.post(
        "/token",
        data={
            "client_id": registered["client_id"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "cursor://anysphere.cursor-retrieval/oauth/callback",
            "code_verifier": verifier,
        },
    )
    assert exchanged.status_code == 200, exchanged.text
    assert await _mcp_works(app, exchanged.json()["access_token"])


async def test_consent_requests_cannot_be_forged_or_reused_late(
    app: FastAPI, client: AsyncClient
) -> None:
    registered = await _register(client)
    _, challenge = _pkce()
    consent = await _consent_url(client, registered, challenge)
    sealed = _sealed(consent)
    tampered = sealed[:-6] + ("A" * 6 if not sealed.endswith("A" * 6) else "B" * 6)
    for value in (tampered, "garbage", ""):
        page = await client.get(CONSENT_PATH, params={"request": value})
        assert page.status_code == 400 and "expired" in page.text
        posted = await client.post(
            CONSENT_PATH, data={"request": value, "secret": SECRET, "decision": "allow"}
        )
        assert posted.status_code == 400

    # A request sealed for a client that no longer exists is refused too.
    async with app.state.db.session() as session:
        row = await session.get(OAuthClient, registered["client_id"])
        await session.delete(row)
        await session.commit()
    assert (await client.get(consent)).status_code == 400


async def test_wrong_secrets_lock_approval(app: FastAPI, client: AsyncClient) -> None:
    registered = await _register(client)
    _, challenge = _pkce()
    consent = await _consent_url(client, registered, challenge)
    for _ in range(10):
        assert (await _decide(client, consent, secret=f"guess-{uuid.uuid4()}")).status_code == 401
    locked = await _decide(client, consent)
    assert locked.status_code == 429 and "paused" in locked.text
    # Denying still works while locked.
    assert (await _decide(client, consent, decision="deny")).status_code == 303


def test_oauth_needs_an_approval_secret(database_url: str) -> None:
    with pytest.raises(RuntimeError, match="APPROVAL_SECRET"):
        create_app(make_settings(database_url=database_url, mcp_public_url=PUBLIC_URL))
    with pytest.raises(RuntimeError, match="MCP only"):
        create_public_app(make_settings(database_url=database_url, mcp_enabled=False))


async def test_public_app_exposes_only_mcp_and_sign_in(database_url: str) -> None:
    public = create_public_app(
        make_settings(
            database_url=database_url, mcp_public_url=PUBLIC_URL, mcp_approval_secret=SECRET
        )
    )
    async with (
        public.router.lifespan_context(public),
        AsyncClient(transport=ASGITransport(app=public), base_url="http://test") as http,
    ):
        assert (await http.get("/health")).status_code == 200
        assert (await http.get("/.well-known/oauth-authorization-server")).status_code == 200
        assert (await http.post("/mcp", json={})).status_code == 401
        page = await http.get(CONSENT_PATH, params={"request": "x"})
        assert page.status_code == 400
        # The REST API and its docs are not reachable here.
        for path in ("/api/v1/projects", "/api/v1/access-tokens", "/docs", "/openapi.json"):
            assert (await http.get(path)).status_code == 404, path
        created = await http.post("/api/v1/access-tokens", json={"name": "sneaky"})
        assert created.status_code in (404, 405)


async def test_expired_grants_are_pruned(app: FastAPI, client: AsyncClient) -> None:
    registered = await _register(client)
    await _sign_in(client, registered)
    personal = (await client.post("/api/v1/access-tokens", json={"name": "mine"})).json()
    past = datetime.now(UTC) - timedelta(minutes=1)
    async with app.state.db.session() as session:
        await session.execute(update(AccessToken).values(expires_at=past))
        await session.execute(update(OAuthRefreshToken).values(expires_at=past))
        await session.commit()
    await prune_expired_credentials(app.state.db)
    async with app.state.db.session() as session:
        tokens = (await session.scalars(select(AccessToken))).all()
        refresh = (await session.scalars(select(OAuthRefreshToken))).all()
    # Expired personal tokens stay listed, so the user can see why a client stopped.
    assert [str(t.id) for t in tokens] == [personal["id"]] and refresh == []
