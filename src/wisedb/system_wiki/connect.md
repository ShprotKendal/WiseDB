---
type: playbook
slug: connect
title: Connect to WiseDB
summary: Library, HTTP server, and MCP entry points
---
WiseDB stores **graph-structured documents** (pages, `links_to` edges, full-text search). One directory on disk is one logical database.

## Python library

```python
from wisedb import Wiki

wiki = Wiki("./data/my-db")
wiki.query("SELECT * FROM pages WHERE slug = 'topic'")
wiki.search("cache", budget_tokens=1024)
```

Run `wiki.query("SCHEMA")` or `from wisedb import schema; schema()` before writing.

## HTTP database server

1. Set `WISEDB_DATA_DIR` and `WISEDB_ROOT_PASSWORD` (required off loopback).
2. `POST /auth/login` with username and password (`root` uses the env password).
3. Send `Authorization: Bearer <token>` on protected routes.
4. Data: `POST /databases/{name}/execute` with WQL, or REST under `/databases/{name}/nodes` and `/tree`.

See [[auth]] and [[http]]. Built-in docs live in database `_wiki` — see [[system-wiki]].

## MCP

`python -m wisedb.mcp_server` opens **one local wiki path**. It is not the multi-database HTTP server.

## Related

- [[wql-read]] — first queries
- [[pages]] — document format
