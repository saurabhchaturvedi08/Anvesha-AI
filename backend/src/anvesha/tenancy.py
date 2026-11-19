import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.db.models import User, Workspace, WorkspaceMember


@dataclass(frozen=True)
class Principal:
    """Who is acting, and in which workspace. Every data access is scoped by this."""

    user_id: uuid.UUID
    workspace_id: uuid.UUID


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
        workspace = Workspace(name="Personal")
        session.add(workspace)
        await session.flush()
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role="owner"))
        workspace_id = workspace.id

    await session.commit()
    return Principal(user_id=user_id, workspace_id=workspace_id)
