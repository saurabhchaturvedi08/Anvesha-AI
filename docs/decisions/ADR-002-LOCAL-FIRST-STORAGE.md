# ADR-002: Local-First Storage

## Status

Accepted

## Context

The product requirement says users should keep research documents, history, reports, and embeddings on their machine by default. There should be no mandatory cloud backend and no mandatory account.

## Decision

Use SQLite for core application state, local files for artifacts and documents, FTS5 for search, and an abstraction layer for vector storage. The default configuration will keep data local unless a user explicitly chooses an alternative storage strategy.

## Consequences

### Positive

- Strong privacy defaults
- Easy desktop startup without database services
- Local reproducibility for research artifacts

### Negative

- Local storage limits and file management complexity
- Retrieval performance depends on implementation quality
- Future server deployments require adapter work for different storage backends
