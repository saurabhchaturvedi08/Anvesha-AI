# Anvesha

Anvesha is an open-source, local-first agentic AI research platform for structured, evidence-backed investigations.

## Mission

Anvesha helps users turn broad research questions into structured, verifiable intelligence using local-first storage, provider-agnostic AI models, and a controlled research workflow.

## Current status

This repository is in the Phase 7 open-source launch preparation phase. The project is being scaffolded around:

- Tauri 2 desktop app
- React + TypeScript + Vite frontend
- FastAPI backend with SQLAlchemy and SQLite
- LangGraph-based research workflow
- Local-first document and evidence storage
- Release-ready contributor workflow and packaging validation

## Repository layout

```text
anvesha/
├── apps/
│   └── desktop/
├── backend/
│   ├── app/
│   └── tests/
├── packages/
│   ├── shared-types/
│   ├── api-contracts/
│   └── ui/
├── docs/
│   ├── product/
│   ├── architecture/
│   ├── agents/
│   ├── development/
│   └── decisions/
├── scripts/
├── .github/
├── .env.example
├── .gitignore
├── README.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
├── CHANGELOG.md
└── LICENSE
```

## Quick start

### Prerequisites

- Node.js 20+
- npm 10+
- Python 3.11+ (3.12 preferred)
- Rust stable toolchain

### Desktop app

```bash
cd apps/desktop
npm install
npm run tauri dev
```

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

### Configure the agentic research flow

Copy `.env.example` to `.env` and configure:

- `TAVILY_API_KEY` for web search
- `ANVESHA_MODEL_BASE_URL` and `ANVESHA_MODEL_NAME` for an OpenAI-compatible model endpoint (the default is local Ollama)
- `ANVESHA_MODEL_API_KEY` when the selected model endpoint requires a key

Create a research project in the desktop app and choose **Agent plan** to preview AI-generated tasks and search queries, or **Run agents** to start the full workflow. Planning, search, mapping source findings to planned tasks, extraction, analysis of verified claims, supervisor-led follow-up, and report organization run as bounded stages. Quote matching and report rendering remain deterministic safeguards. The supervisor may request follow-up searches according to the selected depth (up to one, two, or three rounds for quick, standard, or deep research).

Local-document follow-up also uses a planner, project-scoped retrieval, a bounded supervisor, and an answer agent. Answers only include statements with exact quote matches in the retrieved excerpts. Both workflows show agent activity in the desktop. Creating projects, uploading documents, and persistence remain regular user-controlled data operations; they do not need an AI agent.

If the planning model is unavailable, Anvesha marks the run as using a template fallback. Missing search configuration or insufficient verified evidence produces an explicit incomplete report rather than unsupported findings.

## Development principles

- Local-first by default
- Evidence-backed research over convincing output
- Provider abstraction for LLMs and search services
- Structured agent state with durable checkpoints
- Privacy-first configuration and secure secret handling

## Open-source launch checklist

Anvesha is preparing for a public v0.1.0 milestone. Before a release, we verify:

- backend regression tests pass
- frontend production build passes
- Tauri Windows installers build successfully
- changelog and release notes are updated
- security and contribution guidance are current
- release notes clearly describe known limitations and setup requirements

See [docs/release/RELEASE_CHECKLIST.md](docs/release/RELEASE_CHECKLIST.md) for the current launch checklist and [docs/release/RELEASE_NOTES_v0.1.0.md](docs/release/RELEASE_NOTES_v0.1.0.md) for the public release notes.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, pull request expectations, and validation steps.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
