FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Nested build isolation (Cython/setuptools) inherits these; the default 15s
# read timeout fails on a stalled download from files.pythonhosted.org.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10
RUN set -eux; \
    for attempt in 1 2 3; do \
      pip install --no-cache-dir ".[server]" && break; \
      if [ "$attempt" -eq 3 ]; then exit 1; fi; \
      echo "pip install failed (attempt ${attempt}), retrying..."; \
      sleep 5; \
    done

FROM python:3.12-slim

WORKDIR /app
ARG VERSION=3.0.0
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

ENV WISEDB_DATA_DIR=/app/data
ENV WISEDB_HOST=0.0.0.0
ENV WISEDB_PORT=8000
ENV WISEDB_IMAGE_VERSION=${VERSION}

RUN groupadd --system wisedb && useradd --system --gid wisedb --home-dir /app --no-create-home wisedb \
    && mkdir -p /app/data \
    && chown -R wisedb:wisedb /app/data

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
VOLUME ["/app/data"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["wisedb-server"]
