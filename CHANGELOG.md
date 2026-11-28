# Changelog

## Unreleased

- Added optional multi-user mode (`ANVESHA_AUTH_MODE=jwt`): Better Auth sign-in in the web app (email and password, optional GitHub/Google), and the API verifies the web app's short-lived EdDSA JWTs against its JWKS. Users are created on first sign-in with a personal workspace
- Added team workspaces with owner, admin and member roles, single-use invitation links, a workspace switcher, and workspace settings. Admins manage keys and models; removing a member revokes their tokens and connected apps
- Added "Connect OpenRouter" (OAuth PKCE): a key on the user's own OpenRouter account, saved like a pasted key
- Added rate limits counted in Postgres (shared by all API replicas): research runs per workspace; chat, question checks, searches and tool calls (REST and MCP) per user; `429` with `Retry-After`
- In multi-user mode, MCP connections are approved by the signed-in user on the web app's `/connect` page instead of with the approval secret
- Every REST route now requires a signed-in user in multi-user mode, including ones that didn't ask who was calling

- Added tracing with OpenTelemetry, exported to Langfuse (`ANVESHA_LANGFUSE_*`) and/or any OTLP endpoint: one trace per run execution (a run's executions share a session), spans for each agent step, model calls as generations with tokens and cost, tool calls, and chat replies. Prompts and outputs are attached only with `ANVESHA_TRACE_CONTENT=true`
- Added evals (`python -m anvesha.evals`): a golden question set (`evals/golden.toml`) scored on citation precision, cited-sentence rate, fact recall, sub-question coverage and cost, with a configurable gate, baseline comparison, and a `score` command for any finished run
- Added a CI eval gate: the harness runs on a scripted model with every push, and the live suite runs on a real model (`.github/workflows/evals.yml`) on demand, weekly, and on pull requests labelled `run-evals`

- Added OAuth sign-in for MCP connectors (claude.ai, ChatGPT): dynamic client registration, PKCE, a consent page protected by an operator-set approval secret, rotating refresh tokens, revocation, and discovery metadata
- Added a public app (`create_public_app`, compose profile `public`) that serves only MCP, sign-in and health, so the REST API never has to be exposed
- Added Settings → Use from AI assistants: create and revoke access tokens, see and disconnect connected apps
- Added a remote MCP server at `/mcp` (Streamable HTTP) with research, report and evidence tools plus the research agents' own tools, authenticated with revocable access tokens (`/api/v1/access-tokens`, `python -m anvesha.access_tokens`)
- Added the open-source launch preparation foundation for release readiness
- Documented contributor workflow and validation steps
- Added packaging validation and release-checklist guidance

## v0.1.0

- Initial local-first research platform foundation for evidence-backed research
- Desktop workspace UI for project, execution, claim, and source review
- FastAPI backend with local document ingestion, retrieval, and resumable execution
- Tauri Windows MSI and NSIS packaging validated locally
- Release checklist prepared for the first public milestone
