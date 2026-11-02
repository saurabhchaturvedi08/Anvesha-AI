# Product Requirements Document (PRD)

## Repository assessment

The repository was effectively empty when the project began, so the first obligation was to establish a disciplined engineering foundation instead of building feature work prematurely. The chosen stack aligns with the product goals: a Tauri desktop shell for local-first installation, a FastAPI backend for research orchestration and persistence, and a modular, local-first data model for evidence, research artifacts, and model provider configuration.

## Product goal

Anvesha is an open-source, local-first AI research platform that transforms complex questions into structured, evidence-backed intelligence with transparent agent workflows, durable checkpoints, and local data control.

## Core user value

- Research tasks move from vague questions to structured plans and inspected evidence
- Users can work with local models or cloud providers without forcing a cloud-only setup
- Research outputs are traceable to citations and stored source material
- A desktop-first user experience supports independent research work without a mandatory database server

## Release priorities for v0.1.0

1. Installable desktop experience
2. Local backend and database startup
3. Provider configuration for Ollama and at least one hosted model provider
4. Search provider configuration
5. Research plan generation and execution pipeline
6. Source persistence and evidence tracking
7. Report export and reopening prior projects
8. Automated tests and build validation

## Scope boundaries

The first major milestone intentionally excludes a full web client, mobile app, and extensive MCP ecosystem expansion. The architecture is designed for those future clients, but the desktop-first MVP must be reliable before new surface area is added.

## Risks and constraints

- Local model quality varies widely by hardware and runtime
- Search and retrieval providers create external dependencies
- Multi-agent execution may become costly without strict budgets and retries
- Evidence quality requires structured validation, not model confidence alone
- Cross-platform packaging and secret management add operational complexity

## Success indicators

- A user can install and launch Anvesha locally
- The app starts without manual Python setup
- A research plan is generated and persisted
- Evidence and source records are created and reviewable
- Reports remain exportable and reopenable
- Interruptions are recoverable through checkpoints and execution records
