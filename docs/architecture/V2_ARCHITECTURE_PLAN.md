# Anvesha v2: Architecture and Restructure Plan

Status: **Approved. Phases 0–5 complete; Phase 6 (quality) next.**
Date: 2026-11-16

---

## 1. What Anvesha should become

An open-source agentic research platform for students, researchers, and anyone else. A user asks a question. Agents plan the work, search the web and scholarly sources, read full pages and PDFs, and produce a report where every claim cites a verified quote.

There are three ways to use it, all running the same core engine:

| Mode | Who pays for the model | How |
|---|---|---|
| **Self-hosted (OSS)** | User (their own API keys, or free local models via Ollama) | `docker compose up` |
| **Hosted SaaS, BYOK** | User (their keys or their OpenRouter account) | Sign up, paste a key or click "Connect OpenRouter" |
| **Hosted SaaS, subscription** | Us (credits metered per run) | Free tier plus paid plans |
| **Inside Claude / ChatGPT / Gemini** | User's existing AI subscription | Anvesha runs as an **MCP server**; their assistant calls our research tools |

### About "connect their Claude / ChatGPT / Gemini account"

A third-party server cannot legitimately use someone's ChatGPT Plus or Claude Pro subscription as a model API. Proxying or scraping a consumer session breaks the providers' terms, and the user's account gets banned. There are two legitimate ways to get the same result:

1. **MCP server mode.** We publish Anvesha as a remote MCP server. The user adds it as a connector in claude.ai, ChatGPT (developer mode / custom connectors), Gemini CLI, Cursor, and similar clients. Their subscription pays for the reasoning; Anvesha supplies the tools (search, fetch, verify, evidence store, report). This is the "use your own Claude/ChatGPT" story. Note: ChatGPT's plan coverage for custom connectors is still changing, and sources disagree on which tiers get full access, so we check OpenAI's docs at build time.
2. **OpenRouter OAuth (PKCE).** One click and the user connects their OpenRouter account. We receive a key scoped to their account, and they get access to Claude, GPT, Gemini, Llama and others with their own billing. This is the smoothest BYOK experience.

Plus plain BYOK: paste an OpenAI, Anthropic, Google, Groq, or other key, or point at local Ollama.

---

## 2. Honest assessment of the current repo

**Worth keeping (this is the moat):**
- The evidence-first philosophy. A claim is supported only if its quote **deterministically matches** the source text, and the LLM verifier can reject but never approve. Keep this.
- The data model concepts: Project → Execution → Source → Claim → Evidence.
- Bounded loops, budgets, explicit "incomplete" reports instead of hallucinated ones.
- The test suite's intent (`backend/tests/test_research_engine.py`).

**Problems:**

| # | Problem | Where |
|---|---|---|
| 1 | **It isn't actually agentic.** It's a fixed 15-node pipeline where each node sends one prompt and hand-parses JSON. No LLM ever chooses a tool, decides what to read, or decides when it's done. | `services/research_graph.py` |
| 2 | **3-second model timeout.** Most real cloud or local LLM calls exceed this, so runs silently fall back to templates. Probably the most visible bug today. | `services/model_client.py` (`timeout=3.0`) |
| 3 | **Runs block the HTTP request.** `async def` routes call blocking sync code for minutes. The "job" is fictional and there is no worker. | `routers/research.py` `execute_project_with_budget` |
| 4 | **Verification only sees search snippets** (≤4,000 chars). Pages and PDFs are never fetched, so evidence is shallow. | `collect_sources` / `execute_research_tasks` |
| 5 | **One global model from env vars.** No per-user keys, no per-role models. Provider "validation" always passes. Key column is plaintext. | `services/providers.py`, `models.py` |
| 6 | **Hand-rolled JSON parsing** instead of native tool calling / structured outputs. Causes fragile fallbacks everywhere. | all agent nodes |
| 7 | **Custom checkpoint callback + `START` routing hack** instead of LangGraph's built-in checkpointer. | `_checkpoint`, `_start_node` |
| 8 | Manual `ALTER TABLE` migrations despite Alembic being a dependency; SQLite path hardcoded relative to the working directory. | `database.py` |
| 9 | 963-line `App.tsx`, hardcoded `http://127.0.0.1:8001`, no streaming. | `apps/desktop/src/App.tsx` |
| 10 | No auth, no users, no tenancy, so SaaS is impossible as-is. README lists `packages/` that don't exist. | — |

