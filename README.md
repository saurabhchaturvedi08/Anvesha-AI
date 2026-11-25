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
- [x] **Phase 4: Web app.** A guided new-research flow (with optional question refinement), live agent activity, plan review and editing, reports with hoverable citations that show the quoted evidence, a sources and evidence explorer, a Research Assistant chat that answers from the project's evidence, and settings for keys, models, search and usage.
- [ ] **Phase 5: MCP server** (in progress). Done: a remote MCP endpoint for Claude Code, Cursor, Gemini CLI and other clients that accept a token, with research, report and evidence tools plus the agents' own search, reading and quote-checking tools. Next: OAuth sign-in for claude.ai and ChatGPT connectors.
- [ ] Phases 6–8: Evals, hosted SaaS, desktop

The v1 code lives in [`legacy/`](legacy/) for reference while v2 reaches parity.

## Quick start (Docker)

Requires [Docker](https://docs.docker.com/get-docker/).

```bash
cp .env.example .env        # change POSTGRES_PORT / API_PORT / WEB_PORT if those ports are taken
docker compose up --build
```

Then open the web app at <http://localhost:3000>. (The API's interactive docs are at <http://localhost:8000/docs>.)

Anvesha runs in single-user mode for now: anyone who can reach the app can use it. Docker Compose publishes it on localhost only; don't expose it to a network until multi-user sign-in arrives (Phase 7).

### First-time setup

Open **Settings**:

1. **Connect a model provider.** Paste an API key for OpenAI, Anthropic, Gemini, OpenRouter, Groq or Mistral (it's checked with the provider, then stored encrypted), or set up a local [Ollama](https://ollama.com) server for free models. In Docker, Ollama on your computer is at `http://host.docker.internal:11434/v1`.
2. **Choose a model for each agent.** The lead researcher plans and reviews (use your strongest model), researchers search and read in parallel (a fast, affordable model works well), the verifier checks quotes, and the writer writes the report. The test button sends a short message to check each one.
3. **Optional:** an embedding model adds semantic search over saved sources (for example OpenAI `text-embedding-3-small` or Ollama `nomic-embed-text`), and a paid search API (Tavily, Brave, Exa) can replace the bundled SearXNG.

If you self-host for yourself, you can instead put keys in `.env` (for example `ANVESHA_OPENAI_API_KEY`); see `.env.example`.

### Research a question

1. **New research:** describe what you want to find out. The lead researcher can suggest clarifying questions first (scope, period, region), and you can attach your own links and files.
2. **Choose the depth and a spending limit.** `Quick`, `Standard` or `Deep` sets how many researchers, rounds and steps the agents get. When the limit is reached, research stops and a report is written from what was found.
3. **Review the plan.** The lead proposes sub-questions; edit, remove or add them, then approve. (Or turn off plan review to run straight through.)
4. **Watch the agents work:** searches, pages read, findings recorded, quotes rejected, and verification, live.
5. **Read the report.** Hover or tap a citation to see the exact quotes behind it. Download it as Markdown or print it to PDF.
6. **Explore and ask.** The Sources tab shows every page and paper read (with the quoted passage highlighted), the Evidence tab lists each finding and why any were rejected, and Chat answers follow-up questions from the project's evidence, citing checked quotes.

Runs keep going if you close the browser. You can cancel a run, and retry a failed one from its last completed step (for example after fixing a key).

### Use the API

Everything the web app does goes through the REST API, documented at <http://localhost:8000/docs>:

- **Models:** `PUT /api/v1/providers/{provider}/credential`, `GET /api/v1/providers/{provider}/models`, `PUT /api/v1/model-preferences/{role}`, `POST /api/v1/llm/test`, `GET /api/v1/usage`.
- **Runs:** `POST /api/v1/projects/{id}/runs` (options such as `{"autopilot": true, "depth": "deep", "max_cost_usd": "1.00"}`), `GET /api/v1/runs/{id}/events/stream` (live activity as Server-Sent Events, replaying earlier events first), `POST /api/v1/runs/{id}/approve` (`{}`, or `{"sub_questions": [{"question": "..."}]}` to change the plan), `/cancel`, `/retry`, `GET /api/v1/runs/{id}/report` and `/findings`.
- **Chat:** `POST /api/v1/projects/{id}/chats`, then `POST /api/v1/chats/{id}/messages` with `{"content": "..."}`. The reply is written in the background; follow it at `GET /api/v1/chat-messages/{id}/stream`. Chat uses the `lead` role's model and a per-reply spending limit (`ANVESHA_CHAT_MAX_COST_USD`, default $0.25).
- **Refine a question:** `POST /api/v1/clarify` with `{"question": "..."}`.

### Use from Claude Code, Cursor, Gemini CLI and other MCP clients

Anvesha is also an [MCP](https://modelcontextprotocol.io) server at `http://localhost:8000/mcp` (Streamable HTTP). Your AI assistant can hand research to Anvesha's agents, read the cited reports, and search the verified evidence. It can also research by itself with Anvesha's search, reading and quote-checking tools, so every quote it relies on is checked in code.

1. **Create an access token.** It is shown once, so copy it:

   ```bash
   docker compose exec api python -m anvesha.access_tokens create --name "Claude Code"
   ```

   (Or `POST /api/v1/access-tokens` with `{"name": "..."}`. `list` and `revoke <id>` manage tokens.)

2. **Add the server to your client**, sending the token as a bearer token:

   - **Claude Code:** `claude mcp add --transport http anvesha http://localhost:8000/mcp --header "Authorization: Bearer anv_..."`
   - **Cursor** (`.cursor/mcp.json`) and other clients that take JSON config: `{"mcpServers": {"anvesha": {"url": "http://localhost:8000/mcp", "headers": {"Authorization": "Bearer anv_..."}}}}`
   - **Gemini CLI** (`settings.json`): the same, with `"httpUrl"` instead of `"url"`.

The tools:

| Tool | What it does |
|---|---|
| `start_research` | Runs Anvesha's research agents on a question (in a new or existing project), using the models configured in Anvesha and a spending limit. `autopilot: false` pauses for plan review. |
| `get_research_status` | Status, plan, sub-question coverage, findings, spending and recent activity. |
| `approve_plan`, `cancel_research` | Steer a run. |
| `get_report` | The cited report for a run, or a project's latest; `include_quotes` adds the verified quote behind each citation. |
| `search_evidence` | Searches a project's findings: statements, each backed by a checked quote. |
| `list_projects`, `create_project` | Find or start a project. |
| `web_search`, `scholar_search`, `fetch_page`, `read_source`, `search_sources`, `verify_quote`, `list_sources` | The research agents' own tools, acting on one project (`project_id`). |

With `start_research`, Anvesha's agents do the reasoning on your configured models. With the raw tools, your assistant does it, so your existing AI subscription pays for it. claude.ai and ChatGPT connectors need OAuth sign-in, which is the next step of Phase 5. Set `ANVESHA_MCP_ENABLED=false` to turn the endpoint off.

### Search and gather sources

Docker Compose runs a private [SearXNG](https://docs.searxng.org/) instance, so web search works with no API key. Scholarly search uses free public APIs. These endpoints are what the agents use, and you can call them directly:

- **Web search:** `POST /api/v1/search/web` with `{"query": "..."}`. To use a paid search API instead, add a key with `PUT /api/v1/search/providers/{tavily|brave|exa}/credential`.
- **Papers:** `POST /api/v1/search/scholarly` with `{"query": "...", "sources": ["openalex", "arxiv", "pubmed"]}`. Setting `ANVESHA_SCHOLAR_CONTACT_EMAIL` gets faster access from OpenAlex, Crossref and PubMed. Semantic Scholar rate-limits heavily without an API key.
- **Save a page or PDF to a project:** `POST /api/v1/projects/{id}/sources` with `{"url": "..."}`, or upload a file to `POST /api/v1/projects/{id}/sources/upload` (PDF, text, Markdown, HTML).
- **Search saved sources:** `POST /api/v1/projects/{id}/sources/search`.
- **Check a quote:** `POST /api/v1/projects/{id}/verify-quote` with `{"source_id": "...", "quote": "..."}`. It passes only if the quote appears in the source word for word, allowing for case, typographic quotes, whitespace and PDF line breaks.
- **Try the agents' tools directly:** `GET /api/v1/tools`, then `POST /api/v1/projects/{id}/tools/{name}`.

Fetching is locked down: only public http(s) addresses (no localhost, private networks or cloud metadata, checked on every connection and redirect), with size, time and file-type limits. When agents fetch pages, they respect robots.txt.

## Development

Requires [uv](https://docs.astral.sh/uv/) (it installs the right Python version for you), Docker for PostgreSQL, and for the web app [Node.js](https://nodejs.org) 22 or newer with [pnpm](https://pnpm.io) (`npm install -g pnpm`).

```bash
docker compose up -d postgres searxng # database and web search only
cd backend
uv sync                               # create the virtualenv and install dependencies
uv run python -m anvesha.migrate      # database tables, job queue, agent checkpoints
ANVESHA_SEARXNG_URL=http://localhost:8888 uv run uvicorn anvesha.api.app:create_app --factory --reload
ANVESHA_SEARXNG_URL=http://localhost:8888 uv run python -m anvesha.worker   # in a second terminal
```

The web app, in a third terminal from the repository root (it forwards API calls to `ANVESHA_API_URL`, default `http://127.0.0.1:8000`):

```bash
pnpm install
pnpm dev                              # http://localhost:3000
```

On Windows, if connections to `localhost` hang, use `127.0.0.1` in `ANVESHA_DATABASE_URL` (Docker listens on IPv4 only).

Checks (the same ones CI runs):

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                         # uses a separate anvesha_test database
cd ..
pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

After changing a model in `src/anvesha/db/models/`, create a migration with
`uv run alembic revision --autogenerate -m "describe the change"` and review the generated file.

After changing the API, regenerate the web app's typed client (CI checks it's up to date):

```bash
cd backend && uv run python -m anvesha.api.openapi ../packages/api-client/openapi.json
cd .. && pnpm api:generate
```

JavaScript dependencies follow the same supply-chain rule as Python ones: pnpm ignores releases younger than 7 days (`minimumReleaseAge` in `pnpm-workspace.yaml`).

## Repository layout

```text
apps/
  web/            Next.js web app (App Router, Tailwind, TanStack Query)
    src/app/api/  forwards /api/v1 to the backend (streams uploads and live events)
packages/
  api-client/     typed API client generated from the backend's OpenAPI schema
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
    agents/       lead, researcher, verifier, writer, clarifier and chat assistant; prompts; citations
    graph/        the LangGraph research graph, checkpoints, and the run executor
    worker/       background worker and job queue (Procrastinate)
    events/       run activity events: storage, LISTEN/NOTIFY, Server-Sent Events
    runs.py       starting, approving, cancelling and retrying runs
    chat.py       project chat: messages, background replies, progress
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
