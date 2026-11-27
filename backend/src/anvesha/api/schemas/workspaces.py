import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkspaceRole = Literal["owner", "admin", "member"]
InvitableRole = Literal["admin", "member"]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None


class WorkspaceRead(BaseModel):
    id: uuid.UUID
    name: str
    personal: bool
    role: WorkspaceRole
    members: int


class MeRead(BaseModel):
    """Who the request acts as, and the workspaces they can switch to."""

    auth_mode: Literal["local", "jwt"]
    user: UserRead
    workspace: WorkspaceRead
    workspaces: list[WorkspaceRead]


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class WorkspaceUpdate(WorkspaceCreate):
    pass


class MemberRead(BaseModel):
    user: UserRead
    role: WorkspaceRole
    joined_at: datetime


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: WorkspaceRole


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str | None = Field(
        default=None,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="Who it's for (shown on the invitation; not enforced).",
    )
    role: InvitableRole = "member"
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    role: InvitableRole
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


class InvitationCreated(InvitationRead):
    url: str = Field(description="The link to pass on. Shown only once.")


class InvitationPreview(BaseModel):
    workspace_name: str
    role: InvitableRole
    email: str | None
    invited_by: str | None
    expires_at: datetime
    already_member: bool
