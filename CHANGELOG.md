# Changelog

## Unreleased

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
