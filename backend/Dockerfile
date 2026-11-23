FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
# Volume mount points must exist and be owned by the app user before Docker mounts them.
RUN useradd --create-home --uid 1000 anvesha \
    && mkdir -p /var/lib/anvesha /var/lib/anvesha-files \
    && chown anvesha /var/lib/anvesha /var/lib/anvesha-files
USER anvesha

EXPOSE 8000
CMD ["uvicorn", "anvesha.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
