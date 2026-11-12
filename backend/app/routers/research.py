from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import PurePath
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Document,
    DocumentChunk,
    ResearchCheckpoint,
    ResearchClaim,
    ResearchEvidence,
    ResearchExecution,
    ResearchProject,
    ResearchSource,
)
from app.schemas import (
    DocumentIngestRequest,
    DocumentSearchRequest,
    ResearchExecutionRequest,
    ResearchExecutionResult,
    ResearchPlan,
    ResearchProjectCreate,
)
from app.services.document_followup import run_document_followup
from app.services.document_retrieval import OpenAICompatibleEmbedder, retrieve_project_chunks, split_into_chunks
from app.services.providers import list_providers, validate_provider
from app.services.research_graph import research_graph
from app.services.research_service import generate_agentic_research_plan, serialize_plan

router = APIRouter(prefix="/research", tags=["research"])

MAX_DOCUMENT_BYTES = 2_000_000


def _calculate_budget_usage(result: dict[str, Any], persisted_claims: list[dict[str, Any]]) -> int:
    tasks = result.get("plan", {}).get("tasks", []) if isinstance(result.get("plan"), dict) else []
    source_count = len(result.get("sources", []))
    claim_count = len(persisted_claims)
    return source_count + len(tasks) + (claim_count * 2)


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

    plan, _planning_status = generate_agentic_research_plan(
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


def _persist_execution_result(
    project: ResearchProject,
    execution: ResearchExecution,
    result: dict[str, Any],
    db: Session,
) -> dict[str, Any]:
    project.plan = json.dumps(result["plan"], ensure_ascii=False)
    project.report = result["report"]
    project.status = "completed"

    budget_limit = execution.budget_limit
    budget_used = _calculate_budget_usage(result, [])

    if budget_limit is not None:
        budget_used = _calculate_budget_usage(result, [])
    else:
        budget_used = 0

    source_rows: list[ResearchSource] = []
    source_ids: list[int] = []
    for source in result.get("sources", []):
        row = db.query(ResearchSource).filter(
            ResearchSource.project_id == project.id,
            ResearchSource.url == source["url"],
        ).first()
        if row is None:
            row = ResearchSource(
                project_id=project.id,
                title=source["title"],
                url=source["url"],
                snippet=source.get("snippet", ""),
                provider="tavily",
            )
            db.add(row)
        else:
            row.title = source["title"]
            row.snippet = source.get("snippet", "")
        source_rows.append(row)
    db.flush()
    source_ids = [row.id for row in source_rows]

    persisted_claims: list[dict[str, Any]] = []
    for claim_data in result.get("claims", []):
        claim = ResearchClaim(
            project_id=project.id,
            execution_id=execution.id,
            statement=claim_data["statement"],
            status=claim_data["status"],
            verification_note=claim_data["verification_note"],
        )
        db.add(claim)
        db.flush()
        evidence_id = None
        source_index = claim_data["source_index"] - 1
        if (
            claim_data["status"] == "supported"
            and 0 <= source_index < len(source_rows)
            and claim_data["quote"].casefold() in source_rows[source_index].snippet.casefold()
        ):
            evidence = ResearchEvidence(
                project_id=project.id,
                execution_id=execution.id,
                claim_id=claim.id,
                source_id=source_ids[source_index],
                quote=claim_data["quote"],
                relation="supports",
            )
            db.add(evidence)
            db.flush()
            evidence_id = evidence.id
        persisted_claims.append(
            {
                "id": claim.id,
                "statement": claim.statement,
                "status": claim.status,
                "verification_note": claim.verification_note,
                "source_url": claim_data.get("source_url"),
                "evidence_id": evidence_id,
            }
        )

    budget_used = _calculate_budget_usage(result, persisted_claims)
    execution.budget_used = budget_used
    execution.metrics_json = json.dumps(
        {
            "source_count": len(result.get("sources", [])),
            "claim_count": len(persisted_claims),
            "task_count": len(result.get("plan", {}).get("tasks", [])) if isinstance(result.get("plan"), dict) else 0,
            "budget_limit": budget_limit,
            "budget_used": budget_used,
            "job_status": execution.job_status,
        },
        ensure_ascii=False,
    )

    if budget_limit is not None and budget_used > budget_limit:
        execution.job_status = "budget_exceeded"
        project.status = "blocked"
        execution.error = "Budget limit exceeded during execution."
        if result.get("report"):
            result["report"] = f"{result['report']}\n\nBudget note: the execution exceeded the configured limit of {budget_limit}."
        project.report = result["report"]
    else:
        execution.job_status = "completed"

    execution.status = "completed"
    execution.current_node = "__end__"
    execution.state_json = json.dumps(result, ensure_ascii=False)
    execution.completed_at = datetime.utcnow()

    budget_status = "exceeded" if budget_limit is not None and budget_used > budget_limit else "within_limit"
    summary = {
        "status": project.status,
        "tasks_completed": len(result.get("plan", {}).get("tasks", [])) if isinstance(result.get("plan"), dict) else 0,
        "source_count": len(result.get("sources", [])),
        "claim_count": len(persisted_claims),
        "verified_claim_count": sum(claim["status"] == "supported" for claim in persisted_claims),
        "research_status": result.get("research_status", "insufficient_evidence"),
        "budget_limit": budget_limit,
        "budget_used": budget_used,
        "budget_status": budget_status,
        "job_status": execution.job_status,
    }
    db.commit()
    return ResearchExecutionResult(
        project_id=project.id,
        execution_id=execution.id,
        status=project.status,
        summary=summary,
        report=result["report"],
        sources=len(result.get("sources", [])),
        source_details=result.get("sources", []),
        plan=result["plan"],
        research_findings=result.get("research_findings", []),
        task_coverage=result.get("task_coverage", []),
        claims=persisted_claims,
        analysis=result.get("analysis", []),
        agent_trace=result.get("agent_trace", []),
        search_status=result.get("search_status", "unknown"),
        model_status=result.get("model_status", "not_run"),
    ).model_dump(mode="json")


def _run_execution(
    project: ResearchProject,
    execution: ResearchExecution,
    state: dict[str, Any],
    db: Session,
) -> dict[str, Any]:
    def save_checkpoint(node: str, checkpoint_state: dict[str, Any]) -> None:
        execution.current_node = checkpoint_state.get("resume_from", "__end__")
        execution.state_json = json.dumps(checkpoint_state, ensure_ascii=False)
        execution.job_status = "running"
        execution.status = "running"
        db.add(ResearchCheckpoint(execution_id=execution.id, node=node, state_json=execution.state_json))
        db.commit()

    try:
        execution.job_status = "running"
        execution.job_id = execution.job_id or f"job-{execution.id}"
        result = research_graph.invoke(
            state,
            config={"configurable": {"checkpoint_callback": save_checkpoint}},
        )
    except Exception as exc:
        execution.status = "failed"
        execution.job_status = "failed"
        execution.error = f"{exc.__class__.__name__}: {exc}"[:2000]
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"execution_id": execution.id, "message": "Research execution failed and can be resumed."},
        ) from exc

    return _persist_execution_result(project, execution, result, db)


