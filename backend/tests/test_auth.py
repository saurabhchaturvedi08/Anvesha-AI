"""Multi-user mode: JWT sign-in, workspaces and roles, invitations, rate limits,
OpenRouter OAuth, and approving MCP connections from the web app."""

import base64
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from anvesha.access_tokens import create_access_token, verify_access_token
from anvesha.config import Settings
from anvesha.db.models import AccessToken
from anvesha.llm import openrouter_oauth
from anvesha.ratelimit import prune_rate_limits
from anvesha.tenancy import Principal
from tests.conftest import FakeProviderAPI, make_settings

ISSUER = "http://web.test"
KEY = Ed25519PrivateKey.generate()
JWK = {**json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(KEY.public_key())), "kid": "k1"}
REDIRECT_URI = "https://claude.example/callback"


@pytest.fixture
def test_settings(database_url: str) -> Settings:
    return make_settings(
        database_url=database_url,
        auth_mode="jwt",
        auth_jwks_url=f"{ISSUER}/api/auth/jwks",
        auth_issuer=ISSUER,
        web_url=ISSUER,
        mcp_public_url="http://localhost",
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """The usual client, with the web app's signing keys already known to the API."""
    app.state.jwt_verifier.set_keys({"keys": [JWK]})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _token(subject: str, *, key: Ed25519PrivateKey = KEY, **claims: Any) -> str:
    now = int(time.time())
    payload = {
        "sub": subject,
        "email": f"{subject}@example.org",
        "name": subject.title(),
        "iss": ISSUER,
        "aud": "anvesha-api",
        "iat": now,
        "exp": now + 300,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="EdDSA", headers={"kid": "k1"})


def _as(subject: str, workspace: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {_token(subject)}"}
    if workspace:
        headers["X-Anvesha-Workspace"] = workspace
    return headers


async def test_requests_need_a_valid_token(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/projects")).status_code == 401
    # Routes that don't otherwise ask who is calling are protected too.
    assert (await client.get("/api/v1/config")).status_code == 401
    assert (await client.get("/health")).status_code == 200

    other_key = Ed25519PrivateKey.generate()
    expired = _token("alice", exp=int(time.time()) - 3600)
    for bad in (
        "not-a-jwt",
        _token("alice", key=other_key),
        expired,
        _token("alice", aud="someone-else"),
        _token("alice", iss="http://evil.test"),
    ):
        response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {bad}"})
        assert response.status_code == 401, bad
        assert response.json()["code"] == "unauthenticated"


async def test_first_request_creates_the_user_and_a_personal_workspace(
    client: AsyncClient,
) -> None:
    me = (await client.get("/api/v1/me", headers=_as("alice"))).json()
    assert me["auth_mode"] == "jwt"
    assert me["user"]["email"] == "alice@example.org"
    assert me["workspace"] == {
        "id": me["workspace"]["id"],
        "name": "Personal",
        "personal": True,
        "role": "owner",
        "members": 1,
    }
    again = (await client.get("/api/v1/me", headers=_as("alice"))).json()
    assert again["user"]["id"] == me["user"]["id"]
    assert again["workspace"]["id"] == me["workspace"]["id"]


async def test_users_only_see_their_own_workspace(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/projects",
        json={"title": "Alice's", "objective": "Private research question", "depth": "quick"},
        headers=_as("alice"),
    )
    assert created.status_code == 201
    assert (await client.get("/api/v1/projects", headers=_as("bob"))).json() == []
    alice_ws = (await client.get("/api/v1/me", headers=_as("alice"))).json()["workspace"]["id"]
    # Naming someone else's workspace doesn't get you in.
    response = await client.get("/api/v1/projects", headers=_as("bob", alice_ws))
    assert response.status_code == 404
    assert (
        await client.get("/api/v1/projects", headers=_as("bob", "not-a-uuid"))
    ).status_code == 422


async def _team(client: AsyncClient) -> str:
    response = await client.post("/api/v1/workspaces", json={"name": "Lab"}, headers=_as("alice"))
    assert response.status_code == 201
    return str(response.json()["id"])


async def _join(client: AsyncClient, workspace: str, subject: str, role: str = "member") -> None:
    invited = await client.post(
        "/api/v1/workspace/invitations",
        json={"email": f"{subject}@example.org", "role": role},
        headers=_as("alice", workspace),
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["url"].rsplit("/", 1)[1]
    preview = (await client.get(f"/api/v1/invitations/{token}", headers=_as(subject))).json()
    assert preview["workspace_name"] == "Lab" and preview["already_member"] is False
    joined = await client.post(f"/api/v1/invitations/{token}/accept", headers=_as(subject))
    assert joined.status_code == 200 and joined.json()["role"] == role
    # An invitation works once.
    again = await client.post(f"/api/v1/invitations/{token}/accept", headers=_as("carol"))
    assert again.status_code == 404


async def test_team_workspace_invitations_and_roles(client: AsyncClient) -> None:
    team = await _team(client)
    invited = await client.post(
        "/api/v1/workspace/invitations", json={"role": "member"}, headers=_as("alice", team)
    )
    assert invited.json()["url"].startswith(f"{ISSUER}/invite/inv_")
    # Personal workspaces can't be shared.
    personal = await client.post(
        "/api/v1/workspace/invitations", json={"role": "member"}, headers=_as("alice")
    )
    assert personal.status_code == 409

    await _join(client, team, "bob")
    project = await client.post(
        "/api/v1/projects",
        json={"title": "Shared", "objective": "A shared research question", "depth": "quick"},
        headers=_as("alice", team),
    )
    shared = await client.get("/api/v1/projects", headers=_as("bob", team))
    assert [p["id"] for p in shared.json()] == [project.json()["id"]]

    # Members research; admins manage keys, models and people.
    for method, url, body in (
        ("PUT", "/api/v1/model-preferences/lead", {"provider": "openai", "model": "gpt-x"}),
        ("PUT", "/api/v1/providers/openai/credential", {"api_key": "sk-test"}),
        ("POST", "/api/v1/workspace/invitations", {"role": "member"}),
        ("PATCH", "/api/v1/workspace", {"name": "Bob's now"}),
    ):
        response = await client.request(method, url, json=body, headers=_as("bob", team))
        assert response.status_code == 403, (url, response.text)

    members = (await client.get("/api/v1/workspace/members", headers=_as("alice", team))).json()
    bob_id = next(m["user"]["id"] for m in members if m["user"]["email"] == "bob@example.org")
    alice_id = next(m["user"]["id"] for m in members if m["role"] == "owner")
    promoted = await client.patch(
        f"/api/v1/workspace/members/{bob_id}", json={"role": "admin"}, headers=_as("alice", team)
    )
    assert promoted.json()["role"] == "admin"
    renamed = await client.patch(
        "/api/v1/workspace", json={"name": "Lab 2"}, headers=_as("bob", team)
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Lab 2"
    # Admins can't touch owners, and the last owner can't step down or leave.
    demote_owner = await client.patch(
        f"/api/v1/workspace/members/{alice_id}", json={"role": "member"}, headers=_as("bob", team)
    )
    assert demote_owner.status_code == 403
    assert (
        await client.patch(
            f"/api/v1/workspace/members/{alice_id}",
            json={"role": "admin"},
            headers=_as("alice", team),
        )
    ).status_code == 409
    assert (
        await client.delete(f"/api/v1/workspace/members/{alice_id}", headers=_as("alice", team))
    ).status_code == 409
    assert (await client.delete("/api/v1/workspace", headers=_as("bob", team))).status_code == 403


async def test_removed_members_lose_access_and_tokens(app: FastAPI, client: AsyncClient) -> None:
    team = await _team(client)
    await _join(client, team, "bob")
    bob = (await client.get("/api/v1/me", headers=_as("bob", team))).json()
    async with app.state.db.session() as session:
        _, token = await create_access_token(
            session,
            Principal(
                user_id=uuid.UUID(bob["user"]["id"]), workspace_id=uuid.UUID(team), role="member"
            ),
            name="bob's laptop",
        )
    verified = await verify_access_token(app.state.db, token)
    assert verified is not None and verified.principal.role == "member"

    removed = await client.delete(
        f"/api/v1/workspace/members/{bob['user']['id']}", headers=_as("alice", team)
    )
    assert removed.status_code == 204
    assert (await client.get("/api/v1/projects", headers=_as("bob", team))).status_code == 404
    assert await verify_access_token(app.state.db, token) is None
    async with app.state.db.session() as session:
        assert await session.scalar(select(func.count()).select_from(AccessToken)) == 0

    # Owners can delete a team workspace (never a personal one).
    assert (await client.delete("/api/v1/workspace", headers=_as("alice", team))).status_code == 204
    assert (await client.delete("/api/v1/workspace", headers=_as("alice"))).status_code == 409


async def test_rate_limits(app: FastAPI, client: AsyncClient) -> None:
    app.state.settings = app.state.settings.model_copy(
        update={"rate_limit_tools_per_10_minutes": 2}
    )
    project = await client.post(
        "/api/v1/projects",
        json={"title": "Limits", "objective": "Rate limited question", "depth": "quick"},
        headers=_as("alice"),
    )
    url = f"/api/v1/projects/{project.json()['id']}/tools/no_such_tool"
    for _ in range(2):
        assert (await client.post(url, json={}, headers=_as("alice"))).status_code == 404
    limited = await client.post(url, json={}, headers=_as("alice"))
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    assert 1 <= int(limited.headers["retry-after"]) <= 600
    # Limits are per user.
    other = await client.post(url, json={}, headers=_as("bob"))
    assert other.status_code != 429
    await prune_rate_limits(app.state.db)  # old windows only; nothing to remove yet


async def test_connect_openrouter_with_pkce(
    app: FastAPI, client: AsyncClient, provider_api: FakeProviderAPI
) -> None:
    started = await client.post(
        "/api/v1/providers/openrouter/oauth/start",
        json={"callback_url": f"{ISSUER}/settings/openrouter/callback"},
        headers=_as("alice"),
    )
    assert started.status_code == 200, started.text
    url = urlsplit(started.json()["authorization_url"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == openrouter_oauth.AUTH_URL
    assert query["code_challenge_method"] == ["S256"]
    state = started.json()["state"]

    # Callbacks must go back to the web app.
    elsewhere = await client.post(
        "/api/v1/providers/openrouter/oauth/start",
        json={"callback_url": "https://evil.example/callback"},
        headers=_as("alice"),
    )
    assert elsewhere.status_code == 422
    # The state only works for whoever started the flow.
    stolen = await client.post(
        "/api/v1/providers/openrouter/oauth/complete",
        json={"code": "code-1", "state": state},
        headers=_as("bob"),
    )
    assert stolen.status_code == 422 and stolen.json()["code"] == "invalid_oauth_state"

    provider_api.add(openrouter_oauth.KEYS_URL, 200, {"key": "sk-or-v1-from-oauth"})
    provider_api.add("https://openrouter.ai/api/v1/key", 200, {"data": {"label": "x"}})
    provider_api.add("https://openrouter.ai/api/v1/models", 200, {"data": [{"id": "m/1"}]})
    done = await client.post(
        "/api/v1/providers/openrouter/oauth/complete",
        json={"code": "code-1", "state": state},
        headers=_as("alice"),
    )
    assert done.status_code == 200, done.text
    assert done.json()["source"] == "workspace" and done.json()["status"] == "valid"
    exchange = next(r for r in provider_api.requests if str(r.url) == openrouter_oauth.KEYS_URL)
    sent = json.loads(exchange.content)
    challenge = query["code_challenge"][0]
    assert openrouter_oauth.code_challenge(sent["code_verifier"]) == challenge
    assert sent["code"] == "code-1"


async def test_mcp_connections_are_approved_in_the_web_app(client: AsyncClient) -> None:
    registered = (
        await client.post(
            "/register",
            json={
                "redirect_uris": [REDIRECT_URI],
                "client_name": "Claude",
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
    ).json()
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    authorized = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "s-1",
            "resource": "http://localhost/mcp",
        },
    )
    location = authorized.headers["location"]
    assert location.startswith(f"{ISSUER}/connect?request=")
    sealed = parse_qs(urlsplit(location).query)["request"][0]
    # The single-user approval page (with its shared secret) doesn't exist in this mode.
    assert (await client.get("/oauth/consent", params={"request": sealed})).status_code == 404

    details = await client.get(
        "/api/v1/oauth/consent", params={"request": sealed}, headers=_as("alice")
    )
    assert details.json()["client_name"] == "Claude"
    assert details.json()["redirect_host"] == "claude.example"
    denied = await client.post(
        "/api/v1/oauth/consent",
        json={"request": sealed, "decision": "deny"},
        headers=_as("alice"),
    )
    assert "error=access_denied" in denied.json()["redirect_url"]

    allowed = await client.post(
        "/api/v1/oauth/consent",
        json={"request": sealed, "decision": "allow"},
        headers=_as("alice"),
    )
    params = parse_qs(urlsplit(allowed.json()["redirect_url"]).query)
    assert params["state"] == ["s-1"]
    tokens = await client.post(
        "/token",
        data={
            "client_id": registered["client_id"],
            "client_secret": registered["client_secret"],
            "grant_type": "authorization_code",
            "code": params["code"][0],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "resource": "http://localhost/mcp",
        },
    )
    assert tokens.status_code == 200, tokens.text
    connections = (await client.get("/api/v1/connections", headers=_as("alice"))).json()
    assert [c["client_name"] for c in connections] == ["Claude"]
    assert (await client.get("/api/v1/connections", headers=_as("bob"))).json() == []


def test_jwt_mode_needs_its_settings() -> None:
    with pytest.raises(ValueError, match="ANVESHA_AUTH_JWKS_URL"):
        make_settings(auth_mode="jwt")
    assert make_settings().rate_limits_on is False
    assert make_settings(rate_limits_enabled=True).rate_limits_on is True
