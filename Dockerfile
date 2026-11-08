# Streamable HTTP server for hosting. Local clients should use stdio instead (see README).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-cache && useradd --system app

USER app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["mcp-store-tools", "--transport", "http", "--host", "0.0.0.0"]
