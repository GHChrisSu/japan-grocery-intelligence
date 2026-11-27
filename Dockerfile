FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-jpn && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY configs ./configs
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
RUN uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["grocery", "check-sources"]
