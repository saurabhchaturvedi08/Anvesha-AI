"""Verifying the web app's JWTs (multi-user mode).

People sign in to the web app (Better Auth). Its server calls this API on their behalf
with a short-lived JWT signed by keys it publishes as a JWKS. The keys are cached here and
fetched again when a token names a key we haven't seen (key rotation), at most every
``REFRESH_INTERVAL`` seconds so bad tokens can't make us hammer the web app.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx2
import jwt

from anvesha.config import Settings
from anvesha.errors import UnauthenticatedError

logger = logging.getLogger(__name__)

ALGORITHMS = ["EdDSA", "ES256", "ES384", "ES512", "RS256", "RS384", "RS512", "PS256"]
REFRESH_INTERVAL = 30.0
LEEWAY_SECONDS = 30
MAX_TOKEN_CHARS = 8000


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str | None
    name: str | None


class JwtVerifier:
    def __init__(self, settings: Settings, http: httpx2.AsyncClient) -> None:
        assert settings.auth_jwks_url and settings.auth_issuer
        self._jwks_url = settings.auth_jwks_url
        self._issuer = settings.auth_issuer
        self._audience = settings.auth_audience
        self._http = http
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    def set_keys(self, jwks: dict[str, Any]) -> None:
        keys: dict[str, jwt.PyJWK] = {}
        for item in jwks.get("keys", []):
            try:
                key = jwt.PyJWK(item)
            except jwt.PyJWKError as exc:
                logger.warning("Ignoring a signing key the API can't use: %s", exc)
                continue
            keys[str(item.get("kid", ""))] = key
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def _refresh(self) -> None:
        async with self._lock:
            if time.monotonic() - self._fetched_at < REFRESH_INTERVAL and self._keys:
                return
            try:
                response = await self._http.get(self._jwks_url, timeout=10)
                response.raise_for_status()
                self.set_keys(response.json())
            except (httpx2.HTTPError, ValueError) as exc:
                self._fetched_at = time.monotonic()  # back off before trying again
                logger.error("Could not fetch signing keys from %s: %s", self._jwks_url, exc)

    async def _key(self, kid: str) -> jwt.PyJWK | None:
        key = self._keys.get(kid)
        if key is None and time.monotonic() - self._fetched_at >= REFRESH_INTERVAL:
            await self._refresh()
            key = self._keys.get(kid)
        if key is None and kid == "" and len(self._keys) == 1:
            key = next(iter(self._keys.values()))  # a token without kid, one key published
        return key

    async def verify(self, token: str) -> Identity:
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise UnauthenticatedError("Missing or malformed credentials.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise UnauthenticatedError("Malformed token.") from exc
        key = await self._key(str(header.get("kid", "")))
        if key is None:
            raise UnauthenticatedError("The token was signed with an unknown key.")
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthenticatedError("The session token has expired.") from exc
        except jwt.PyJWTError as exc:
            raise UnauthenticatedError(f"Invalid token: {exc}") from exc
        subject = str(claims["sub"])
        if not subject or len(subject) > 255:
            raise UnauthenticatedError("Invalid token subject.")
        email = claims.get("email")
        name = claims.get("name")
        return Identity(
            subject=subject,
            email=str(email)[:320] if email else None,
            name=str(name)[:200] if name else None,
        )
