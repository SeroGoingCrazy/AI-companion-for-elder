# The web app only. Fall detection stays out of this image on purpose: it wants a camera
# or a video file and ~1GB of torch, and it already reports over HTTP, so it runs wherever
# the camera is and posts alerts here (FALL_REPORT_URL).
FROM python:3.12-slim

# uv, pinned by digest-free tag but copied from the official image so the build does not
# depend on a network installer script.
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0

WORKDIR /app

# Dependencies first: this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN uv sync --locked --no-dev

# SQLite and generated audio live here. Mount a volume at /app/data to keep them across
# deploys; without one the demo simply reseeds, which is usually what a demo wants.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/healthz').read()"

CMD ["uv", "run", "--no-sync", "elder-web"]
