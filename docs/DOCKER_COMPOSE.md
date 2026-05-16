# WiseDB в docker-compose другого проекта

**Публичный образ:** [`winnergd/wise`](https://hub.docker.com/r/winnergd/wise) на Docker Hub.

```bash
docker pull winnergd/wise:v2.1
```

Полная инструкция для агента: **`docs/AGENT_WISEDB.md`**.

## 1. Минимальный compose

```yaml
services:
  wise:
    image: winnergd/wise:v2.1
    environment:
      WISEDB_API_KEY: ${WISEDB_API_KEY:-}
      WISEDB_SQLITE_PATH: /app/data/wisedb.sqlite
    volumes:
      - wisedb_data:/app/data
    expose:
      - "8000"
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')",
        ]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 15s

  app:
    depends_on:
      wise:
        condition: service_healthy
    environment:
      WISEDB_URL: http://wise:8000
      WISEDB_API_KEY: ${WISEDB_API_KEY:-}

volumes:
  wisedb_data:
```

```bash
export WISEDB_API_KEY="$(openssl rand -hex 16)"
docker compose up -d
```

## 2. Через include

```yaml
include:
  - path: ../WiseDB/compose/wisedb.yaml
```

По умолчанию используется `winnergd/wise:v2.1`.
Переопределить тег: `WISEDB_IMAGE=winnergd/wise:v2.1` в `.env`.

## 3. Локальная сборка (для сопровождающих)

```bash
./scripts/docker-build.sh
export WISEDB_IMAGE=winnergd/wise
./scripts/docker-push.sh
```

Скрипт сборки создаёт локальные теги `wise:2.1.0`, `wise:v2.1`, `wise:latest`.

## 4. Обновление версии

```yaml
wise:
  image: winnergd/wise:v2.1
```
