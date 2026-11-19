import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anvesha.api.deps import PrincipalDep, SessionDep
from anvesha.api.schemas.projects import ProjectCreate, ProjectRead, ProjectUpdate
from anvesha.db.models import Project
from anvesha.tenancy import Principal

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


async def _get_project(
    session: AsyncSession, principal: Principal, project_id: uuid.UUID
) -> Project:
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id, Project.workspace_id == principal.workspace_id
        )
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    session: SessionDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Project]:
    result = await session.scalars(
        select(Project)
        .where(Project.workspace_id == principal.workspace_id)
        .order_by(Project.created_at.desc(), Project.id)
        .limit(limit)
        .offset(offset)
    )
    return result.all()


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, session: SessionDep, principal: PrincipalDep
) -> Project:
    project = Project(
        workspace_id=principal.workspace_id,
        created_by_id=principal.user_id,
        **payload.model_dump(),
    )
    session.add(project)
    await session.commit()
    return project


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Project:
    return await _get_project(session, principal, project_id)


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, session: SessionDep, principal: PrincipalDep
) -> Project:
    project = await _get_project(session, principal, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await session.commit()
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    project = await _get_project(session, principal, project_id)
    await session.delete(project)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
