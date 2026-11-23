# Anvesha

Anvesha is an open-source agentic research platform for students, researchers, and anyone who needs answers they can trust. You ask a question. Agents plan the work, search the web and scholarly sources, read full pages and papers, and write a report where every claim links to a verified quote.

Use it the way that suits you:

- **Self-host it for free** with your own API keys or local models (Ollama).
- **Use the hosted version** with your own keys, your OpenRouter account, or a subscription.
- **Use it from Claude, ChatGPT, or Gemini** by adding Anvesha as an MCP connector, so your existing AI subscription does the reasoning.

## Status

Anvesha is being rebuilt as **v2**, a web-first, multi-agent architecture. See the [v2 architecture plan](docs/architecture/V2_ARCHITECTURE_PLAN.md) for the design and roadmap.

- [x] **Phase 0: Foundation.** FastAPI backend, PostgreSQL, migrations, Docker Compose, CI.
- [x] **Phase 1: Model gateway.** Bring your own key for OpenAI, Anthropic, Gemini, OpenRouter, Groq, Mistral, Ollama, or any OpenAI-compatible server; encrypted key storage; per-role models; usage and cost tracking.
- [x] **Phase 2: Research tools.** Web search (bundled SearXNG, free; or Tavily, Brave, Exa), scholarly search (OpenAlex, Semantic Scholar, arXiv, Crossref, PubMed), safe page and PDF reading, uploads, hybrid keyword and semantic search, deterministic quote verification.
- [x] **Phase 3: Research agents.** A lead researcher plans (you approve or edit the plan), researcher agents investigate in parallel, a verifier checks every quote in code and with a model, and a writer produces a cited report. Runs execute in a background worker, survive restarts, stream live progress, and stop at a spending limit with a partial report.
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

### Connect a model

From the API docs page (a web app comes in Phase 4):

