FROM python:3.12-slim

ARG VERSION=2.1.0
LABEL org.opencontainers.image.title="WiseDB" \
      org.opencontainers.image.version="${VERSION}"

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    WISEDB_SQLITE_PATH=/app/data/wisedb.sqlite

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
COPY README.md LICENSE /app/
COPY src /app/src

RUN pip install --upgrade pip && pip install ".[server]"

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "wisedb.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
