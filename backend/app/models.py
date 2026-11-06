from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProviderConfiguration(Base):
    __tablename__ = "provider_configurations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider_name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(40), default="not_tested")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchProject(Base):
    __tablename__ = "research_projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    research_depth: Mapped[str] = mapped_column(String(40), default="standard")
    preferred_source_types: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(40), default="created")
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchSource(Base):
    __tablename__ = "research_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ResearchExecution(Base):
    __tablename__ = "research_executions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    project_id: Mapped[str] = mapped_column(ForeignKey("research_projects.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="running")
    current_node: Mapped[str] = mapped_column(String(80), default="plan")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ResearchCheckpoint(Base):
    __tablename__ = "research_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("research_executions.id"), index=True, nullable=False)
    node: Mapped[str] = mapped_column(String(80), nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ResearchClaim(Base):
    __tablename__ = "research_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("research_projects.id"), index=True, nullable=False)
    execution_id: Mapped[str] = mapped_column(ForeignKey("research_executions.id"), index=True, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="unverified")
    verification_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ResearchEvidence(Base):
    __tablename__ = "research_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("research_projects.id"), index=True, nullable=False)
    execution_id: Mapped[str] = mapped_column(ForeignKey("research_executions.id"), index=True, nullable=False)
    claim_id: Mapped[int] = mapped_column(ForeignKey("research_claims.id"), index=True, nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("research_sources.id"), index=True, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[str] = mapped_column(String(24), default="supports")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
