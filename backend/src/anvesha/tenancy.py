"""Who is acting, and in which workspace.

Single-user mode has one built-in user with one workspace. In multi-user mode a user is
created the first time they call the API (identified by the sign-in provider's subject),
with a personal workspace; they may also belong to shared workspaces and pick one per
request.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.db.models import User, Workspace, WorkspaceMember
from anvesha.errors import ForbiddenError, NotFoundError

Role = Literal["owner", "admin", "member"]
_RANK = {"member": 0, "admin": 1, "owner": 2}


@dataclass(frozen=True)
class Principal:
    """Who is acting, and in which workspace. Every data access is scoped by this."""

    user_id: uuid.UUID
    workspace_id: uuid.UUID
    role: str = "owner"


def has_role(principal: Principal, minimum: Role) -> bool:
    return _RANK.get(principal.role, -1) >= _RANK[minimum]


def require_role(principal: Principal, minimum: Role) -> None:
    if not has_role(principal, minimum):
        raise ForbiddenError(f"This needs the {minimum} role in this workspace.")


async def _personal_workspace(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    workspace = Workspace(name="Personal", personal=True)
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role="owner"))
    return workspace.id


async def ensure_local_principal(session: AsyncSession, email: str) -> Principal:
    """Create (once) the built-in user and personal workspace used in local auth mode."""
    email = email.strip().lower()
    await session.execute(
        insert(User)
        .values(email=email, display_name="Local user")
        .on_conflict_do_nothing(index_elements=[User.email])
    )
    user_id = (await session.execute(select(User.id).where(User.email == email))).scalar_one()

    workspace_id = (
        await session.execute(
            select(WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(WorkspaceMember.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if workspace_id is None:
        workspace_id = await _personal_workspace(session, user_id)

    await session.commit()
    return Principal(user_id=user_id, workspace_id=workspace_id)


async def ensure_user(
    session: AsyncSession, *, subject: str, email: str | None, name: str | None
) -> uuid.UUID:
    """The user for a sign-in subject, created (with a personal workspace) on first use."""
    user = await session.scalar(select(User).where(User.auth_subject == subject))
    email = (email or "").strip().lower() or f"{subject}@users.invalid"
    if user is not None:
        if user.email != email or (name and user.display_name != name):
            # Keep the profile in step with the sign-in provider. Another account already
            # using the address is left alone.
            try:
                async with session.begin_nested():
                    await session.execute(
                        update(User)
                        .where(User.id == user.id)
                        .values(email=email, display_name=name or user.display_name)
                    )
            except IntegrityError:
                pass
            await session.commit()
        return user.id
    try:
        async with session.begin_nested():
            user = User(email=email, display_name=name, auth_subject=subject)
            session.add(user)
            await session.flush()
    except IntegrityError:
        # Created concurrently by another request, or the email belongs to an account
        # from another sign-in: never merge accounts by email.
        existing = await session.scalar(select(User.id).where(User.auth_subject == subject))
        if existing is not None:
            return existing
        user = User(email=f"{subject}@users.invalid", display_name=name, auth_subject=subject)
        session.add(user)
        await session.flush()
    await _personal_workspace(session, user.id)
    await session.commit()
    return user.id


async def principal_for(
    session: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID | None
) -> Principal:
    """The user acting in the requested workspace (default: their oldest membership)."""
    query = (
        select(WorkspaceMember)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(WorkspaceMember.created_at, WorkspaceMember.workspace_id)
        .limit(1)
    )
    if workspace_id is not None:
        query = query.where(WorkspaceMember.workspace_id == workspace_id)
    member = await session.scalar(query)
    if member is None:
        if workspace_id is not None:
            raise NotFoundError("Workspace not found, or you are not a member of it.")
        workspace = await _personal_workspace(session, user_id)
        await session.commit()
        return Principal(user_id=user_id, workspace_id=workspace, role="owner")
    return Principal(user_id=user_id, workspace_id=member.workspace_id, role=member.role)
