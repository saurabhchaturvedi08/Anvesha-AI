"""Research runs: the plan, the agents' activity log, the evidence they found, and reports."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey
from anvesha.db.models.research import DEPTHS

RUN_STATUSES = ("queued", "running", "awaiting_approval", "completed", "failed", "cancelled")
# A project has at most one run in one of these states at a time.
ACTIVE_RUN_STATUSES = ("queued", "running", "awaiting_approval")
SUB_QUESTION_STATUSES = ("pending", "researching", "done", "failed")
COVERAGES = ("answered", "partial", "not_found")
# quote_verified: the quote is in the source text (checked in code).
# verified: additionally, the verifier model agreed the quote supports the statement.
# rejected: the verifier model found the quote doesn't support the statement.
FINDING_STATUSES = ("quote_verified", "verified", "rejected")
REPORT_STATUSES = ("complete", "incomplete")


class Run(UUIDPrimaryKey, Timestamps, Base):
    """One execution of the research agents for a project."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES!r}", name="status"),
        CheckConstraint(f"depth IN {DEPTHS!r}", name="depth"),
        Index("ix_runs_project_id_created_at", "project_id", "created_at"),
        # Two runs writing to the same project at once would race on sources and reports.
        Index(
            "uq_runs_project_id_active",
            "project_id",
            unique=True,
            postgresql_where=text(f"status IN {ACTIVE_RUN_STATUSES!r}"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # The research question at the time the run started (the project's may change later).
    question: Mapped[str] = mapped_column(Text)
    depth: Mapped[str] = mapped_column(String(16))
    # Skip plan approval and run straight through.
    autopilot: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    max_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    # The current plan: {"strategy": str, "sub_questions": [{"question", "rationale"}]}.
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The user's answer to a pending approval, waiting for the worker to pick it up.
    pending_decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # How many times the worker has started executing this run (retries included).
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Refreshed by the worker while it executes the run; a stale value means the worker
    # died and the run should be picked up again.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    """The run's activity log. Streamed live to clients and replayed when they reconnect."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(48))
    # Which agent produced it, e.g. "lead" or "researcher:2".
    agent: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SubQuestion(UUIDPrimaryKey, Timestamps, Base):
    """One part of the research plan, investigated by one researcher agent."""

    __tablename__ = "sub_questions"
    __table_args__ = (
        UniqueConstraint("run_id", "round", "ordinal"),
        CheckConstraint(f"status IN {SUB_QUESTION_STATUSES!r}", name="status"),
        CheckConstraint(f"coverage IS NULL OR coverage IN {COVERAGES!r}", name="coverage"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    # 1 for the initial plan; later rounds fill gaps found when reflecting.
    round: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    coverage: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text)
    gaps: Mapped[list[str]] = mapped_column(JSONB, default=list)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class Finding(UUIDPrimaryKey, Timestamps, Base):
    """A statement backed by a quote that appears word for word in a saved source."""

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("run_id", "source_id", "quote_hash"),
        CheckConstraint(f"status IN {FINDING_STATUSES!r}", name="status"),
        Index("ix_findings_project_id_created_at", "project_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    sub_question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sub_questions.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    statement: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    # sha256 of the normalised quote, so the same passage isn't recorded twice in a run.
    quote_hash: Mapped[str] = mapped_column(String(64))
    # [[start, end], ...] character offsets of the quote in the source text.
    spans: Mapped[list[list[int]]] = mapped_column(JSONB, default=list)
    page: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="quote_verified")
    judge_note: Mapped[str | None] = mapped_column(Text)


class Report(UUIDPrimaryKey, Timestamps, Base):
    """The written result of a run, citing only verified findings."""

    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        CheckConstraint(f"status IN {REPORT_STATUSES!r}", name="status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), unique=True
    )
    # 1, 2, 3... per project, in the order reports were written.
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(500))
    markdown: Mapped[str] = mapped_column(Text)
    # [{"number", "source_id", "title", "url", "finding_ids"}], in citation order.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SchemaComponent(Base):
    """Schema versions of bundled libraries that manage their own tables (job queue)."""

    __tablename__ = "schema_components"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
