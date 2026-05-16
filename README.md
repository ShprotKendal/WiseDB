# WiseDB

WiseDB — контекстная база для быстрого гибридного поиска: HNSW, лексика и граф в оперативной памяти,
с опциональной долговечностью через локальный SQLite.

## Возможности

- Низкая задержка: индексы в ОЗУ, опционально WAL SQLite на диск.
- HTTP API (FastAPI) и MCP для сценариев с ИИ-агентами.
- Гибрид: HNSW, лексика, граф, MMR, пространственный / «gaze»-учёт направлений.
- Docker и Docker Compose для локальной разработки и выката.

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[server,mcp,dev]"

export WISEDB_SQLITE_PATH=./data/wisedb.sqlite
wisedb-server
```

По умолчанию API: `http://127.0.0.1:8000`.

## Docker

### Из этого репозитория

Если вы находитесь в клоне **WiseDB**, корневой `docker-compose.yml` собирает локальный `Dockerfile`.
Так вы запускаете текущий код из рабочей копии, а не скачанный образ:

```bash
export WISEDB_API_KEY="$(openssl rand -hex 16)"
docker compose up --build -d
```

API будет доступен на `http://127.0.0.1:8000`.

### Образ с Docker Hub (для другого проекта)

Публичный образ: **[winnergd/wise](https://hub.docker.com/r/winnergd/wise)**. Его нужно указывать в `docker-compose.yml` **вашего приложения**, когда WiseDB подключается как готовый сервис.

Минимальный фрагмент для **другого** проекта:

```yaml
services:
  wise:
    image: winnergd/wise:v2.1
    environment:
      WISEDB_API_KEY: ${WISEDB_API_KEY:-}
      WISEDB_SQLITE_PATH: /app/data/wisedb.sqlite
    volumes:
      - wisedb_data:/app/data
    ports:
      - "8000:8000"
    restart: unless-stopped

volumes:
  wisedb_data:
```

Перед запуском задайте админ-ключ (для создания логических баз):  
`export WISEDB_API_KEY="$(openssl rand -hex 16)"`, затем `docker compose up -d`.

`WISEDB_SQLITE_PATH` включает хранение данных в SQLite внутри тома `wisedb_data`. Если переменную убрать, сервис будет работать только в памяти.

Подключение приложения к API: базовый URL `http://wise:8000` из другого сервиса в той же сети compose; для запросов к данным нужны заголовки `X-WiseDB-Database` и `X-WiseDB-Password` (см. `docs/AGENT_WISEDB.md`).

Больше вариантов (healthcheck, `include`, push своего тега): **`docs/DOCKER_COMPOSE.md`**.

## Документация

- `docs/DEPLOYMENT.md` — установка, переменные окружения, Docker, MCP, резервные копии.
- `docs/AGENT_WISEDB.md` — подробная шпаргалка для операторов и агентов.
- `docs/AI_AGENT_OPERATIONS.md` — краткий чеклист для агентов.

## Разработка

```bash
pip install -e ".[viz,server,mcp,dev]"
pytest
```

## Лицензия

MIT, см. файл `LICENSE`.
