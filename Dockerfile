# syntax=docker/dockerfile:1

# Build stage: resolve the locked dependencies into a self-contained virtualenv
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Runtime stage: no build tools, unprivileged user
FROM python:3.13-slim
RUN useradd --system --uid 10001 --no-create-home tavern
COPY --from=build /app/.venv /app/.venv
# The examples work as a default config; mount your own over /config
COPY examples /config
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TAVERN_WRAPPER_CONFIG=/config/services.yaml
USER tavern
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
ENTRYPOINT ["tavern-wrapper"]
