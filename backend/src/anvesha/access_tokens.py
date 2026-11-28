"""Bearer tokens for outside clients (the MCP server accepts them).

A token is ``anv_`` plus 32 random bytes. Only its SHA-256 hash is stored: the tokens
are long and random, so a plain hash can't be reversed or guessed, and a lookup by hash
is a single indexed query.
"""

import argparse
import asyncio
import hashlib
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.config import get_settings
from anvesha.db.models import AccessToken, WorkspaceMember
from anvesha.db.session import Database
from anvesha.errors import NotFoundError
from anvesha.tenancy import Principal, ensure_local_principal

TOKEN_PREFIX = "anv_"
# How much of the token is kept for display: the "anv_" marker and 4 random characters.
DISPLAY_CHARS = len(TOKEN_PREFIX) + 4
LAST_USED_RESOLUTION = timedelta(minutes=1)


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class VerifiedToken:
    token_id: uuid.UUID
    principal: Principal
    expires_at: datetime | None
    # Set for tokens issued through OAuth sign-in: the approved client.
    client_id: str | None = None
    scopes: tuple[str, ...] = ()
    resource: str | None = None


async def create_access_token(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    expires_in_days: int | None = None,
) -> tuple[AccessToken, str]:
    """Store a new token and return it with its secret, which can't be recovered later."""
    token = generate_token()
    row = AccessToken(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        name=name,
        token_hash=hash_token(token),
        prefix=token[:DISPLAY_CHARS],
        expires_at=(
            datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
        ),
    )
    session.add(row)
    await session.commit()
    return row, token


async def list_access_tokens(session: AsyncSession, principal: Principal) -> Sequence[AccessToken]:
    result = await session.scalars(
        select(AccessToken)
        .where(
            AccessToken.workspace_id == principal.workspace_id,
            AccessToken.user_id == principal.user_id,
            # OAuth tokens are managed as connections (see anvesha.mcp.oauth).
            AccessToken.client_id.is_(None),
        )
        .order_by(AccessToken.created_at.desc(), AccessToken.id)
    )
    return result.all()


async def revoke_access_token(
    session: AsyncSession, principal: Principal, token_id: uuid.UUID
) -> None:
    result = await session.execute(
        delete(AccessToken).where(
            AccessToken.id == token_id,
            AccessToken.workspace_id == principal.workspace_id,
            AccessToken.user_id == principal.user_id,
            AccessToken.client_id.is_(None),
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFoundError("Access token not found")
    await session.commit()


async def verify_access_token(
    db: Database, token: str, *, track_use: bool = True
) -> VerifiedToken | None:
    """The token's owner, or None if the token is unknown, revoked or expired."""
    if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    now = datetime.now(UTC)
    async with db.session() as session:
        row = await session.scalar(
            select(AccessToken).where(AccessToken.token_hash == hash_token(token))
        )
        if row is None or (row.expires_at is not None and row.expires_at <= now):
            return None
        # A token acts with its owner's current role, and stops working if they leave.
        role = await session.scalar(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == row.workspace_id,
                WorkspaceMember.user_id == row.user_id,
            )
        )
        if role is None:
            return None
        if track_use and (
            row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION
        ):
            await session.execute(
                update(AccessToken).where(AccessToken.id == row.id).values(last_used_at=now)
            )
            await session.commit()
        return VerifiedToken(
            token_id=row.id,
            principal=Principal(user_id=row.user_id, workspace_id=row.workspace_id, role=role),
            expires_at=row.expires_at,
            client_id=row.client_id,
            scopes=tuple(row.scopes or ()),
            resource=row.resource,
        )


async def prune_expired_credentials(db: Database) -> None:
    """Delete OAuth codes and tokens past their expiry (run hourly by the worker).

    Expired tokens already fail verification; this only keeps the tables small. Personal
    tokens stay listed after they expire, so the user sees why a client stopped working.
    """
    from anvesha.db.models import OAuthAuthorizationCode, OAuthRefreshToken

    now = datetime.now(UTC)
    async with db.session() as session:
        await session.execute(
            delete(OAuthAuthorizationCode).where(OAuthAuthorizationCode.expires_at <= now)
        )
        await session.execute(delete(OAuthRefreshToken).where(OAuthRefreshToken.expires_at <= now))
        await session.execute(
            delete(AccessToken).where(
                AccessToken.client_id.is_not(None), AccessToken.expires_at <= now
            )
        )
        await session.commit()


# --- Command line ---------------------------------------------------------------------
# For self-hosters without the web app:  python -m anvesha.access_tokens create --name ...


async def _cli(args: argparse.Namespace) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    try:
        async with db.session() as session:
            principal = await ensure_local_principal(session, settings.local_user_email)
            if args.command == "create":
                row, token = await create_access_token(
                    session, principal, name=args.name, expires_in_days=args.expires_in_days
                )
                print(f"Created access token {row.id} ({row.name}).")
                print("Copy it now; it can't be shown again:\n")
                print(f"  {token}\n")
                print("MCP clients send it as the header  Authorization: Bearer <token>")
            elif args.command == "list":
                rows = await list_access_tokens(session, principal)
                if not rows:
                    print("No access tokens.")
                for row in rows:
                    used = (
                        f"last used {row.last_used_at:%Y-%m-%d %H:%M}"
                        if row.last_used_at
                        else "never used"
                    )
                    expires = f", expires {row.expires_at:%Y-%m-%d}" if row.expires_at else ""
                    print(f"{row.id}  {row.prefix}…  {row.name}  ({used}{expires})")
            else:
                await revoke_access_token(session, principal, args.token_id)
                print(f"Revoked access token {args.token_id}.")
    finally:
        await db.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m anvesha.access_tokens",
        description="Manage the access tokens MCP clients use to connect to Anvesha.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a token and print it once.")
    create.add_argument("--name", default="MCP client", help="A label to recognise it by.")
    create.add_argument("--expires-in-days", type=int, default=None)
    commands.add_parser("list", help="List tokens.")
    revoke = commands.add_parser("revoke", help="Revoke a token.")
    revoke.add_argument("token_id", type=uuid.UUID)
    args = parser.parse_args(argv)
    if get_settings().auth_mode != "local":
        parser.exit(
            1,
            "This command manages the built-in user's tokens (single-user mode). With "
            "sign-in on, create tokens in the web app: Settings → Use from AI assistants.\n",
        )
    if args.command == "create" and not 1 <= len(args.name) <= 100:
        parser.error("--name must be 1 to 100 characters.")
    try:
        asyncio.run(_cli(args))
    except NotFoundError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
