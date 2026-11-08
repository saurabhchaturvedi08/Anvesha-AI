# Development Roadmap

## Repository assessment

The repository began empty, which is a good starting point for a clean, disciplined architecture. The first task is to create the engineering foundation: the Tauri desktop app, FastAPI backend, documentation, and a reproducible local development setup.

## Phase 0 – Repository foundation and scaffolding

### Goal

Establish working local development foundations for the desktop app and backend.

### Deliverables

- Monorepo directories and documentation
- Desktop shell setup with Vite and Tauri
- Backend skeleton with FastAPI
- Local config and environment defaults
- Lint/test scaffolding

### Milestone checklist

- [ ] Inspect repository and confirm environment
- [ ] Create architecture and product docs
- [ ] Scaffold desktop app shell
- [ ] Scaffold backend app shell
- [ ] Configure development scripts and environment variables
- [ ] Validate desktop and backend start-up behavior

## Phase 1 – Desktop MVP foundation

- Settings panel
- Provider configuration
- Ollama connectivity
- Model health checks
- Local data persistence

## Phase 2 – Research engine

- Model abstraction
- Search providers
- Research planning workflow
- Single-project execution

## Phase 3 – Multi-agent orchestration

- Supervisor and worker agents
- Evidence storage
- Claim verification
- Conditional branching and resume behavior

### Implemented foundation

- A supervised LangGraph workflow checkpoints each completed stage
- Structured claims are verified against exact source-snippet quotes before being marked supported
- Supported claims persist as evidence linked to both the source and execution
- Runs with no sources or no supported claims branch to explicit incomplete reports
- Failed or interrupted runs can resume from their most recent saved next-node state

## Phase 4 – Knowledge and evidence

- Documents
- Embeddings
- Retrieval
- Follow-up research

## Phase 5 – Production reliability

- Durable execution
- Background job records
- Budget enforcement
- Evaluation and observability

## Phase 6 – Desktop polish

- Workspace UX
- Evidence explorer
- Export flows
- Packaging and installers

### Implemented foundation

- Project-scoped evidence explorer with claim status and source quote filtering
- Markdown research report and JSON evidence bundle exports
- Functional in-page navigation for research, workspace, history, and local knowledge
- Keyboard-visible focus states for interactive controls
- Windows MSI and NSIS installer targets configured and locally validated (unsigned)

## Phase 7 – Open-source launch

- Release docs
- Contributor workflows
- Packaging validation
- v0.1.0 public release

## Risks, dependencies, and challenges

- Rust installation and Cross-platform Tauri setup
- Local model compatibility and provider selection
- Search provider API normalization and site restrictions
- SQLite + FTS + vector abstraction design
- Durable workflow checkpoints without over-engineering
- Avoiding fake progress and unsupported claims

## Implementation checklist for the first milestone

- [ ] Repository assessment complete
- [ ] Architecture documentation prepared
- [ ] Desktop app scaffolded
- [ ] Backend scaffolded
- [ ] Local startup verified
- [ ] Remaining issues documented for Phase 1
