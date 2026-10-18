# WiseDB

WiseDB is **long-term memory for LLM agents**: remember, read, edit, and search over a file-backed wiki graph.

Storage follows [WikiLLM](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): markdown pages, media files, and `[[slug]]` links. The hot path is a **C core** with optional **Python** and **C++** bindings. HTTP and MCP are optional extras.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Use as a library (no server)

### Python

```python
from wisedb import Wiki

wiki = Wiki("./data/my-agent")

wiki.remember("redis", markdown="""---
type: concept
slug: redis
title: Redis
summary: In-memory cache
---
Used by [[my-app]].
""")

wiki.read("redis", neighbors=True)
wiki.search("cache", budget_tokens=1024)

# Human-oriented CRUD
wiki.list()
wiki.markdown("redis")
wiki.update("redis", title="Redis 7", body="Updated text.")
wiki.delete("redis")
```

### C++

```cpp
#include "wisedb.hpp"

wisedb::Wiki wiki("./data/my-agent");
wiki.remember("redis", page_markdown);
wiki.list();
wiki.update("redis", nullptr, "Redis 7", nullptr, "Updated text.");
wiki.remove("redis");
```

Build with CMake:

```bash
cmake -B build && cmake --build build
./build/wisedb_cpp_hello ./data/cpp-example
```

### C

```c
#include "wisedb.h"

wisedb_engine *eng = wisedb_open("./data/my-agent");
char *out = NULL;
wisedb_remember(eng, "redis", markdown, NULL, 0, NULL, &out);
wisedb_free_string(out);
wisedb_close(eng);
```

## Optional HTTP API (database server)

```bash
export WISEDB_DATA_DIR=./data
export WISEDB_ROOT_PASSWORD='replace-with-a-long-random-value'
pip install -e ".[server]"
wisedb-server
```

The server exposes **named databases**, **sessions**, and **WQL**. Set `WISEDB_ROOT_PASSWORD` before binding off loopback. The storage engine is still the v3 filesystem-backed implementation; see `docs/ARCHITECTURE.md` for the v4 target.

With `WISEDB_SYSTEM_WIKI=1` (default), the server registers a read-only **`_wiki`** database of built-in usage docs (re-seeded on each startup; WQL/REST writes are rejected). Set `WISEDB_SYSTEM_WIKI=0` to disable it.

| Who | Method | Path | Description |
|-----|--------|------|-------------|
| anyone | POST | `/auth/login` | Session from username/password (`root` uses `WISEDB_ROOT_PASSWORD`) |
| client | POST | `/databases/{name}/execute` | Run WQL (`{"query": "..."}`, bearer session) |
| client | GET | `/databases/{name}/tree` | Nested folders and page catalog (read) |
| client | GET/POST/PATCH/PUT/DELETE | `/databases/{name}/nodes`, `/nodes/{slug}`, `/nodes/{slug}/content` | Node CRUD and raw markdown (read/write) |
| client | GET/POST/PATCH/DELETE | `/databases/{name}/optics`, `/nodes/{slug}/optics/{optic}` | Custom optical layers (UTF-8 text per slug; `content` stays on `/content`) |
| client | POST/DELETE | `/databases/{name}/folders`, `/folders/move`, `/nodes/{slug}/move` | Folder layout (write) |
| client | WS | `/ws` | JSON ops mirroring REST; `WS[]` streams row results |
| client | GET | `/databases/{name}/nodes/{slug}/payload` | Download binary media for a node (read grant) |
| root | POST/GET/DELETE | `/databases`, `/users`, grants | Catalog administration |

### WQL (WiseDB query language)

Library: `wiki.query("SELECT * FROM pages WHERE slug = 'redis'")` returns the same JSON as `POST /databases/{db}/execute` with that query.

Examples:

- `SELECT * FROM pages WHERE slug = 'redis'` — read a page
- `SELECT * FROM pages WHERE slug = 'redis' NEIGHBORS` — one hop, same as `read(..., neighbors=True)`
- `SELECT * FROM pages WHERE slug = 'redis' LINKS DEPTH 2 DIRECTION OUTGOING` — graph walk with edges and `unknown_slugs`
- `SELECT * FROM edges WHERE from_slug = 'redis' AND type = 'links_to'`
- `INSERT INTO pages (slug, markdown) VALUES ('redis', '---\\n...')`
- `UPDATE pages SET title = 'New' WHERE slug = 'redis'`
- `DELETE FROM pages WHERE slug = 'redis'`
- `SEARCH 'redis cache' LIMIT 5 BUDGET 512`
- `SCHEMA` — agent format guide (same text as library `schema()`)
- `SELECT * FROM layout` — folder tree plus page catalog
- `SELECT markdown FROM pages WHERE slug = 'redis'` — raw stored document
- `CREATE FOLDER 'Notes/2026'`, `DROP FOLDER 'Notes/2026'`, `MOVE FOLDER 'A' TO 'B'`, `MOVE PAGE 'slug' TO 'path.md'`
- `CREATE OPTIC metatags 'optional description'`, `DROP OPTIC metatags`
- `SELECT * FROM optics WHERE slug = 'redis' AND name = 'metatags'`
- `UPDATE optics SET text = '...' WHERE slug = 'redis' AND name = 'metatags'`
- `DELETE FROM optics WHERE slug = 'redis' AND name = 'metatags'`
- `INSERT INTO pages (slug, rel_path, file_base64) VALUES (...)` — binary media node (caption via optional `markdown`)
- WebSocket `WS /ws` — same capabilities as HTTP (auth, users, databases, WQL, payload). Streaming per row:

```wql
SELECT * FROM pages
WS[SELECT markdown FROM pages WHERE slug = $.slug]
```

`WS[]` and `$.field` are rejected on `POST .../execute`; use the WebSocket `execute` op instead.

## MCP

```bash
pip install -e ".[mcp]"
WISEDB_DATA_DIR=./data python -m wisedb.mcp_server
```

Tools: `wisedb_remember`, `wisedb_read`, `wisedb_edit`, `wisedb_search`, `wisedb_schema`, `wisedb_list`, `wisedb_read_markdown`, `wisedb_update`, `wisedb_delete`, `wisedb_query`.

## Architecture

- **C** — file catalog, in-memory graph, BM25 search, token budget
- **Cython** — `wisedb._engine` ↔ `wisedb.h`
- **C++** — header-only RAII wrapper (`wisedb.hpp`)
- **Python** — `Wiki` API, optional FastAPI server and MCP

## Docker

Production-style (named data volume, loopback bind):

```bash
export WISEDB_ROOT_PASSWORD='replace-with-a-long-random-value'
docker compose up --build
```

Development (auto-reload for `server/` and `wql/`, `./data` on the host):

```bash
export WISEDB_ROOT_PASSWORD='dev-only-password'   # optional; compose has a dev default
docker compose -f docker-compose.dev.yml up --build
```

Open `http://127.0.0.1:8000/docs` after startup. Rebuild the dev image after C/Cython changes.

Compose binds to loopback by default and requires a root password. Docker runtime runs as an unprivileged user; host bind mounts must be writable by that container UID.

## Development

```bash
pip install -e ".[dev]"
pytest tests/test_memory_v3.py -v
cmake -B build && cmake --build build && ctest --test-dir build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/AGENT_WISEDB.md](docs/AGENT_WISEDB.md).

## License

MIT — see [LICENSE](LICENSE).
