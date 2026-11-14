# The gateway image. Cloudflare Containers (edge/wrangler.jsonc) and `docker compose up` build this same file
# from the repository root, so the layout under /srv mirrors the repo:
#   /srv/backend   FastAPI app (editable install, so app/ resolves the repo-relative data paths)
#   /srv/data      the architect's gitignored data/catalog + data/fixtures, copied in at build time.
#                  data/raw is excluded by .dockerignore. Nothing from data/ is ever committed (patch P7).
# No persistence: SQLite lives in /data on the container's ephemeral disk and resets on restart/deploy
# (accepted, docs/WORKFLOW.md → Decisions).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip install -e ./backend tzdata

COPY scripts scripts
COPY data/ data/

RUN useradd --create-home gateway \
    && mkdir -p /data \
    && chown -R gateway /data /srv/data

ENV DATABASE_URL=sqlite:////data/gateway.db

# T48: the git commit this image was built from, reported by /health as "build" (scripts/deploy passes it;
# without it the app falls back to data/catalog/BUILD_SHA, which scripts/deploy also writes).
ARG BUILD_SHA=unknown
ENV BUILD_SHA=${BUILD_SHA}

USER gateway
WORKDIR /srv/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
