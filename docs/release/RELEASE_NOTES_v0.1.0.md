# Anvesha v0.1.0 release notes

## Overview

Anvesha is a local-first AI research platform designed for evidence-backed investigation and structured knowledge work. This first milestone focuses on a stable desktop workflow, local document ingestion, and a resilient research engine that avoids unsupported claims.

## Included in this release

- Desktop research workspace for project and execution management
- Local-first backend with SQLite persistence and durable checkpoints
- Research orchestration with LangGraph-based planning and execution flows
- Local document upload and project-scoped retrieval
- Evidence and claim tracking tied to exact source snippets
- Markdown report export and JSON evidence bundle export
- Windows installer targets for NSIS and MSI packaging

## Supported workflows

- Create and manage research projects locally
- Run structured research tasks against user-selected model and search providers
- Ingest project documents and retrieve them by local knowledge context
- Resume interrupted work from the latest persisted checkpoint
- Review claims and supporting evidence before final reporting

## Known limitations

- The initial release is optimized for local and desktop usage, not a shared cloud deployment
- Model and search provider configuration is still environment-dependent
- Packaging is validated for Windows installer generation; broader OS signing and distribution workflows may require additional platform-specific steps
- This is an early milestone release intended for testing, feedback, and refinement

## Requirements

- Windows 10 or later for the packaged desktop app
- Node.js 20+
- Python 3.11+
- Rust stable toolchain for Tauri development and packaging

## Upgrade and usage notes

This release is intended for local evaluation and early adoption. Users should configure provider access and validate model connectivity before starting long-running research sessions.

## Feedback and contribution

We welcome bug reports, feature requests, and contribution ideas. See [CONTRIBUTING.md](../../CONTRIBUTING.md) and [SECURITY.md](../../SECURITY.md) for the project workflow and security policy.
