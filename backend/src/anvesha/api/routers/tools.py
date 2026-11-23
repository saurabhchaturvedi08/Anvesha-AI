import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from anvesha.api.deps import (
    DatabaseDep,
    GatewayDep,
    HttpDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    SourceServiceDep,
    ToolsDep,
    VaultDep,
)
from anvesha.sources.service import require_project
from anvesha.tools import ToolContext

router = APIRouter(tags=["tools"])


class ToolRead(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ToolInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultRead(BaseModel):
    content: str
    data: dict[str, Any]
    is_error: bool
    error_code: str | None
    retryable: bool


@router.get("/api/v1/tools", response_model=list[ToolRead])
async def list_tools(tools: ToolsDep) -> list[ToolRead]:
    """The tools research agents can call, with their JSON Schema parameters."""
    return [ToolRead(**spec.__dict__) for spec in tools.specs()]


@router.post("/api/v1/projects/{project_id}/tools/{name}", response_model=ToolResultRead)
async def invoke_tool(
    project_id: uuid.UUID,
    name: str,
    payload: ToolInvocation,
    session: SessionDep,
    principal: PrincipalDep,
    tools: ToolsDep,
    db: DatabaseDep,
    settings: SettingsDep,
    http: HttpDep,
    vault: VaultDep,
    gateway: GatewayDep,
    sources: SourceServiceDep,
) -> ToolResultRead:
    """Run a tool exactly as an agent would, and see what the agent would read.

    Tool failures come back with ``is_error`` set (status 200), the same way agents see them.
    """
    if tools.get(name) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown tool {name!r}")
    await require_project(session, principal.workspace_id, project_id)
    context = ToolContext(
        workspace_id=principal.workspace_id,
        project_id=project_id,
        db=db,
        settings=settings,
        http=http,
        vault=vault,
        gateway=gateway,
        sources=sources,
        added_by="user",
    )
    result = await tools.invoke(name, payload.arguments, context)
    return ToolResultRead(**result.__dict__)
