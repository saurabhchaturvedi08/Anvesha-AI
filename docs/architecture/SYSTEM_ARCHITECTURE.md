# System Architecture

## Overview

Anvesha is a modular monorepo that separates desktop client concerns, backend services, shared contracts, and long-running research orchestration.

## High-level layers

1. Desktop shell (Tauri + React + TypeScript)
2. Local API boundary to the backend
3. FastAPI backend with domain services and persistence
4. Research orchestration with LangGraph and durable checkpoints
5. Provider adapters for models, search, retrieval, and document ingestion
6. Local SQLite and filesystem storage for app state and research artifacts

## Desktop application layer

The desktop app provides the primary user experience and handles local file dialogs, settings, research request creation, and progress updates. It communicates with the backend over localhost using a local auth token and strict origin controls.

## Backend services layer

The backend owns the business logic and persistence concerns. It exposes endpoints for:

- Research project lifecycle
- Provider configuration and validation
- Document ingestion
- Research execution and progress stream
- Source and evidence retrieval
- Report generation and export

## Research orchestration layer

The research workflow is implemented in LangGraph with a supervisor-led graph. The workflow stays explicit and stateful, persisting checkpoints after significant transitions to allow resume and recovery.

## Storage strategy

- SQLite for structured operational data
- Filesystem for documents and generative artifacts
- SQLite FTS5 for text search
- Replaceable vector abstraction for embeddings and retrieval

## Security boundary

The backend is treated as a security boundary. It binds only to localhost, validates requests, restricts file access, redacts secrets in logs, and ensures provider calls are explicit and user-configurable.

## Deployment model

The initial release is desktop-first and self-contained. Future work may introduce server deployment modes that support multi-user operation and PostgreSQL-backed storage.
