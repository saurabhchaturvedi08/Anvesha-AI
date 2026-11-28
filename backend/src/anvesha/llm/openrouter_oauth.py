"""Connect OpenRouter with one click: OAuth PKCE, which gives us a key on the user's own
OpenRouter account (https://openrouter.ai/docs/use-cases/oauth-pkce).

1. ``start``: make a code verifier and seal it, with who asked and when, into an opaque
   ``state`` (encrypted with the master key, so nothing is stored server side). The web
   app keeps ``state`` and sends the user to OpenRouter.
2. OpenRouter sends the user back to the callback with a one-time ``code``.
3. ``exchange``: open ``state``, check it belongs to the same workspace and user, and
   trade code + verifier for the key, which is then stored like a pasted key.
"""

import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx2

from anvesha.crypto import DecryptionError, SecretBox
from anvesha.errors import InvalidInputError, UpstreamError
from anvesha.tenancy import Principal

AUTH_URL = "https://openrouter.ai/auth"
KEYS_URL = "https://openrouter.ai/api/v1/auth/keys"
STATE_TTL_SECONDS = 15 * 60
_AAD = "openrouter-oauth-state"


@dataclass(frozen=True)
class OAuthStart:
    authorization_url: str
    state: str


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def start(
    vault: SecretBox, principal: Principal, callback_url: str, *, now: float | None = None
) -> OAuthStart:
    verifier = secrets.token_urlsafe(48)
    state = vault.encrypt(
        json.dumps(
            {
                "verifier": verifier,
                "workspace_id": str(principal.workspace_id),
                "user_id": str(principal.user_id),
                "expires_at": (time.time() if now is None else now) + STATE_TTL_SECONDS,
            }
        ),
        _AAD,
    )
    query = urlencode(
        {
            "callback_url": callback_url,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return OAuthStart(authorization_url=f"{AUTH_URL}?{query}", state=state)


def open_state(vault: SecretBox, principal: Principal, state: str) -> str:
    """The code verifier, if ``state`` is genuine, fresh, and this principal's."""
    invalid = InvalidInputError(
        "This OpenRouter sign-in expired or was started elsewhere. Start again.",
        code="invalid_oauth_state",
    )
    if not state or len(state) > 4000:
        raise invalid
    try:
        payload = json.loads(vault.decrypt(state, _AAD))
        expired = float(payload["expires_at"]) < time.time()
        owner = (uuid.UUID(payload["workspace_id"]), uuid.UUID(payload["user_id"]))
        verifier = str(payload["verifier"])
    except (DecryptionError, ValueError, KeyError, TypeError) as exc:
        raise invalid from exc
    if expired or owner != (principal.workspace_id, principal.user_id):
        raise invalid
    return verifier


async def exchange(http: httpx2.AsyncClient, code: str, verifier: str) -> str:
    """Trade the one-time code for an API key on the user's OpenRouter account."""
    try:
        response = await http.post(
            KEYS_URL,
            json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
            timeout=20,
        )
    except httpx2.HTTPError as exc:
        raise UpstreamError(f"OpenRouter is unreachable: {exc}") from exc
    if response.status_code in (400, 401, 403):
        raise InvalidInputError(
            "OpenRouter didn't accept the sign-in (the code may have been used or expired). "
            "Start again.",
            code="oauth_exchange_failed",
        )
    if response.status_code >= 300:
        raise UpstreamError(f"OpenRouter answered {response.status_code} when issuing the key.")
    try:
        key = response.json()["key"]
    except (ValueError, KeyError, TypeError) as exc:
        raise UpstreamError("OpenRouter's answer had no key in it.") from exc
    if not isinstance(key, str) or not key:
        raise UpstreamError("OpenRouter's answer had no key in it.")
    return key
