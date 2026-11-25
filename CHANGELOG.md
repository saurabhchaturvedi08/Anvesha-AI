# Changelog

## Unreleased

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
