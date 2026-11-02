# ADR-001: Desktop Architecture

## Status

Accepted

## Context

Anvesha is a local-first research platform and the desktop client should be the primary product surface. The app must support local model use, local-file access, and a reliable local backend without requiring a database server.

## Decision

Use Tauri 2 with React, TypeScript, and Vite for the desktop shell. The backend will run as a sidecar Python process with a localhost-only API boundary. This keeps the user setup simple while preserving separation between presentation logic and research business logic.

## Consequences

### Positive

- Strong desktop UX and local file integration
- Native installation story for Windows, macOS, and Linux
- Simplified local-first operation with no mandatory server dependency

### Negative

- Cross-platform packaging complexity
- Additional build and runtime dependencies
- Need to manage backend startup and lifecycle carefully
