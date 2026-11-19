# Anvesha

Anvesha is an open-source agentic research platform for students, researchers, and anyone who needs answers they can trust. You ask a question. Agents plan the work, search the web and scholarly sources, read full pages and papers, and write a report where every claim links to a verified quote.

Use it the way that suits you:

- **Self-host it for free** with your own API keys or local models (Ollama).
- **Use the hosted version** with your own keys, your OpenRouter account, or a subscription.
- **Use it from Claude, ChatGPT, or Gemini** by adding Anvesha as an MCP connector, so your existing AI subscription does the reasoning.

## Status

Anvesha is being rebuilt as **v2**, a web-first, multi-agent architecture. See the [v2 architecture plan](docs/architecture/V2_ARCHITECTURE_PLAN.md) for the design and roadmap.

- [x] **Phase 0: Foundation.** FastAPI backend, PostgreSQL, migrations, Docker Compose, CI.
- [ ] Phase 1: Model gateway and bring-your-own-key
- [ ] Phase 2: Research tools (web search, scholarly sources, page and PDF reading)
- [ ] Phase 3: Multi-agent research graph
- [ ] Phase 4: Web app
- [ ] Phase 5: MCP server
- [ ] Phases 6–8: Evals, hosted SaaS, desktop

The v1 code lives in [`legacy/`](legacy/) for reference while v2 reaches parity.

## Quick start (Docker)

Requires [Docker](https://docs.docker.com/get-docker/).

```bash
cp .env.example .env        # change POSTGRES_PORT / API_PORT if those ports are taken
docker compose up --build
```

Then open the API docs at <http://localhost:8000/docs>.

## Development

Requires [uv](https://docs.astral.sh/uv/) (it installs the right Python version for you) and Docker for PostgreSQL.

```bash
docker compose up -d postgres         # database only
cd backend
uv sync                               # create the virtualenv and install dependencies
uv run alembic upgrade head           # apply database migrations
uv run uvicorn anvesha.api.app:create_app --factory --reload
```

Checks (the same ones CI runs):

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                         # uses a separate anvesha_test database
```

After changing a model in `src/anvesha/db/models/`, create a migration with
`uv run alembic revision --autogenerate -m "describe the change"` and review the generated file.

## Repository layout

```text
backend/
  src/anvesha/
    api/          FastAPI app, routers, request/response schemas
    db/           SQLAlchemy models and session management
    config.py     all settings (ANVESHA_* environment variables)
    tenancy.py    users, workspaces, and request scoping
  alembic/        database migrations
  tests/
docs/             architecture, decisions, product
legacy/           v1 backend and desktop app (reference only)
compose.yaml      self-hosted stack
```

## Principles

- **Evidence over eloquence.** A claim is only reported if its quote is found in the source. The AI can reject claims but never approve its own.
- **Bring your own model.** Any provider, any key, or fully local.
- **Open by default.** Everything needed to run Anvesha yourself is in this repository.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[GNU Affero General Public License v3.0](LICENSE). You can use, modify, and self-host Anvesha freely. If you offer a modified version to others as a hosted service, you must publish your changes under the same license.
