# WiseDB — инструкция для агента

WiseDB — **HTTP REST API** (FastAPI) для семантической памяти: чанки, граф связей, гибридный retrieve (dense + lexical + graph). Это **не** PostgreSQL и **не** SQL — только REST (или MCP поверх REST).

**Официальный образ Docker Hub:** `winnergd/wise`  
**Текущий образ:** `winnergd/wise:v2.1` (также `2.1.0`, `latest`)

---

## 1. Быстрый старт в docker-compose проекта

Добавь сервис в `docker-compose.yml` (или `include` файла `compose/wisedb.yaml` из репозитория WiseDB):

```yaml
services:
  wise:
    image: winnergd/wise:v2.1
    environment:
      WISEDB_API_KEY: ${WISEDB_API_KEY:-}
      WISEDB_DATA_DIR: /app/data
      WISEDB_SLEEP_REINFORCE_ON_RETRIEVE: ${WISEDB_SLEEP_REINFORCE_ON_RETRIEVE:-false}
      WISEDB_SLEEP_LLM_API_KEY: ${WISEDB_SLEEP_LLM_API_KEY:-}
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
    restart: unless-stopped

  app:
    depends_on:
      wise:
        condition: service_healthy
    environment:
      WISEDB_URL: http://wise:8000
      WISEDB_DATABASE: myapp
      WISEDB_PASSWORD: ${WISEDB_DB_PASSWORD:-}

volumes:
  wisedb_data:
```

Перед `docker compose up`:

```bash
export WISEDB_API_KEY="$(openssl rand -hex 16)"
export WISEDB_DB_PASSWORD="strong-secret"
docker compose pull wise
docker compose up -d
```

Создать базу для приложения:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/databases \
  -H "X-API-Key: $WISEDB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"id":"myapp","password":"'"$WISEDB_DB_PASSWORD"'","name":"My App"}'
```

### URL-адреса

| Откуда | Base URL |
|--------|----------|
| Другой контейнер в том же compose | `http://wise:8000` |
| Хост (если проброшен порт `8000:8000`) | `http://127.0.0.1:8000` |

Имя сервиса в compose должно быть **`wise`** (или поменяй `WISEDB_URL` под своё имя).

### Проверка

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/readyz
```

Из контейнера `app`: `curl -s http://wise:8000/healthz`

---

## 2. Несколько баз на одном сервере

Один процесс WiseDB хостит **много изолированных баз**. У каждой свой **id** (slug) и **пароль**. Данные лежат в `{WISEDB_DATA_DIR}/databases/{id}/`.

| Уровень | Что это |
|---------|---------|
| **database** (`myapp`) | Отдельная SQLite + HNSW; свой пароль |
| **tenant_id** (`default`) | Namespace внутри базы (как раньше) |

### 2.1 Admin (создание баз)

Требует **`WISEDB_API_KEY`** на сервере — только для `/v1/databases/*`:

| Действие | Метод | Путь |
|----------|-------|------|
| Создать базу | POST | `/v1/databases` |
| Список баз | GET | `/v1/databases` |
| Удалить базу | DELETE | `/v1/databases/{id}` |
| Сменить пароль | POST | `/v1/databases/{id}/password` |

```bash
curl -s -X POST http://wise:8000/v1/databases \
  -H "X-API-Key: $WISEDB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"id":"myapp","password":"secret123","name":"My App"}'
```

`id`: `^[a-z][a-z0-9_-]{0,63}$` (например `myapp`, `project_alpha`).

### 2.2 Доступ к данным (chunks, retrieve, …)

К **каждому** запросу `/v1/chunks/*`, `/v1/edges/*`, `/v1/retrieve`:

```http
X-WiseDB-Database: myapp
X-WiseDB-Password: secret123
```

Альтернатива: `Authorization: Bearer myapp:secret123`

При первом старте без баз в каталоге создаётся **`default`** с пустым паролем (dev). В проде всегда создавай базы через admin API.

`/healthz`, `/readyz`, `/metrics` — без авторизации.

---