**Verdict:** keep the ideas and the verification logic. Rewrite the orchestration, the model layer, the job execution, and the frontend. Restructuring the existing code would take longer than rebuilding these layers properly.

---

## 3. Recommended tech stack

| Layer | Choice | Why |
|---|---|---|
| Agent orchestration | **LangGraph** (keep) | Durable checkpoints, human-in-the-loop `interrupt()`, parallel fan-out (`Send`), time-travel debugging. The team already knows it. Use it *properly*: native checkpointer, tool-calling agents inside nodes. |
| Model gateway | **Own thin gateway on the official `openai` and `anthropic` SDKs**, prices from Pydantic's **genai-prices** | One OpenAI-compatible adapter covers OpenAI, Gemini, OpenRouter, Groq, Mistral, Ollama and custom servers; a native adapter covers Anthropic. LiteLLM was the first choice, but its release pipeline was compromised twice in 2026 (credential-stealing releases 1.82.7/1.82.8 in March and 1.107.0 in October), which is unacceptable for a service that holds users' API keys. |
| Structured output | **Pydantic v2 schemas + native tool calling** | Replaces all hand-parsed JSON. |
| API | **FastAPI** (keep), fully async | Already in use; OpenAPI drives the generated TS client. |
| Background jobs | **Procrastinate** (Postgres-backed async job queue) + separate worker process | Runs graphs outside the request. No extra infrastructure, since the queue lives in Postgres. (arq was the first choice, but it is in maintenance-only mode.) LangGraph checkpoints make jobs resumable after crashes. |
| Live progress | **SSE** via Postgres LISTEN/NOTIFY, events persisted in `run_events` | Stream agent steps, tool calls, and partial reports to the UI; reconnecting clients replay from the table. |
| Database | **PostgreSQL 16 + pgvector** | One DB for app data, LangGraph checkpoints (`PostgresSaver`), vectors, and full-text search. Works the same self-hosted and in the cloud. |
| Migrations | **Alembic** | Already a dependency; actually use it. |
| File storage | **Local volume** now; **S3-compatible** (Cloudflare R2) for SaaS | Original PDFs. Extracted text lives in Postgres. The storage interface is small, so an S3 backend drops in for the hosted service. MinIO isn't needed for self-hosting. |
| Document parsing | **pypdfium2** (PDFium, Chrome's PDF engine) now; **Docling** later as an optional quality path | PyMuPDF 2.x now bundles onnxruntime and a layout model (~100 MB), too heavy for the default image. pypdfium2 is fast, permissively licensed, and has no dependencies. |
| Web content extraction | `httpx` + **trafilatura**; optional Jina Reader / Firecrawl | Full-text pages for real quote verification. |
| Search (pluggable) | **SearXNG** (free, self-hostable, default for OSS), Tavily, Brave, Exa | Students can run with zero search cost. |
| Scholarly sources | **OpenAlex, Semantic Scholar, arXiv, Crossref, PubMed** | Free APIs. This is the big differentiator for researchers. |
| Frontend | **Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query** | Covers both the SaaS marketing site and the app. Typed client generated from OpenAPI (`openapi-typescript`). |
| Auth | **Better Auth** (open source, in Next.js); FastAPI verifies JWT via JWKS | Self-hostable, no vendor lock-in. Optional single-user "no auth" mode for local OSS installs. |
| MCP server | **Official MCP Python SDK** (Streamable HTTP + OAuth) | "Use Anvesha from Claude / ChatGPT / Gemini". |
| Observability | **Langfuse** (self-hostable) + OpenTelemetry | Traces every agent step, token, and cost. |
| Evals | Small golden-question set + citation-accuracy metrics in CI | Without this you can't safely change prompts or models. |
| Billing (SaaS only) | **Stripe** or **Paddle** (merchant of record); **Razorpay** for INR | Credits ledger fed by the gateway's `usage_events`. |
| Python tooling | **uv**, ruff, mypy/pyright, pytest | Fast and reproducible. |
| JS tooling | **pnpm** workspaces | |
| Desktop (later) | **Tauri** shell wrapping the web app | Keep the existing Tauri work, but as a thin wrapper, not a separate UI. |

**Considered and rejected:**
- *CrewAI / AutoGen:* less control over durability and budgets.
- *OpenAI Agents SDK:* OpenAI-first; we need provider neutrality.
- *Pydantic AI alone:* good typed agents, but no built-in graph checkpointing. We get most of its value from Pydantic schemas inside LangGraph.
- *Temporal:* excellent, but heavy for self-hosters. Procrastinate plus LangGraph checkpoints is enough for now; revisit at scale.
- *Keeping SQLite:* supporting both SQLite and Postgres doubles the test surface and blocks pgvector. One `docker compose` gives local users Postgres for free.

---

## 4. System architecture

```text
                        ┌────────────────────────────────────────────┐
  Browser / Tauri  ───► │ apps/web  (Next.js, Better Auth, shadcn)   │
                        └───────────────┬────────────────────────────┘
                                        │ REST + SSE (JWT)
  Claude / ChatGPT /                    ▼
  Gemini CLI / Cursor ─► ┌────────────────────────────────────────────┐
     (MCP over HTTP)     │ api  (FastAPI)                             │
                         │  auth · projects · runs · keys · billing   │
                         │  /mcp  (MCP server, same core)             │
                         └──────┬───────────────┬─────────────────────┘
                                │ enqueue       │ subscribe (SSE)
                                ▼               ▼
                         ┌────────────┐   ┌──────────┐
                         │  Postgres  │◄──┤  events  │
                         └─────┬──────┘   └──────────┘
                               ▼
                         ┌────────────────────────────────────────────┐
                         │ worker (procr.) runs LangGraph research    │
                         │   ├─ agents/ (lead, researcher, verifier,  │
                         │   │          writer, assistant)            │
                         │   ├─ tools/  (search, fetch, scholar,      │
                         │   │          docs, evidence)               │
                         │   └─ llm/    (model gateway, key vault,    │
                         │              per-role routing, budgets)    │
                         └──────┬──────────────┬──────────────────────┘
                                ▼              ▼
                     ┌─────────────────┐  ┌───────────────┐   ┌──────────┐
                     │ Postgres        │  │ S3 / MinIO    │   │ Langfuse │
                     │ +pgvector +FTS  │  │ PDFs, pages   │   │ traces   │
                     │ +LG checkpoints │  └───────────────┘   └──────────┘
                     └─────────────────┘
```

The same `anvesha` Python package powers the API, the worker, the MCP server, and a future CLI.

---

## 5. Agent architecture (the core)

### 5.1 Pattern: orchestrator-workers, inside a durable graph

"Pure agentic" should mean agents decide *what* to do: which queries, which pages to read, when evidence is sufficient. It shouldn't mean unbounded autonomy. LangGraph is the durable harness (checkpoints, budgets, human approval). Inside it, real tool-using agents run their own loops.

```text
 START
   │
   ▼
 [Clarifier]  (optional) asks the user 1–3 scoping questions ── interrupt() ──► user
   │
   ▼
 [Lead Researcher]  writes a research plan: sub-questions, source strategy, depth
   │
   ├── interrupt(): user reviews / edits / approves the plan  (skippable via "autopilot")
   ▼
 [Fan-out via Send]  one Researcher agent per sub-question, in parallel
   │    each Researcher = tool-calling loop (max N steps, token cap):
   │      web_search · scholar_search · fetch_page · read_pdf
   │      query_project_docs · record_finding(statement, quote, source_id)
   ▼
 [Verifier]
   │   1. deterministic: quote must exist in the FETCHED full text (kept from v1)
   │   2. LLM judge: does the quote actually entail the statement? (can only reject)
   │   3. cross-source agreement / contradiction flags
   ▼
 [Lead Researcher: reflect]  coverage per sub-question; gaps? contradictions?
   │        ├── gaps & budget left & round < max ──► new sub-questions ──► Fan-out
   │        └── done
   ▼
 [Writer]  report built ONLY from verified evidence; inline citations [n]
   │       → executive summary, findings, contradictions, open questions
   ▼
 END  (report + evidence graph persisted; run is resumable at any checkpoint)
```

After a run, the **Research Assistant** agent handles chat over the project. Its tools are `query_evidence`, `query_project_docs`, `web_search` and `fetch_page`, and it applies the same verification rules to every answer. This replaces the separate `document_followup.py` graph.

### 5.2 Mapping v1 to v2

| v1 nodes (15) | v2 |
|---|---|
| plan | Lead Researcher (plan) |
| search, search_agent, research | Researcher agents (tool loop, parallel) |
| extract, extract_entities | Researcher's `record_finding` tool (structured output) |
| review, verify | Verifier (deterministic + LLM judge) |
| analysis, reflect (supervisor) | Lead Researcher (reflect) |
| report_plan, report, briefing | Writer |
| insufficient_report, no_sources_report | Writer with `status=incomplete` |

The result is fewer LLM calls per run, lower cost, and better quality, because each agent sees full context and decides for itself instead of filling one rigid slot.

### 5.3 Model routing per role

Users (or plan defaults) pick a model per role:
- **Lead / Writer:** strongest model (Claude Opus/Sonnet, GPT, Gemini Pro).
- **Researchers / page triage:** fast, cheap model (Haiku, Gemini Flash, GPT mini, Llama on Groq).
- **Verifier judge:** cheap model is fine, since the deterministic check does the heavy lifting.
- **Embeddings:** provider or local.

### 5.4 Guardrails

- **Budgets:** max cost (USD/credits), max tokens, max tool calls per agent, max rounds by depth. When a budget runs out, the run stops gracefully and returns a partial report.
- **Prompt injection:** all fetched content is wrapped as untrusted data, tools have least privilege, and no tool can exfiltrate keys or reach internal URLs.
- **SSRF protection** on `fetch_page`: block private IP ranges and metadata endpoints, cap size and time, check content type.
- **Determinism where it matters:** quote matching and citation rendering stay in code, never in the LLM.

---

## 6. Model access and key management

```text
resolve_model(user, role) →
   1. user's BYOK key for that provider       (encrypted, envelope encryption)
   2. user's OpenRouter OAuth key             (PKCE flow)
   3. local Ollama endpoint                   (self-hosted)
   4. platform key + credits                  (SaaS subscription only)
```

- Keys are encrypted at rest (AES-GCM with a per-deployment master key from env or KMS). They are never logged, never returned to the browser after saving, and are validated with a real cheap test call (unlike the current fake validation).
- Every LLM call goes through `llm/gateway.py`. It records tokens and cost to `usage_events`, which drives per-run budgets and SaaS billing.
- Supply-chain guard: `uv` ignores package releases younger than 7 days (`exclude-newer`), and the lockfile pins hashes.

---

## 7. Data model (Postgres)

```text
users ─┬─ workspace_members ── workspaces          (personal workspace auto-created)
       │
workspaces ─┬─ provider_credentials   (provider, encrypted_key, oauth, status)
            ├─ model_preferences      (role → provider/model)
            ├─ projects ─┬─ runs ─┬─ run_events      (SSE replay / activity log)
            │            │        ├─ sub_questions
            │            │        └─ (LangGraph checkpoint tables)
            │            ├─ sources        (url/doi, title, type, fetched_at, s3_key, text_hash)
            │            ├─ source_chunks  (text, tsvector, embedding vector)
            │            ├─ findings       (statement, quote, source_id, sub_question_id,
            │            │                  verification_status, judge_note)
            │            ├─ reports        (markdown, version, status)
            │            ├─ documents      (user uploads → also become sources)
            │            └─ chat_threads ── chat_messages
            ├─ usage_events       (run_id, model, tokens_in/out, cost)
            └─ subscriptions / credit_ledger   (SaaS only)
```

Every row carries `workspace_id`, enforced in the repository layer (Postgres RLS optional later).

---

## 8. Repository restructure

```text
anvesha/
├── apps/
│   ├── web/                    Next.js app (new primary UI)
│   └── desktop/                Tauri shell wrapping web (later; reuse current setup)
├── backend/
│   ├── pyproject.toml          uv-managed
│   ├── alembic/                real migrations
│   ├── src/anvesha/
│   │   ├── config.py           pydantic-settings, all env in one place
│   │   ├── api/                FastAPI app, routers, deps, auth (JWT verify)
│   │   ├── worker/             Procrastinate worker, job definitions
│   │   ├── graph/              research graph, state, checkpointer wiring
│   │   ├── agents/             lead.py, researcher.py, verifier.py, writer.py,
│   │   │                       assistant.py, prompts/
│   │   ├── tools/              web_search.py, fetch.py, scholar.py, documents.py,
│   │   │                       evidence.py  (one registry; reused by agents AND MCP)
│   │   ├── search/             providers: searxng, tavily, brave, exa
│   │   ├── scholar/            openalex, semantic_scholar, arxiv, crossref, pubmed
│   │   ├── llm/                gateway, adapters, catalog, credentials, budgets, pricing
│   │   ├── ingest/             docling/pymupdf parsing, chunking, embeddings
│   │   ├── verify/             deterministic quote matching (ported from v1)
│   │   ├── db/                 SQLAlchemy models, session, repositories
│   │   ├── events/             Postgres LISTEN/NOTIFY, SSE helpers
│   │   ├── mcp/                MCP server exposing tools/ + run_research
│   │   └── billing/            interfaces only (SaaS implementation plugs in)
│   └── tests/                  unit, integration (testcontainers), graph tests w/ fake LLM
├── packages/
│   └── api-client/             generated TS client from OpenAPI
├── evals/                      golden questions, scoring scripts, CI job
├── compose.yaml                self-hosted stack: `docker compose up`
├── infra/
│   └── deploy/                 SaaS deploy configs (Fly/Render/VPS), later Helm
├── docs/
└── .github/workflows/          lint, test, evals (cheap model), build images
```

---

## 9. Open source vs SaaS (open core)

- **Everything research-related is open source** (Apache-2.0, kept): agents, tools, UI, MCP, self-hosting, BYOK, Ollama.
- **SaaS-only extras** live in a separate private module or repo that plugs into the `billing/` interfaces: Stripe/Razorpay, credits, hosted platform keys, admin, abuse protection.
- **License decision: AGPL-3.0** (decided 2026-11-16). Apache-2.0 would let anyone run a closed, competing hosted copy. AGPL-3.0 keeps the project fully free for students, researchers, and self-hosters, and requires anyone who offers a modified version as a hosted service to publish their changes. Cal.com and Plausible use the same approach. Relicensing is easy now, while there is one author; it becomes hard once outside contributors join. We'll add a CLA before accepting outside PRs so dual licensing stays possible.

### SaaS deployment (start cheap, scale later)
- **Stage 1 (≈$30–80/mo):** Next.js on Vercel; API and worker on Fly.io / Railway / one Hetzner VPS running Docker; Neon or Supabase Postgres (pgvector); Cloudflare R2; Langfuse Cloud free tier.
- **Stage 2:** autoscaling workers, per-workspace rate limits for platform keys, Kubernetes/Helm only once it's actually needed.

---

## 10. Phased implementation roadmap

Each phase ends with something runnable and tests passing.

| Phase | Goal | Key deliverables | Est. |
|---|---|---|---|
| **0. Foundation** ✅ | New skeleton, no feature loss in concept | uv + `src/anvesha` layout, config, Postgres + Alembic, root `compose.yaml` (postgres, migrate, api), local single-user tenancy, projects API, CI. MinIO and SearXNG join in Phase 2, when something uses them. | 1 wk |
| **1. LLM gateway + BYOK** ✅ | Any model, per user, per role | Gateway on official SDKs (8 providers), AES-GCM key vault with auto-generated master key, free key validation via model lists, per-role model preferences, deployment-wide fallback keys, usage tracking, budgets, `/llm/test` endpoint | 1 wk |
| **2. Tools layer** ✅ | Agents can see the real world | SearXNG bundled in compose plus Tavily/Brave/Exa (workspace or server keys); OpenAlex, Semantic Scholar, arXiv, Crossref, PubMed; SSRF-proof fetcher (address check on every connection, redirects, size/time/type limits, robots.txt for agents); trafilatura and PDFium extraction; uploads; chunks with exact offsets; embeddings via the gateway (new `embedding` role); hybrid FTS + pgvector search with RRF; deterministic quote verification (typography, ligatures, PDF hyphenation, ellipsis fragments); tool registry with untrusted-content wrapping | 1.5 wk |
| **3. Agent graph v2** ✅ | Real agentic research | Lead (plan, review) / Researchers (tool loops, `Send` fan-out, quotes checked on record) / Verifier (deterministic re-check + judge that can only reject) / Writer (citations rendered in code); `interrupt()` plan approval with edits; `AsyncPostgresSaver` with a strict serializer; Procrastinate worker with retries for temporary failures, heartbeats and stalled-run recovery; per-run budgets with a writer reserve and partial reports; cancel and retry-from-checkpoint; `run_events` + LISTEN/NOTIFY + SSE with replay; `python -m anvesha.migrate` for all schemas; scripted-model graph tests. The optional Clarifier and the post-run Research Assistant chat shipped with Phase 4. | 2 wk |
| **4. Web app** ✅ | Easy to use | Next.js 16 app in `apps/web` (Tailwind, Radix, TanStack Query) with a typed client generated from OpenAPI (`packages/api-client`, drift-checked in CI); same-origin `/api/v1` proxy that streams uploads and SSE; new-research wizard with the optional Clarifier (`POST /clarify`) and your own links/files; live activity feed with reconnect and replay; plan review with edits; reports with hoverable citations showing the verified quotes, Markdown download and print; sources explorer with quote highlighting; evidence explorer with verifier notes; Research Assistant chat (`chat_threads`/`chat_messages`, read-only source tools plus a `cite` tool that checks quotes in code, background replies with progress over the event channel, per-reply budget); settings for keys, per-role models, search providers and usage; `web` service in compose. Markdown from models never renders raw HTML or loads images. | 2 wk |
| **5. MCP server** ✅ | Use from Claude / ChatGPT / Gemini | `/mcp` on the API (official MCP SDK v2, low-level server, stateless Streamable HTTP so any replica serves any request); tools `start_research`, `get_research_status`, `approve_plan`, `cancel_research`, `get_report` (optionally with the quotes behind citations), `search_evidence`, `list_projects`, `create_project`, plus every agent tool with a `project_id` (outside agents' fetches respect robots.txt), with read-only/destructive/open-world hints; revocable personal access tokens (hashed; REST, CLI and a settings page); Anvesha as its own OAuth 2.1 authorization server for connectors: dynamic client registration (secrets encrypted with the master key), PKCE, a consent page whose request is sealed into its URL and which asks for an operator-set approval secret in single-user mode (throttled, strict CSP), single-use hashed codes, hour-long access tokens with rotating refresh tokens, revocation, RFC 8414/9728 metadata and the 401 pointer, hourly pruning; a public app (`create_public_app`, compose profile `public`) that serves only MCP, sign-in and health, so the unauthenticated REST API is never exposed; connected-apps list with disconnect. Verified with the official MCP client in tests and over real HTTP; a live check against claude.ai and ChatGPT connectors is still to do. | 1 wk |
| **6. Quality** | Measurably good | Langfuse tracing, eval set (citation precision, coverage, cost), CI eval gate | 1 wk |
| **7. SaaS** | Paid, hosted version | Better Auth, workspaces, OpenRouter OAuth, credits + Stripe/Razorpay, rate limits, deploy pipeline | 2 wk |
| **8. Polish** | Reach | Tauri wrapper, exports (PDF/DOCX/BibTeX), Zotero import, templates (lit review, market scan, fact check) | ongoing |

Phases 0 → 3 are the critical path. The web app (4) can start in parallel once the API contract from phase 3 is stable.

---

## 11. Decisions (approved 2026-11-16)

1. **Web-first.** Next.js is the primary UI; Tauri becomes a thin wrapper later.
2. **Postgres-only.** SQLite is dropped; local users get Postgres via `docker compose`.
3. **License: AGPL-3.0** (see section 9).
4. **Billing:** Stripe first for global payments, with Razorpay added for INR. Not needed until Phase 7.
5. **Rewrite strategy:** build v2 on the `v2` branch. v1's backend is kept under `legacy/backend-v1/` as reference for porting (mainly quote verification) and is deleted when Phase 3 reaches parity.
