from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = "sqlite:///./data/anvesha.db"


Path("./data").mkdir(exist_ok=True)


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
_fts5_available = False


def init_db() -> None:
    from app.models import (
        Document,
        DocumentChunk,
        ProviderConfiguration,
        ResearchCheckpoint,
        ResearchClaim,
        ResearchEvidence,
        ResearchExecution,
        ResearchProject,
        ResearchSource,
    )  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_execution_columns()
    global _fts5_available
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts "
                "USING fts5(text, content='document_chunks', content_rowid='id')"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS document_chunks_ai AFTER INSERT ON document_chunks BEGIN "
                "INSERT INTO document_chunks_fts(rowid, text) VALUES (new.id, new.text); END"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS document_chunks_ad AFTER DELETE ON document_chunks BEGIN "
                "INSERT INTO document_chunks_fts(document_chunks_fts, rowid, text) "
                "VALUES ('delete', old.id, old.text); END"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS document_chunks_au AFTER UPDATE ON document_chunks BEGIN "
                "INSERT INTO document_chunks_fts(document_chunks_fts, rowid, text) "
                "VALUES ('delete', old.id, old.text); "
                "INSERT INTO document_chunks_fts(rowid, text) VALUES (new.id, new.text); END"
            )
        _fts5_available = True
    except OperationalError as exc:
        if "fts5" not in str(exc).casefold():
            raise
        _fts5_available = False


def _ensure_execution_columns() -> None:
    inspector = inspect(engine)
    if "research_executions" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("research_executions")}
    migrations = {
        "budget_limit": "ALTER TABLE research_executions ADD COLUMN budget_limit INTEGER",
        "budget_used": "ALTER TABLE research_executions ADD COLUMN budget_used INTEGER NOT NULL DEFAULT 0",
        "job_status": "ALTER TABLE research_executions ADD COLUMN job_status VARCHAR(40) NOT NULL DEFAULT 'queued'",
        "job_id": "ALTER TABLE research_executions ADD COLUMN job_id VARCHAR(64)",
        "metrics_json": "ALTER TABLE research_executions ADD COLUMN metrics_json TEXT NOT NULL DEFAULT '{}'",
    }
    with engine.begin() as connection:
        for column_name, ddl in migrations.items():
            if column_name not in existing_columns:
                connection.execute(text(ddl))


def is_fts5_available() -> bool:
    return _fts5_available


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
