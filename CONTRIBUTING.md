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

1. Fork the repository.
2. Clone your fork locally.
3. Install Node.js 20+, Python 3.11+, and Rust stable.
4. Follow the setup instructions in the project README.
5. Create a local virtual environment for the backend and install dependencies.

## Development workflow

- Keep changes focused and small.
- Prefer evidence-driven fixes over speculative changes.
- Write tests for new behavior.
- Keep secrets out of the repository.
- Document non-obvious decisions.
- Update release notes when the public behavior or release status changes.

## Local validation

Before opening a pull request, run the relevant checks:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
```

```bash
cd apps/desktop
npm install
npm run build
```

For Windows packaging validation:

```bash
cd apps/desktop
npm run tauri:build:windows
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