1. **Add a key:** `PUT /api/v1/providers/{provider}/credential` with `{"api_key": "..."}`. The key is checked with the provider, then stored encrypted. For local models, install [Ollama](https://ollama.com) and use provider `ollama` with no key. (In Docker, first set `ANVESHA_OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` in `.env`.)
2. **See available models:** `GET /api/v1/providers/{provider}/models`.
3. **Pick a model for each agent role** (`lead`, `researcher`, `verifier`, `writer`): `PUT /api/v1/model-preferences/{role}` with `{"provider": "...", "model": "..."}`. A strong model suits `lead` and `writer`; a fast, cheap one suits `researcher`.
4. **Check it works:** `POST /api/v1/llm/test`. Spending shows up at `GET /api/v1/usage`.

If you self-host for yourself, you can instead put keys in `.env` (for example `ANVESHA_OPENAI_API_KEY`); see `.env.example`.

For semantic search over your sources, also pick an **embedding model**: `PUT /api/v1/model-preferences/embedding` (for example OpenAI `text-embedding-3-small`, Gemini `gemini-embedding-001`, or Ollama `nomic-embed-text`). Without one, source search uses keywords only. Anthropic has no embeddings API.

### Run research

With models chosen for all four agent roles:

1. **Start:** `POST /api/v1/projects/{id}/runs` with `{}` (researches the project's objective), or with options such as `{"autopilot": true, "depth": "deep", "max_cost_usd": "1.00"}`. Depth `quick`, `standard` or `deep` sets how many sub-questions, research rounds and steps the agents get.
2. **Follow along:** `GET /api/v1/runs/{id}/events/stream` streams the agents' activity as Server-Sent Events (searches, pages read, findings, verification), replaying earlier events first. `GET /api/v1/runs/{id}` shows status, the plan, sub-questions and cost so far.
3. **Approve the plan:** unless `autopilot` is set, the run pauses with status `awaiting_approval` once the lead has a plan. Continue with `POST /api/v1/runs/{id}/approve` (`{}` as proposed, or `{"sub_questions": [{"question": "..."}]}` to change it).
4. **Read the report:** `GET /api/v1/runs/{id}/report` (Markdown with numbered citations and a source list), and the evidence behind it at `GET /api/v1/runs/{id}/findings`.

`POST /api/v1/runs/{id}/cancel` stops a run within seconds. `POST /api/v1/runs/{id}/retry` continues a failed run from its last completed step (for example after fixing a key). Each run has a spending limit (`ANVESHA_RUN_DEFAULT_MAX_COST_USD`, default $2); when it is reached, research stops and the report is written from what was found.

### Search and gather sources

Docker Compose runs a private [SearXNG](https://docs.searxng.org/) instance, so web search works with no API key. Scholarly search uses free public APIs.

- **Web search:** `POST /api/v1/search/web` with `{"query": "..."}`. To use a paid search API instead, add a key with `PUT /api/v1/search/providers/{tavily|brave|exa}/credential`.
- **Papers:** `POST /api/v1/search/scholarly` with `{"query": "...", "sources": ["openalex", "arxiv", "pubmed"]}`. Setting `ANVESHA_SCHOLAR_CONTACT_EMAIL` gets faster access from OpenAlex, Crossref and PubMed. Semantic Scholar rate-limits heavily without an API key.
- **Save a page or PDF to a project:** `POST /api/v1/projects/{id}/sources` with `{"url": "..."}`, or upload a file to `POST /api/v1/projects/{id}/sources/upload` (PDF, text, Markdown, HTML).
- **Search saved sources:** `POST /api/v1/projects/{id}/sources/search`.
- **Check a quote:** `POST /api/v1/projects/{id}/verify-quote` with `{"source_id": "...", "quote": "..."}`. It passes only if the quote appears in the source word for word, allowing for case, typographic quotes, whitespace and PDF line breaks.
- **Try the agents' tools directly:** `GET /api/v1/tools`, then `POST /api/v1/projects/{id}/tools/{name}`.

Fetching is locked down: only public http(s) addresses (no localhost, private networks or cloud metadata, checked on every connection and redirect), with size, time and file-type limits. When agents fetch pages, they respect robots.txt.

## Development

Requires [uv](https://docs.astral.sh/uv/) (it installs the right Python version for you) and Docker for PostgreSQL.

```bash
docker compose up -d postgres searxng # database and web search only
cd backend
uv sync                               # create the virtualenv and install dependencies
uv run python -m anvesha.migrate      # database tables, job queue, agent checkpoints
ANVESHA_SEARXNG_URL=http://localhost:8888 uv run uvicorn anvesha.api.app:create_app --factory --reload
ANVESHA_SEARXNG_URL=http://localhost:8888 uv run python -m anvesha.worker   # in a second terminal
```

On Windows, if connections to `localhost` hang, use `127.0.0.1` in `ANVESHA_DATABASE_URL` (Docker listens on IPv4 only).

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
    llm/          model gateway: provider catalog, adapters, credentials, budgets, pricing
    search/       web search providers (SearXNG, Tavily, Brave, Exa)
    scholar/      scholarly APIs (OpenAlex, Semantic Scholar, arXiv, Crossref, PubMed)
    web/          safe fetching: SSRF-proof transport, robots.txt, limits
    ingest/       text extraction (HTML, PDF) and chunking
    sources/      saving sources, embeddings, hybrid search
    verify/       deterministic quote verification
    tools/        the tools agents call (shared with the future MCP server)
    agents/       lead, researcher, verifier and writer agents, prompts, citations
    graph/        the LangGraph research graph, checkpoints, and the run executor
    worker/       background worker and job queue (Procrastinate)
    events/       run activity events: storage, LISTEN/NOTIFY, Server-Sent Events
    runs.py       starting, approving, cancelling and retrying runs
    migrate.py    one command for all schemas (`python -m anvesha.migrate`)
    config.py     all settings (ANVESHA_* environment variables)
    crypto.py     encryption for stored API keys
    tenancy.py    users, workspaces, and request scoping
  alembic/        database migrations
  tests/
docs/             architecture, decisions, product
infra/            configuration for bundled services (SearXNG)
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