## 3. API — что вызывать

Base: `{WISEDB_URL}/v1` (например `http://wise:8000/v1`).

| Действие | Метод | Путь |
|----------|-------|------|
| Записать/обновить чанки | POST | `/v1/chunks/upsert` |
| Записать рёбра графа | POST | `/v1/edges/upsert` |
| Поиск (retrieve) | POST | `/v1/retrieve` |
| Чанк по UUID | GET | `/v1/chunks/{uuid}` |
| Чанк по внешнему ключу | GET | `/v1/chunks/lookup?external_key=...&tenant_id=default` |
| Список чанков | GET | `/v1/chunks?tenant_id=default` |
| Удалить чанк | DELETE | `/v1/chunks/{uuid}` |
| **Сон / обслуживание графа** | POST | `/v1/sleep` |
| Вложение | GET | `/v1/chunks/{uuid}/attachments/{slot}` |

### 3.1 Upsert чанков

```bash
curl -s -X POST "http://wise:8000/v1/chunks/upsert" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{
    "tenant_id": "default",
    "chunks": [{
      "external_key": "doc:readme",
      "title": "README",
      "body": "WiseDB stores chunks and a knowledge graph.",
      "tags": ["docs"],
      "embedding": [0.1, 0.2, ...]
    }]
  }'
```

- `tenant_id` — изоляция данных (по умолчанию `"default"`).
- `external_key` — идемпотентный ключ для upsert (удобно для синка из приложения).
- `embedding` — массив из **384** float (`EMBEDDING_DIM`). Без embedding чанк слабо участвует в dense-поиске, но работает lexical.
- `body_format`: `"plain"` | `"wisedoc"`.

Ответ: список созданных/обновлённых чанков с `id` (UUID).

### 3.2 Upsert рёбер

```bash
curl -s -X POST "http://wise:8000/v1/edges/upsert" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{
    "tenant_id": "default",
    "edges": [{
      "source_chunk_id": "<uuid-1>",
      "target_chunk_id": "<uuid-2>",
      "weight": 0.9,
      "relation_type": "references"
    }]
  }'
```

### 3.3 Retrieve (главный endpoint для RAG)

```bash
curl -s -X POST "http://wise:8000/v1/retrieve" \
  -H "Content-Type: application/json" \
  -H "X-WiseDB-Database: myapp" \
  -H "X-WiseDB-Password: $WISEDB_DB_PASSWORD" \
  -d '{
    "tenant_id": "default",
    "query": "как подключить wise в compose",
    "query_embedding": [0.1, 0.2, ...],
    "top_k_dense": 20,
    "top_k_lexical": 20,
    "max_hops": 5,
    "return_markdown": true
  }'
```

Ответ: `chunks[]` (с `score`, `hop_distance`) и опционально `markdown` — готовый контекст для промпта.

**Важно про embeddings:**

- `query_embedding` — 384 float в теле запроса, **или**
- на сервере `WISEDB_EMBEDDING_MODEL` (образ с `embed`; публичный `winnergd/wise` обычно **без** модели).

Без `query_embedding` и без модели на сервере → **400**. Для lexical-only можно искать по тексту, но dense-канал не сработает без векторов в чанках.

### 3.4 Gaze / углы на рёбрах (опционально)

Чанк может задавать «взгляд» в `metadata.wisedb_gaze`:

- `bearing_rad` (float) — направление «вперёд» в абстрактной окружности (0 = вперёд, π = сзади);
- `toward` (UUID соседа) — без `bearing_rad` ось «вперёд» строится по PCA эмбеддингов в сторону этого соседа;
- `locked` — для `toward`-only не кэшировать авто-углы `gaze_pca` на рёбрах при upsert (явные углы на рёбрах и `relation_type` не трогаем).

Ребро может кэшировать углы в `metadata.wisedb_bearing`: `from_source_rad`, `from_target_rad`, `from_source_kind` / `from_target_kind` (`explicit`, `relation_type`, `gaze_pca`). При upsert чанка/ребра сервер дополняет кэш, если угол можно вывести.

