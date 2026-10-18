# Compatibility and storage safety

The current Python API is the v3 filesystem-backed implementation, not the v4 native transactional design in `ARCHITECTURE.md`. Run one writable process per wiki root; another process will fail to open the same root while its owner lock is held. Close `Wiki` explicitly (or use it as a context manager) when finished. `refresh()` rescans through the same handle and keeps the prior in-memory catalog if the scan fails.

Page and metadata inputs must be valid UTF-8 without embedded NUL; frontmatter metadata is single-line and bounded by the legacy parser. Slugs must match between the API argument and frontmatter. Relative paths cannot traverse, use symlinks, or enter reserved `_wisedb` storage. A single file publication uses a temp file and atomic rename/link, but a directory-sync failure can leave an uncertain published file; media payload plus sidecar and human folder/page moves are not transactions. Do not interpret an error as proof that no file changed.

The scanner is qualified only for POSIX local filesystems. Windows persistent reopen, network filesystems, and hostile concurrent modification of parent directories are unsupported. Do not migrate legacy data without a separate raw-file inventory and verification.

For C/C++ integrations that need a diagnostic when opening a root, use `wisedb_open_ex`; `wisedb_open` is the compatibility wrapper. `wisedb_refresh` rebuilds a staged index and only swaps it in after a successful scan. Use the Python `Wiki` context manager or call `close()` to release the process owner lock promptly.

# WiseDB — agent guide (v3 engine, database server HTTP)

WiseDB is a **graph-structured document store** (nodes, `links_to` edges, full-text search). The Python `Wiki` class is one database directory; the HTTP server manages many databases via **WQL**.

Core operations map to WQL: **INSERT/SELECT/UPDATE/DELETE**, **SEARCH**, graph **LINKS** / **NEIGHBORS**, **edges** table. The HTTP server also exposes **REST** for nodes and folder layout (`GET /databases/{name}/tree`, `/nodes`, …); WQL remains the path for scripting, search, and graph walks.

## Connect

- Python library: `from wisedb import Wiki` then `wiki.query("...")`
- C++: `#include "wisedb.hpp"`
- HTTP: login `POST /auth/login`, then WQL `POST /databases/{name}/execute` or REST nodes/tree under `/databases/{name}/`
- MCP: `python -m wisedb.mcp_server` (single local path; not the multi-DB server)

Before writing, run `SCHEMA` in WQL, call `wisedb_schema` (MCP), or `schema()` in Python.

> **Invariant:** no `/v1` or `X-API-Key`. HTTP is session + catalog + WQL + REST nodes/layout (+ media payload download). See `.cursor/rules/wisedb-database-server.mdc`.

## Page format (WikiLLM)

```markdown
---
type: concept
slug: my-topic
title: My Topic
summary: One-line catalog entry
---
Body with [[other-slug]] links.
```

Types: `entity`, `concept`, `source`, `comparison`, `synthesis`, `playbook`, `media`.

Media files (jpg, png, mp3, mp4) live in the wiki directory; captions and links go in a sidecar `file.jpg.md`.

## Typical agent loop

1. `remember` — store a fact or page
2. `search` — find relevant context before answering (`budget_tokens` limits packet size)
3. `read` — open a specific slug
4. `edit` — replace the full page after new information

Check `unknown_slugs` and `backlinks` in `remember` responses and fill in missing linked pages.

## Human editing

Use field-level updates instead of full-document `edit` when a person or UI edits content:

- `list` — catalog without bodies
- `markdown` — raw file with frontmatter
- `update` — change `type`, `title`, `summary`, and/or `body`
- `delete` — remove page and disk files

HTTP: field updates via `UPDATE pages SET ... WHERE slug = '...'` or `PATCH /databases/{name}/nodes/{slug}`; raw file via `SELECT markdown FROM pages WHERE slug = '...'` or `GET .../nodes/{slug}/content`.

## Optics

Each node has a built-in **`content`** optic (`markdown`). Admins register **custom optics** with an immutable **kind**: `markdown`, `json`, `image`, `video`, `link`, `file`, or `span`. Several optics can coexist on one slug (for example markdown body plus JSON metadata plus an image layer).

- Text kinds: `GET/PUT/DELETE .../nodes/{slug}/optics/{optic}`.
- Binary kinds: `GET/PUT .../nodes/{slug}/optics/{optic}/bytes`.
- **`span`**: bind with `path` + line range under `PATCH /databases/{name}/settings` `code_root`; edits write back to the checkout file when `base_sha256` matches.

REST registry: `GET/POST .../optics`. WQL: `CREATE OPTIC name [KIND]`, `SELECT/UPDATE/DELETE` on `optics`.

Optic privileges (`view`, `edit`, `delete`) inherit from database `read`/`write` grants and can be overridden per user and optic via `PUT /users/{id}/grants/{database}/optics/{optic}`.
