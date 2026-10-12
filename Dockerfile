FROM python:3.12-slim

# Pinned for reproducible builds; matches the uv_build range in pyproject.toml.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./

RUN uv sync \
    --frozen \
    --no-dev \
    --no-install-project

COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

RUN uv sync \
    --frozen \
    --no-dev

# Run as an unprivileged user. It owns /app so job commands that use
# `uv run` can still update the project environment if needed.
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app

USER app

ENV PATH="/app/.venv/bin:$PATH"

CMD ["sh", "-c", "uvicorn vendor_risk_analyzer.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
