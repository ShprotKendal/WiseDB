# WiseDB v2.1 — инструкция эксплуатации для AI-агентов

Этот документ предназначен для AI-агента, который подключает WiseDB к чужому проекту как память/RAG-сервис. WiseDB — не SQL-БД и не замена Postgres. Это HTTP API для чанков, графа связей, retrieve и обслуживания памяти.

## 1. Подключение к проекту

Минимальный `docker-compose.yml` проекта:

```yaml
services:
  wise:
    image: winnergd/wise:v2.1
    environment:
      WISEDB_API_KEY: ${WISEDB_API_KEY}
      WISEDB_DATA_DIR: /app/data
      WISEDB_SLEEP_REINFORCE_ON_RETRIEVE: "true"
      WISEDB_SLEEP_LLM_API_KEY: ${WISEDB_SLEEP_LLM_API_KEY:-}
      WISEDB_SLEEP_LLM_BASE_URL: ${WISEDB_SLEEP_LLM_BASE_URL:-https://api.openai.com/v1}
      WISEDB_SLEEP_LLM_MODEL: ${WISEDB_SLEEP_LLM_MODEL:-gpt-4o-mini}
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
      WISEDB_DATABASE: myapp
      WISEDB_PASSWORD: ${WISEDB_DB_PASSWORD}

volumes:
  wisedb_data:
```

Перед стартом:

```bash
export WISEDB_API_KEY="$(openssl rand -hex 16)"
export WISEDB_DB_PASSWORD="$(openssl rand -hex 24)"
docker compose up -d
```

## 2. Создание базы

Создай базу один раз на проект или окружение:

```bash
curl -s -X POST "$WISEDB_URL/v1/databases" \
  -H "X-API-Key: $WISEDB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"id":"myapp","password":"'"$WISEDB_DB_PASSWORD"'","name":"My App"}'
```

Дальше все запросы к данным требуют:

```http
X-WiseDB-Database: myapp
X-WiseDB-Password: <WISEDB_DB_PASSWORD>
```

Не передавай `WISEDB_API_KEY` в обычный runtime-код приложения: он нужен только для администрирования баз.

## 3. Запись памяти

Используй `external_key` для идемпотентности. Повторный upsert с тем же ключом обновит узел, а не создаст дубль.

```bash
curl -s -X POST "$WISEDB_URL/v1/chunks/upsert" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{
    "tenant_id": "default",
    "chunks": [{
      "external_key": "repo:docs:readme",
      "title": "README",
      "body": "Project setup and architecture notes.",
      "tags": ["docs", "repo"],
      "metadata": {"source": "git", "kind": "documentation"}
    }]
  }'
```

Если приложение умеет считать embeddings 384-dim, передавай `embedding`. Если нет, можно писать без него: lexical и граф останутся рабочими, но dense-поиск будет слабее. Для серверного автокодирования нужен образ/установка с `sentence-transformers` и `WISEDB_EMBEDDING_MODEL`.

## 4. Связи графа

После записи связанных узлов добавляй рёбра:

```bash
curl -s -X POST "$WISEDB_URL/v1/edges/upsert" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{
    "tenant_id": "default",
    "edges": [{
      "source_chunk_id": "<uuid-a>",
      "target_chunk_id": "<uuid-b>",
      "weight": 0.9,
      "relation_type": "references"
    }]
  }'
```

Используй `relation_type`: `references`, `supports`, `contradicts`, `part_of`, `related`, `sleep_split`. Это помогает graph propagation и gaze-геометрии.

## 5. Чтение контекста

Основной путь для агента — `POST /v1/retrieve`:

```bash
curl -s -X POST "$WISEDB_URL/v1/retrieve" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{
    "tenant_id": "default",
    "query": "How is the project deployed?",
    "max_nodes": 32,
    "return_markdown": true
  }'
```

Используй `markdown` из ответа как контекст для промпта. Если передаёшь `query_embedding`, dense-канал будет точнее.

## 6. Сон памяти

Запускай sleep как периодическую задачу: вручную, cron, CI job или внутренний scheduler приложения.

Обычное обслуживание графа:

```bash
curl -s -X POST "$WISEDB_URL/v1/sleep" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{
    "tenant_id": "default",
    "phase": "decay_prune",
    "decay_factor": 0.98,
    "min_weight": 0.05,
    "skip_reinforced_within_hours": 168,
    "delete_orphan_chunks": false
  }'
```

Пересчёт геометрии:

```bash
curl -s -X POST "$WISEDB_URL/v1/sleep" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{"tenant_id":"default","phase":"geometry_refresh"}'
```

LLM-сон:

```bash
curl -s -X POST "$WISEDB_URL/v1/sleep" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{
    "tenant_id": "default",
    "phase": "llm_optimize",
    "dry_run": true,
    "llm_max_chunks": 24,
    "llm_max_actions": 12
  }'
```

Сначала запускай `dry_run: true`. Если `stats.llm_actions_preview` выглядит корректно, повтори с `dry_run: false`. LLM может:

- `update`: улучшить существующий узел без изменения фактов;
- `split`: создать новые атомарные узлы и связать их с исходным через `sleep_split`.

Исходный узел при split не удаляется автоматически.

## 7. Политика безопасности

- Не используй WiseDB для users, payments, sessions, orders.
- Не клади секреты в `body`, `title`, `metadata`.
- Храни `WISEDB_API_KEY`, `WISEDB_DB_PASSWORD`, `WISEDB_SLEEP_LLM_API_KEY` в secret manager или `.env`, не в репозитории.
- Для production всегда подключай volume `/app/data`.
- Для LLM-сна учитывай, что выбранные чанки уходят во внешний LLM API.

## 8. Минимальный регламент

- Upsert знаний: при изменении документов, кода, тикетов или пользовательской памяти.
- Retrieve: перед ответом агента или планированием задачи.
- `decay_prune`: ежедневно или еженедельно.
- `geometry_refresh`: после массовых изменений графа.
- `llm_optimize`: реже, например ночью или после больших импортов; всегда сначала dry-run.
- Backup: регулярно копировать volume/SQLite при остановленном сервисе или через SQLite backup API.
