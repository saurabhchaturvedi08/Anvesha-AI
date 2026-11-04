from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ResearchProject
from app.schemas import ResearchExecutionResult, ResearchPlan, ResearchProjectCreate
from app.services.providers import list_providers, validate_provider
from app.services.research_service import execute_research_workflow, generate_research_plan, serialize_plan

router = APIRouter(prefix="/research", tags=["research"])


@router.get("/projects")
async def list_research_projects(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    projects = db.query(ResearchProject).order_by(ResearchProject.created_at.desc()).all()
    return [
        {
            "id": project.id,
            "title": project.title,
            "objective": project.objective,
            "research_depth": project.research_depth,
            "status": project.status,
            "preferred_source_types": json.loads(project.preferred_source_types or "[]"),
        }
        for project in projects
    ]


@router.post("/projects")
async def create_research_project(payload: ResearchProjectCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = ResearchProject(
        title=payload.title,
        objective=payload.objective,
        research_depth=payload.research_depth,
        preferred_source_types=json.dumps(payload.preferred_source_types),
        status="created",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return {
        "id": project.id,
        "title": project.title,
        "objective": project.objective,
        "research_depth": project.research_depth,
        "status": project.status,
        "preferred_source_types": payload.preferred_source_types,
    }


@router.post("/projects/{project_id}/plan")
async def generate_project_plan(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")

    plan = generate_research_plan(
        project_id=project.id,
        title=project.title,
        objective=project.objective,
        research_depth=project.research_depth,
        preferred_sources=json.loads(project.preferred_source_types or "[]"),
    )
    project.plan = serialize_plan(plan)
    project.status = "planned"
    db.commit()
    return plan.model_dump(mode="json")


@router.post("/projects/{project_id}/execute")
async def execute_project(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")

    result = execute_research_workflow(
        project_id=project.id,
        title=project.title,
        objective=project.objective,
        preferred_sources=json.loads(project.preferred_source_types or "[]"),
    )
    project.report = result.report
    project.status = "completed"
    db.commit()
    return result.model_dump(mode="json")


@router.get("/providers")
async def get_providers() -> list[dict[str, Any]]:
    return list_providers()


@router.post("/providers/{provider_id}/validate")
async def validate_provider_status(provider_id: str) -> dict[str, Any]:
    return validate_provider(provider_id)
