FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN python -m pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src


FROM base AS test

COPY tests ./tests
COPY scripts ./scripts

RUN uv sync --frozen --extra dev

ENTRYPOINT ["pytest"]
CMD ["-q"]


FROM base AS runtime

RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["web"]
