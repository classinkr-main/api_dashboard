# ClassIn 학원 대시보드 (classin-dashboard)
# Runtime image: install the package and run the `classin-dash` entrypoint
# (uvicorn factory classin_dashboard.web.app:create_app, root_path=/dash, port 8100).

FROM python:3.11-slim

# Faster, quieter, deterministic Python behavior in containers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy only what's needed to resolve dependencies + build the package.
COPY pyproject.toml ./
COPY src ./src

# Install the package (pulls in FastAPI/uvicorn/httpx/etc from pyproject.toml).
RUN pip install --no-cache-dir .

# Non-root user; app writes SQLite + JSONL under /app/data.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

# Persistent state (SQLite + webhook JSONL) — mount a volume here.
VOLUME ["/app/data"]

EXPOSE 8100

# DASH_DATA_DIR should point at /app/data (see .env.example / docker-compose.yml).
CMD ["classin-dash"]