@router.post("/projects/{project_id}/execute")
async def execute_project_with_budget(
    project_id: str,
    payload: ResearchExecutionRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")

    budget_limit = payload.budget_limit if payload is not None else None

    initial_state = {
        "project_id": project.id,
        "title": project.title,
        "objective": project.objective,
        "research_depth": project.research_depth,
        "preferred_sources": json.loads(project.preferred_source_types or "[]"),
    }
    execution = ResearchExecution(
        project_id=project.id,
        status="running",
        current_node="plan",
        state_json=json.dumps(initial_state, ensure_ascii=False),
        budget_limit=budget_limit,
        budget_used=0,
        job_status="queued",
        job_id=f"job-{uuid4().hex[:12]}",
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)
    execution.job_id = execution.job_id or f"job-{execution.id}"
    initial_state["execution_id"] = execution.id
    execution.state_json = json.dumps(initial_state, ensure_ascii=False)
    execution.job_status = "running"
    db.commit()
    return _run_execution(project, execution, initial_state, db)


@router.post("/projects/{project_id}/executions/{execution_id}/resume")
async def resume_project_execution(
    project_id: str,
    execution_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    execution = (
        db.query(ResearchExecution)
        .filter(ResearchExecution.id == execution_id, ResearchExecution.project_id == project_id)
        .first()
    )
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research execution not found")
    if execution.status not in {"failed", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only incomplete executions can be resumed")
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")

    state = json.loads(execution.state_json)
    state["resume_from"] = execution.current_node if execution.current_node != "__end__" else "report"
    execution.status = "running"
    execution.error = None
    db.commit()
    return _run_execution(project, execution, state, db)


@router.get("/projects/{project_id}/executions")
async def list_project_executions(project_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")
    executions = (
        db.query(ResearchExecution)
        .filter(ResearchExecution.project_id == project_id)
        .order_by(ResearchExecution.started_at.desc())
        .all()
    )
    return [
        {
            "id": execution.id,
            "status": execution.status,
            "current_node": execution.current_node,
            "checkpoint_count": db.query(ResearchCheckpoint)
            .filter(ResearchCheckpoint.execution_id == execution.id)
            .count(),
            "job_status": execution.job_status,
            "job_id": execution.job_id,
            "budget_limit": execution.budget_limit,
            "budget_used": execution.budget_used,
            "error": execution.error,
            "started_at": execution.started_at.isoformat(),
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        }
        for execution in executions
    ]


@router.get("/projects/{project_id}/claims")
async def list_project_claims(project_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    claims = (
        db.query(ResearchClaim)
        .filter(ResearchClaim.project_id == project_id)
        .order_by(ResearchClaim.created_at.asc())
        .all()
    )
    return [
        {
            "id": claim.id,
            "execution_id": claim.execution_id,
            "statement": claim.statement,
            "status": claim.status,
            "verification_note": claim.verification_note,
        }
        for claim in claims
    ]


@router.get("/projects/{project_id}/evidence")
async def list_project_evidence(project_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    evidence_rows = (
        db.query(ResearchEvidence)
        .filter(ResearchEvidence.project_id == project_id)
        .order_by(ResearchEvidence.created_at.asc())
        .all()
    )
    return [
        {
            "id": evidence.id,
            "execution_id": evidence.execution_id,
            "claim_id": evidence.claim_id,
            "source_id": evidence.source_id,
            "quote": evidence.quote,
            "relation": evidence.relation,
        }
        for evidence in evidence_rows
    ]


@router.get("/projects/{project_id}/sources")
async def list_project_sources(project_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")

    sources = (
        db.query(ResearchSource)
        .filter(ResearchSource.project_id == project_id)
        .order_by(ResearchSource.created_at.asc())
        .all()
    )
    return [
        {
            "id": source.id,
            "title": source.title,
            "url": source.url,
            "snippet": source.snippet,
            "provider": source.provider,
        }
        for source in sources
    ]


@router.post("/projects/{project_id}/documents", status_code=status.HTTP_201_CREATED)
async def ingest_project_document(
    project_id: str,
    payload: DocumentIngestRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")

    filename = PurePath(payload.filename.replace("\\", "/")).name
    if not filename or PurePath(filename).suffix.casefold() not in {".txt", ".md"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Only .txt and .md documents are supported")
    content = payload.content.strip()
    content_bytes = content.encode("utf-8")
    if not content_bytes or len(content_bytes) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Document content must be under 2 MB")

    content_hash = hashlib.sha256(content_bytes).hexdigest()
    existing = (
        db.query(Document)
        .filter(Document.project_id == project_id, Document.content_sha256 == content_hash)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This document is already in the project")

    chunks = split_into_chunks(content)
    embedder = OpenAICompatibleEmbedder()
    embeddings: list[list[float] | None] = [None] * len(chunks)
    embedding_status = "not_configured"
    if embedder.model:
        embedding_status = "ready"
        for offset in range(0, len(chunks), 64):
            batch, batch_status = embedder.embed(chunks[offset : offset + 64])
            if batch is None:
                embeddings = [None] * len(chunks)
                embedding_status = batch_status
                break
            embeddings[offset : offset + len(batch)] = batch

    document = Document(
        project_id=project_id,
        filename=filename,
        content_sha256=content_hash,
        content_bytes=len(content_bytes),
    )
    db.add(document)
    db.flush()
    db.add_all(
        [
            DocumentChunk(
                document_id=document.id,
                project_id=project_id,
                ordinal=ordinal,
                text=chunk,
                embedding_json=json.dumps(embedding) if embedding is not None else None,
            )
            for ordinal, (chunk, embedding) in enumerate(zip(chunks, embeddings))
        ]
    )
    db.commit()
    db.refresh(document)
    return {
        "id": document.id,
        "project_id": project_id,
        "filename": document.filename,
        "content_bytes": document.content_bytes,
        "chunks_created": len(chunks),
        "embedding_status": embedding_status,
    }


@router.get("/projects/{project_id}/documents")
async def list_project_documents(project_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")
    documents = (
        db.query(Document)
        .filter(Document.project_id == project_id)
        .order_by(Document.created_at.desc())
        .all()
    )
    return [
        {
            "id": document.id,
            "filename": document.filename,
            "content_bytes": document.content_bytes,
            "chunk_count": db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).count(),
            "created_at": document.created_at.isoformat(),
        }
        for document in documents
    ]


@router.post("/projects/{project_id}/retrieve")
async def retrieve_project_documents(
    project_id: str,
    payload: DocumentSearchRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")
    retrieved = retrieve_project_chunks(db, project_id, payload.query, payload.limit)
    return {"project_id": project_id, "query": payload.query, **retrieved}


@router.post("/projects/{project_id}/follow-up")
async def follow_up_research(
    project_id: str,
    payload: DocumentSearchRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research project not found")

    return {
        "project_id": project_id,
        "query": payload.query,
        **run_document_followup(db, project_id, payload.query, payload.limit),
    }


@router.get("/providers")
async def get_providers() -> list[dict[str, Any]]:
    return list_providers()


@router.post("/providers/{provider_id}/validate")
async def validate_provider_status(provider_id: str) -> dict[str, Any]:
    return validate_provider(provider_id)
