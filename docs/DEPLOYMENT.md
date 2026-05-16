# WiseDB — ОЗУ + опционально SQLite

WiseDB держит **HNSW + лексику + граф** в памяти для низкой задержки. При необходимости пишет в **локальный SQLite** (режим WAL), чтобы после перезапуска поднять тот же граф — без отдельного СУБД-процесса.

## Быстрый старт

```bash
pip install -e ".[server,mcp]"
export WISEDB_SQLITE_PATH=./data/wisedb.sqlite
wisedb-server
```

Если `WISEDB_SQLITE_PATH` не задан, сервер работает без файловой долговечности.

## Переменные окружения

| Переменная | Назначение |
|------------|------------|
| `WISEDB_SQLITE_PATH` | Если задан — путь к WAL SQLite; чанки/рёбра синхронизируются после мутаций (асинхронный поток). Пусто → **только RAM** (типично для dev/тестов). |
| `WISEDB_API_KEY` | Опциональный админ-ключ |
| `WISEDB_EMBEDDING_MODEL` | Опционально id модели sentence-transformers (`pip install -e ".[server,embed]"`) |
| `WISEDB_LIBRARY_PATH` | Гибрид: корень файловой библиотеки узлов; SQLite тогда хранит в основном **рёбра** (см. `docs/AGENT_WISEDB.md`). |

## Docker

- **`docker-compose.yml` в корне репозитория** собирает локальный `Dockerfile` и поднимает только **WiseDB** (API на 8000, данные графа в SQLite внутри тома `/app/data`). Отдельный Postgres не нужен — WiseDB к нему не подключается.
- В другом проекте используйте готовый образ **`winnergd/wise:v2.1`** — см. **`docs/AGENT_WISEDB.md`**, **`docs/DOCKER_COMPOSE.md`**.

Подробная шпаргалка для агента: **`docs/AGENT_WISEDB.md`**.

## MCP / LLM

Тот же HTTP API или MCP к запущенному процессу. Режим долговечности API не меняет.

## Резервные копии

Копируйте файл SQLite в простое время или используйте API резервного копирования SQLite. `pg_dump` не применим.
