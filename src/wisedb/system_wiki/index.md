---
type: concept
slug: index
title: WiseDB system wiki
summary: Map of built-in operator and agent documentation
---
The `_wiki` database is a **read-only** copy of WiseDB usage docs shipped with the server. It is re-seeded on every server start when `WISEDB_SYSTEM_WIKI` is enabled.

## Start here

- [[connect]] — library, HTTP sessions, MCP
- [[pages]] — WikiLLM page format and links
- [[auth]] — users, grants, root password

## WQL

- [[wql-read]] — `SELECT`, `SCHEMA`, layout catalog
- [[wql-write]] — `INSERT`, `UPDATE`, `DELETE`
- [[wql-search]] — full-text `SEARCH`
- [[wql-graph]] — `LINKS`, `NEIGHBORS`, `edges`
- [[wql-layout]] — folders and `MOVE PAGE`
- [[wql-stream]] — batch, parse, WebSocket `WS[]`

## HTTP

- [[http]] — REST tree, nodes, content, media payload

## This database

- [[system-wiki]] — flag, read-only rules, re-seed behavior

For a one-line format reminder on any database, run `SCHEMA` in WQL.
