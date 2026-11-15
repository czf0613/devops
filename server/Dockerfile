FROM registry:3.1.2@sha256:ddf754342cfc8acc51a56d5d0ab6af06826461864460636d8bd5c546dab2a7b8 AS distribution
FROM python:3.14-alpine AS build
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.13
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-alpine
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
RUN apk add --no-cache git \
    && addgroup -S -g 101 app && adduser -S -u 101 -G app app \
    && mkdir -p /mnt/git-repos && chown app:app /mnt/git-repos
COPY --from=build /app/.venv /app/.venv
COPY --from=distribution /bin/registry /usr/local/bin/registry
COPY alembic.ini ./
COPY migrations/ ./migrations/
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=3)"
# Gateway supplies explicit client IP headers; avoid a second proxy-header interpretation.
CMD ["uvicorn", "devops_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
