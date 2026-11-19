# Contributing to Anvesha

Thank you for contributing to Anvesha. We are building an open-source, local-first research platform with strong engineering discipline.

## Ways to contribute

- Improve documentation
- Report bugs and edge cases
- Add provider integrations
- Improve the research engine
- Add tests and evaluation cases
- Help with packaging and desktop distribution
- Review release readiness and packaging validation

## Setup

1. Fork the repository and clone your fork.
2. Install [uv](https://docs.astral.sh/uv/) and [Docker](https://docs.docker.com/get-docker/).
3. Follow the **Development** section of the [README](README.md).

## Development workflow

- Keep changes focused and small.
- Prefer evidence-driven fixes over speculative changes.
- Write tests for new behavior. API and database tests run against real PostgreSQL.
- Every model change needs an Alembic migration (`test_models_match_migrations` enforces this).
- Keep secrets out of the repository.
- Document non-obvious decisions in `docs/decisions/`.

## Local validation

Before opening a pull request, run the same checks as CI:

```bash
docker compose up -d postgres
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

## Pull requests

- Ensure the relevant tests pass.
- Update documentation if behavior changes.
- Explain the reasoning behind significant architecture decisions.
- Include the release impact for any user-facing change.
- Keep the change list small and reviewable.

## Release process

The project follows a lightweight release workflow for each milestone. Review the release checklist in [docs/release/RELEASE_CHECKLIST.md](docs/release/RELEASE_CHECKLIST.md) before tagging or announcing a public release.

## Code of conduct

Please review [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before participating.