**Приоритет при расчёте направления по ребру A→B:** явный `wisedb_bearing` на ребре → эвристика `relation_type` (`supports`→0, `part_of`→π/4, `related`→π/2, `contradicts`→π) → `wisedb_gaze` на A + локальный PCA по эмбеддингам A, B и соседей. Если угол нельзя определить, ребро **«плавает»**: множитель при graph propagation равен 1 (как раньше).

Дополнительные поля **`POST /v1/retrieve`**:

| Поле | Значение по умолчанию | Смысл |
|------|------------------------|--------|
| `gaze_aware` | `true` | Умножать вклад рёбер на cos(bearing) и политику «сзади» |
| `suppress_behind` | `true` | Для \|bearing\| > 150° дополнительный штраф, если не `include_opposition` |
| `include_opposition` | `false` | Усилить проход по «противоположным» связям (π) |
| `gaze_anchor_chunk_id` | `null` | Якорь для секторов в markdown (`ahead` / `aside` / `behind`); иначе топовый seed RRF |

В ответе в `metadata` чанка могут появиться `wisedb_bearing_from_anchor_rad` и `wisedb_gaze_sector` относительно якоря (если есть прямое ребро и известен bearing).

### 3.5 Sleep (обслуживание памяти)

`POST /v1/sleep` — фоновая по смыслу операция (синхронный HTTP-ответ по окончании прогона в процессе):

| Поле | Смысл |
|------|--------|
| `phase` | `decay_prune` — умножить веса рёбер на `decay_factor`, мягко удалить рёбра ниже `min_weight`; `geometry_refresh` — пересчитать кэш `wisedb_bearing` для всех рёбер тенанта; `llm_optimize` — попросить OpenAI-compatible LLM консервативно улучшить или распилить чанки |
| `dry_run` | Только счётчики в `stats`, без записи в чанки/рёбра |
| `delete_orphan_chunks` | После prune удалить (soft) чанки без живых рёбер (кроме `wisedb_pinned` / `pinned`) |
| `skip_reinforced_within_hours` | Не трогать рёбра с недавним `wisedb_last_reinforced_at` из retrieve (см. env ниже) |
| `llm_api_key` | Ключ для `llm_optimize` на один запрос; альтернатива env `WISEDB_SLEEP_LLM_API_KEY` |
| `llm_base_url` | OpenAI-compatible base URL; по умолчанию из env или `https://api.openai.com/v1` |
| `llm_model` | Модель для `llm_optimize`; по умолчанию из env |
| `llm_max_chunks` / `llm_max_actions` | Лимиты на объём сна: сколько чанков показать модели и сколько действий принять |

Журнал прогонов: таблица `sleep_runs` в SQLite этой базы (`run_id`, `stats`, `status`).

Переменная **`WISEDB_SLEEP_REINFORCE_ON_RETRIEVE`**: если `true`, каждый `retrieve` помечает внутренние рёбра между попавшими в ответ узлами меткой `wisedb_last_reinforced_at`, чтобы ночной decay реже резал «полезные» связи.

`llm_optimize` ожидает JSON-действия от модели:

- `update` — переписать существующий чанк более ясно, не меняя факты.
- `split` — создать несколько новых атомарных чанков и связать их с исходным ребром `sleep_split`.

Исходный чанк при split не удаляется автоматически: это безопаснее для памяти и ссылок. Если нужен предварительный просмотр действий модели, запускай `dry_run: true`.

---

## 4. Типичный workflow агента

1. **Поднять** `wise`, volume `wisedb_data`, `WISEDB_API_KEY` (admin).
2. **Создать базу:** `POST /v1/databases` → `id` + `password`.
3. **Записать знания:** `POST /v1/chunks/upsert` с заголовками `X-WiseDB-Database` / `X-WiseDB-Password`.
4. **Связать:** `POST /v1/edges/upsert` между связанными чанками.
5. **Читать контекст:** `POST /v1/retrieve` с `query` + `query_embedding`.
6. **Идемпотентность:** повторный upsert с тем же `external_key` обновляет чанк, не дублирует.

