"""Workspaces, members and invitations (multi-user mode, but works in single-user too).

Roles: owners do everything, including deleting the workspace and managing owners;
admins manage the shared setup (keys, models) and members other than owners; members
research. A workspace always keeps at least one owner. Personal workspaces belong to one
user and can't be shared or deleted.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.db.models import (
    AccessToken,
    OAuthAuthorizationCode,
    OAuthRefreshToken,
    Project,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)
from anvesha.errors import AppError, ForbiddenError, InvalidInputError, NotFoundError
from anvesha.tenancy import Principal, has_role, require_role

INVITATION_PREFIX = "inv_"


class WorkspaceStateError(AppError):
    code = "invalid_workspace_state"
    http_status = 409


@dataclass(frozen=True)
class WorkspaceInfo:
    id: uuid.UUID
    name: str
    personal: bool
    role: str
    members: int


def hash_invitation(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def list_workspaces(session: AsyncSession, user_id: uuid.UUID) -> list[WorkspaceInfo]:
    counts = (
        select(WorkspaceMember.workspace_id, func.count().label("members"))
        .group_by(WorkspaceMember.workspace_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Workspace, WorkspaceMember.role, counts.c.members)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .join(counts, counts.c.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(WorkspaceMember.created_at, Workspace.id)
        )
    ).all()
    return [
        WorkspaceInfo(ws.id, ws.name, ws.personal, role, int(members)) for ws, role, members in rows
    ]


async def create_workspace(session: AsyncSession, user_id: uuid.UUID, name: str) -> uuid.UUID:
    workspace = Workspace(name=name, personal=False)
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role="owner"))
    await session.commit()
    return workspace.id


async def _workspace(session: AsyncSession, principal: Principal) -> Workspace:
    workspace = await session.get(Workspace, principal.workspace_id)
    if workspace is None:
        raise NotFoundError("Workspace not found")
    return workspace


async def rename_workspace(session: AsyncSession, principal: Principal, name: str) -> None:
    require_role(principal, "admin")
    workspace = await _workspace(session, principal)
    workspace.name = name
    await session.commit()


async def delete_workspace(session: AsyncSession, principal: Principal) -> list[uuid.UUID]:
    """Delete the workspace and everything in it. Returns its projects (for file cleanup)."""
    require_role(principal, "owner")
    workspace = await _workspace(session, principal)
    if workspace.personal:
        raise WorkspaceStateError("Personal workspaces can't be deleted.")
    projects = list(
        await session.scalars(select(Project.id).where(Project.workspace_id == workspace.id))
    )
    await session.delete(workspace)
    await session.commit()
    return projects


async def list_members(
    session: AsyncSession, principal: Principal
) -> list[tuple[User, WorkspaceMember]]:
    rows = await session.execute(
        select(User, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == principal.workspace_id)
        .order_by(WorkspaceMember.created_at, User.email)
    )
    return [(user, member) for user, member in rows.all()]


async def _member(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID
) -> WorkspaceMember:
    member = await session.get(WorkspaceMember, (principal.workspace_id, user_id))
    if member is None:
        raise NotFoundError("Member not found")
    return member


async def _owner_count(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")
        )
        or 0
    )


async def change_role(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID, role: str
) -> WorkspaceMember:
    require_role(principal, "admin")
    member = await _member(session, principal, user_id)
    if (member.role == "owner" or role == "owner") and not has_role(principal, "owner"):
        raise ForbiddenError("Only owners can make or change owners.")
    if (
        member.role == "owner"
        and role != "owner"
        and await _owner_count(session, principal.workspace_id) <= 1
    ):
        raise WorkspaceStateError("A workspace needs at least one owner.")
    member.role = role
    await session.commit()
    return member


async def remove_member(session: AsyncSession, principal: Principal, user_id: uuid.UUID) -> None:
    """Remove someone (admins), or leave (anyone).

    Their access to the workspace ends at once: their access tokens and connected apps
    for it are revoked too.
    """
    leaving = user_id == principal.user_id
    if not leaving:
        require_role(principal, "admin")
    member = await _member(session, principal, user_id)
    workspace = await _workspace(session, principal)
    if workspace.personal:
        raise WorkspaceStateError("You can't leave your personal workspace.")
    if member.role == "owner":
        if not leaving and not has_role(principal, "owner"):
            raise ForbiddenError("Only owners can remove owners.")
        if await _owner_count(session, principal.workspace_id) <= 1:
            raise WorkspaceStateError(
                "A workspace needs at least one owner. Make someone else an owner first."
            )
    await session.delete(member)
    await session.execute(
        delete(AccessToken).where(
            AccessToken.workspace_id == principal.workspace_id, AccessToken.user_id == user_id
        )
    )
    await session.execute(
        delete(OAuthRefreshToken).where(
            OAuthRefreshToken.workspace_id == principal.workspace_id,
            OAuthRefreshToken.user_id == user_id,
        )
    )
    await session.execute(
        delete(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.workspace_id == principal.workspace_id,
            OAuthAuthorizationCode.user_id == user_id,
        )
    )
    await session.commit()


async def create_invitation(
    session: AsyncSession,
    principal: Principal,
    *,
    email: str | None,
    role: str,
    expires_in_days: int,
) -> tuple[WorkspaceInvitation, str]:
    require_role(principal, "admin")
    workspace = await _workspace(session, principal)
    if workspace.personal:
        raise WorkspaceStateError(
            "Personal workspaces can't be shared. Create a team workspace to invite people."
        )
    token = INVITATION_PREFIX + secrets.token_urlsafe(24)
    invitation = WorkspaceInvitation(
        workspace_id=principal.workspace_id,
        email=email.lower() if email else None,
        role=role,
        token_hash=hash_invitation(token),
        invited_by_id=principal.user_id,
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
    )
    session.add(invitation)
    await session.commit()
    return invitation, token


async def list_invitations(
    session: AsyncSession, principal: Principal
) -> list[WorkspaceInvitation]:
    require_role(principal, "admin")
    rows = await session.scalars(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == principal.workspace_id,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.expires_at > func.now(),
        )
        .order_by(WorkspaceInvitation.created_at.desc())
    )
    return list(rows.all())


async def revoke_invitation(
    session: AsyncSession, principal: Principal, invitation_id: uuid.UUID
) -> None:
    require_role(principal, "admin")
    result = await session.execute(
        delete(WorkspaceInvitation).where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == principal.workspace_id,
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFoundError("Invitation not found")
    await session.commit()


async def open_invitation(
    session: AsyncSession, token: str, *, lock: bool = False
) -> tuple[WorkspaceInvitation, Workspace]:
    """A pending invitation by its token (expired and used ones count as not found)."""
    if not token.startswith(INVITATION_PREFIX) or len(token) > 100:
        raise NotFoundError("This invitation link is not valid.")
    query = select(WorkspaceInvitation).where(
        WorkspaceInvitation.token_hash == hash_invitation(token)
    )
    if lock:
        query = query.with_for_update()
    invitation = await session.scalar(query)
    if invitation is None or invitation.accepted_at is not None:
        raise NotFoundError("This invitation link is not valid or was already used.")
    if invitation.expires_at <= datetime.now(UTC):
        raise NotFoundError("This invitation has expired. Ask for a new one.")
    workspace = await session.get(Workspace, invitation.workspace_id)
    if workspace is None:
        raise NotFoundError("The workspace no longer exists.")
    return invitation, workspace


async def accept_invitation(session: AsyncSession, user_id: uuid.UUID, token: str) -> uuid.UUID:
    """Join the invitation's workspace (or be promoted by it). Returns the workspace id."""
    invitation, workspace = await open_invitation(session, token, lock=True)
    existing = await session.get(WorkspaceMember, (workspace.id, user_id))
    if existing is None:
        session.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role=invitation.role)
        )
    elif existing.role == "member" and invitation.role == "admin":
        existing.role = "admin"
    else:
        raise InvalidInputError(
            "You are already a member of this workspace.", code="already_member"
        )
    invitation.accepted_at = datetime.now(UTC)
    invitation.accepted_by_id = user_id
    await session.commit()
    return workspace.id
