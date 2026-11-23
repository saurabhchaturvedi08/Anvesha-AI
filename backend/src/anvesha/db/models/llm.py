import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
)
from sqlalchemy.orm import Mapped, mapped_column

from anvesha.db.base import Base, Timestamps, UUIDPrimaryKey
from anvesha.llm.types import MODEL_ROLES


class ProviderCredential(UUIDPrimaryKey, Timestamps, Base):
    """A workspace's own access to one model provider (bring-your-own-key)."""

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider"),
        CheckConstraint("status IN ('valid', 'invalid', 'unverified')", name="status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))
    # AES-GCM ciphertext from anvesha.crypto; NULL for providers that need no key.
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    # Last 4 characters, so people can tell keys apart. Never the full key.
    key_hint: Mapped[str | None] = mapped_column(String(8))
    api_base: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), default="unverified")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ModelPreference(Timestamps, Base):
    """Which provider and model each role (agents, embeddings) uses in a workspace."""

    __tablename__ = "model_preferences"
    __table_args__ = (CheckConstraint(f"role IN {MODEL_ROLES!r}", name="role"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(255))


class UsageEvent(Base):
    """One model call: tokens, cost, latency, outcome. Feeds budgets and billing."""

    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'error')", name="status"),
        Index("ix_usage_events_workspace_id_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    # Set for calls made by a research run; its budget is rebuilt from these after restarts.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    role: Mapped[str | None] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(255))
    # "workspace" (their own key) or "deployment" (the server's key) — matters for billing.
    credential_source: Mapped[str] = mapped_column(String(16))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # NULL when the model's price is unknown.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
