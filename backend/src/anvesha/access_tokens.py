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
from anvesha.db.models import AccessToken
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
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFoundError("Access token not found")
    await session.commit()


async def verify_access_token(db: Database, token: str) -> VerifiedToken | None:
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
        if row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION:
            await session.execute(
                update(AccessToken).where(AccessToken.id == row.id).values(last_used_at=now)
            )
            await session.commit()
        return VerifiedToken(
            token_id=row.id,
            principal=Principal(user_id=row.user_id, workspace_id=row.workspace_id),
            expires_at=row.expires_at,
        )


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
    if args.command == "create" and not 1 <= len(args.name) <= 100:
        parser.error("--name must be 1 to 100 characters.")
    try:
        asyncio.run(_cli(args))
    except NotFoundError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
