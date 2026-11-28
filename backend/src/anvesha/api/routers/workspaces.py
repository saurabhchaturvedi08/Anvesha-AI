import uuid
from typing import cast

from fastapi import APIRouter, Response, status

from anvesha.api.deps import PrincipalDep, SessionDep, SettingsDep, SourceServiceDep
from anvesha.api.schemas.workspaces import (
    InvitableRole,
    InvitationCreate,
    InvitationCreated,
    InvitationPreview,
    InvitationRead,
    MemberRead,
    MemberUpdate,
    MeRead,
    UserRead,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceRole,
    WorkspaceUpdate,
)
from anvesha.db.models import User, WorkspaceMember
from anvesha.errors import NotFoundError
from anvesha.workspaces import (
    WorkspaceInfo,
    accept_invitation,
    change_role,
    create_invitation,
    create_workspace,
    delete_workspace,
    list_invitations,
    list_members,
    list_workspaces,
    open_invitation,
    remove_member,
    rename_workspace,
    revoke_invitation,
)

router = APIRouter(prefix="/api/v1", tags=["workspaces"])


def _read(info: WorkspaceInfo) -> WorkspaceRead:
    return WorkspaceRead(
        id=info.id,
        name=info.name,
        personal=info.personal,
        role=cast(WorkspaceRole, info.role),
        members=info.members,
    )


async def _workspace_read(
    session: SessionDep, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> WorkspaceRead:
    for info in await list_workspaces(session, user_id):
        if info.id == workspace_id:
            return _read(info)
    raise NotFoundError("Workspace not found")


def _member_read(user: User, member: WorkspaceMember) -> MemberRead:
    return MemberRead(
        user=UserRead.model_validate(user),
        role=cast(WorkspaceRole, member.role),
        joined_at=member.created_at,
    )


@router.get("/me", response_model=MeRead)
async def me(session: SessionDep, principal: PrincipalDep, settings: SettingsDep) -> MeRead:
    """The signed-in user, the workspace this request acts in, and all of theirs."""
    user = await session.get(User, principal.user_id)
    if user is None:
        raise NotFoundError("User not found")
    workspaces = [_read(info) for info in await list_workspaces(session, principal.user_id)]
    current = next((w for w in workspaces if w.id == principal.workspace_id), None)
    if current is None:
        raise NotFoundError("Workspace not found")
    return MeRead(
        auth_mode=settings.auth_mode,
        user=UserRead.model_validate(user),
        workspace=current,
        workspaces=workspaces,
    )


@router.get("/workspaces", response_model=list[WorkspaceRead])
async def workspaces(session: SessionDep, principal: PrincipalDep) -> list[WorkspaceRead]:
    return [_read(info) for info in await list_workspaces(session, principal.user_id)]


@router.post("/workspaces", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def new_workspace(
    payload: WorkspaceCreate, session: SessionDep, principal: PrincipalDep
) -> WorkspaceRead:
    """Create a shared workspace, owned by you. Switch to it with X-Anvesha-Workspace."""
    workspace_id = await create_workspace(session, principal.user_id, payload.name)
    return WorkspaceRead(
        id=workspace_id, name=payload.name, personal=False, role="owner", members=1
    )


# --- The current workspace (chosen with the X-Anvesha-Workspace header) -----------------


@router.get("/workspace", response_model=WorkspaceRead)
async def current_workspace(session: SessionDep, principal: PrincipalDep) -> WorkspaceRead:
    return await _workspace_read(session, principal.user_id, principal.workspace_id)


@router.patch("/workspace", response_model=WorkspaceRead)
async def update_workspace(
    payload: WorkspaceUpdate, session: SessionDep, principal: PrincipalDep
) -> WorkspaceRead:
    """Rename the workspace (admins)."""
    await rename_workspace(session, principal, payload.name)
    return await _workspace_read(session, principal.user_id, principal.workspace_id)


@router.delete("/workspace", status_code=status.HTTP_204_NO_CONTENT)
async def remove_workspace(
    session: SessionDep, principal: PrincipalDep, sources: SourceServiceDep
) -> Response:
    """Delete the workspace with all its projects, keys and members (owners only)."""
    projects = await delete_workspace(session, principal)
    for project_id in projects:
        await sources.delete_project_files(principal.workspace_id, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workspace/members", response_model=list[MemberRead])
async def members(session: SessionDep, principal: PrincipalDep) -> list[MemberRead]:
    return [_member_read(user, member) for user, member in await list_members(session, principal)]


@router.patch("/workspace/members/{user_id}", response_model=MemberRead)
async def update_member(
    user_id: uuid.UUID, payload: MemberUpdate, session: SessionDep, principal: PrincipalDep
) -> MemberRead:
    """Change a member's role (admins; only owners make or change owners)."""
    member = await change_role(session, principal, user_id, payload.role)
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("Member not found")
    return _member_read(user, member)


@router.delete("/workspace/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    user_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    """Remove a member (admins), or leave the workspace (your own id)."""
    await remove_member(session, principal, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workspace/invitations", response_model=list[InvitationRead])
async def invitations(session: SessionDep, principal: PrincipalDep) -> list[InvitationRead]:
    """Pending invitations (admins)."""
    return [
        InvitationRead.model_validate(row) for row in await list_invitations(session, principal)
    ]


@router.post(
    "/workspace/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def invite(
    payload: InvitationCreate, session: SessionDep, principal: PrincipalDep, settings: SettingsDep
) -> InvitationCreated:
    """Create an invitation link to pass on (admins). Whoever opens it signed in can join."""
    row, token = await create_invitation(
        session,
        principal,
        email=payload.email,
        role=payload.role,
        expires_in_days=payload.expires_in_days,
    )
    return InvitationCreated(
        **InvitationRead.model_validate(row).model_dump(),
        url=f"{settings.web_url}/invite/{token}",
    )


@router.delete("/workspace/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninvite(
    invitation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    await revoke_invitation(session, principal, invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Invitations, from the invitee's side ------------------------------------------------


@router.get("/invitations/{token}", response_model=InvitationPreview)
async def preview_invitation(
    token: str, session: SessionDep, principal: PrincipalDep
) -> InvitationPreview:
    invitation, workspace = await open_invitation(session, token)
    inviter = (
        await session.get(User, invitation.invited_by_id) if invitation.invited_by_id else None
    )
    member = await session.get(WorkspaceMember, (workspace.id, principal.user_id))
    return InvitationPreview(
        workspace_name=workspace.name,
        role=cast(InvitableRole, invitation.role),
        email=invitation.email,
        invited_by=(inviter.display_name or inviter.email) if inviter else None,
        expires_at=invitation.expires_at,
        already_member=member is not None,
    )


@router.post("/invitations/{token}/accept", response_model=WorkspaceRead)
async def join(token: str, session: SessionDep, principal: PrincipalDep) -> WorkspaceRead:
    """Join the workspace. Afterwards, switch to it with X-Anvesha-Workspace."""
    workspace_id = await accept_invitation(session, principal.user_id, token)
    return await _workspace_read(session, principal.user_id, workspace_id)
