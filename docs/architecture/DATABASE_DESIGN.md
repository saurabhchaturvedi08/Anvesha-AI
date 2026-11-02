# Database Design

## Storage principles

- Default to SQLite for local-first installation
- Keep application metadata separate from secret storage
- Use durable metadata records linked to filesystem artifacts
- Support safe migrations and schema versioning

## Core tables

- ApplicationSettings
- ModelProvider
- ResearchProject
- ResearchSession
- ResearchTask
- ResearchExecution
- ResearchCheckpointReference
- ResearchSource
- ResearchClaim
- ResearchEvidence
- ResearchReport
- Document
- DocumentChunk
- AgentConfiguration
- ToolConfiguration
- UsageRecord

## Key design decisions

- Research objects are persisted with explicit status fields and versioning
- Large artifacts are stored on disk and referenced by database records
- Evidence, claim, and source relationships are modeled as normalized tables
- FTS5 is used for text queries and structured filtering for local content
- Embedding storage is abstracted behind a vector-store interface for later replacement

## Relationship model

A research project owns sessions, tasks, sources, evidence, and final reports. Each source can support multiple evidence records, while claims are linked to supporting and contradicting evidence.

## Migration guidance

Alembic migrations should remain backward-conscious. When schema changes are introduced, app code must support safe upgrades and cleanup of orphaned artifacts.

## Backup and restore

The desktop app should expose a local data export and restore workflow, including research records and generated artifacts, without requiring a server database administrator.