Не используй WiseDB как основную БД приложения (users, orders) — только **контекст / память / RAG**.

---

## 5. Переменные окружения (контейнер)

| Переменная | Назначение | По умолчанию в образе |
|------------|------------|------------------------|
| `WISEDB_API_KEY` | Admin-ключ (`/v1/databases/*`) | пусто |
| `WISEDB_DATA_DIR` | Корень: `catalog.sqlite` + `databases/` | родитель `WISEDB_SQLITE_PATH` или `/app/data` |
| `WISEDB_BOOTSTRAP_DEFAULT_DB` | Создать `default` если каталог пуст | `true` |
| `WISEDB_DEFAULT_DB_PASSWORD` | Пароль для bootstrapped `default` | пусто |
| `WISEDB_LOG_LEVEL` | Логи | `INFO` |
| `WISEDB_HNSW_MAX_ELEMENTS` | Лимит HNSW | `2000000` |
| `WISEDB_LIBRARY_PATH` | Гибрид: файлы на диске, граф в SQLite | не задан |
| `WISEDB_EMBEDDING_MODEL` | sentence-transformers на сервере | не задан |
| `WISEDB_SLEEP_REINFORCE_ON_RETRIEVE` | Помечать рёбра при retrieve (под decay «сна») | `false` |
| `WISEDB_SLEEP_LLM_API_KEY` | OpenAI-compatible ключ для фазы `llm_optimize` | не задан |
| `WISEDB_SLEEP_LLM_BASE_URL` | Base URL LLM API для `llm_optimize` | `https://api.openai.com/v1` |
| `WISEDB_SLEEP_LLM_MODEL` | Модель для `llm_optimize` | `gpt-4o-mini` |

Том **`wisedb_data` → `/app/data`** обязателен, иначе данные пропадут при пересоздании контейнера.

---

## 6. MCP (Cursor / Claude Desktop)

MCP-сервер — отдельный процесс на машине разработчика, ходит в HTTP API:

```json
{
  "mcpServers": {
    "wisedb": {
      "command": "python",
      "args": ["-m", "wisedb.mcp_server"],
      "env": {
        "WISEDB_API_BASE": "http://127.0.0.1:8000",
        "WISEDB_DATABASE": "myapp",
        "WISEDB_PASSWORD": "<пароль базы>"
      }
    }
  }
}
```

Нужен пакет: `pip install wisedb[mcp]` (или клон репозитория WiseDB).

Инструмент **`get_context_bundle`**: параметры `query`, опционально `query_embedding` (384), `tenant_id`.

---

## 7. Чего не делать

| Нельзя | Почему |
|--------|--------|
| `psql` / `postgresql://` к WiseDB | WiseDB не Postgres |
| SQL-запросы к WiseDB | Только REST |
| Путать WiseDB и Postgres приложения | WiseDB — свой SQLite в томе; Postgres, если нужен, поднимаете вы отдельно |
| Забыть volume | SQLite на томе, иначе потеря данных |
| Путать `WISEDB_API_KEY` и пароль базы | API key — только admin; данные — `X-WiseDB-Password` |
| Retrieve без embedding и без модели на сервере | 400 или слабый dense |

---

## 8. Локальная разработка WiseDB (опционально)

Из клона репозитория WiseDB, не нужно для обычного потребителя образа:

```bash
pip install -e ".[server,mcp]"
export WISEDB_SQLITE_PATH=./data/wisedb.sqlite
wisedb-server
```

---

## 9. Ссылки

| Ресурс | Значение |
|--------|----------|
| Docker Hub | `docker pull winnergd/wise:v2.1` |
| Compose-фрагмент | `compose/wisedb.yaml` (`WISEDB_IMAGE` переопределяет образ) |
| Подробнее про compose | `docs/DOCKER_COMPOSE.md` |
| Инструкция для внешних AI-агентов | `docs/AI_AGENT_OPERATIONS.md` |
| Переменные / бэкапы | `docs/DEPLOYMENT.md` |
