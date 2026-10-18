---
type: concept
slug: system-wiki
title: The _wiki system database
summary: Built-in docs, WISEDB_SYSTEM_WIKI, read-only access
---
## Purpose

`_wiki` holds operator and agent documentation (this tree). Sources live in the WiseDB package; the server copies them into `{data_dir}/databases/_wiki` on startup.

## Enable and disable

- `WISEDB_SYSTEM_WIKI=1` (default): register `_wiki`, wipe its data directory, re-seed all pages, expose read-only access.
- `WISEDB_SYSTEM_WIKI=0`: remove `_wiki` from the catalog; routes return 404; on-disk files may remain.

## Access rules

Any valid session may **read** `_wiki` without a grant. **No** session may write: WQL mutations, REST writes, `DROP DATABASE`, or grants on `_wiki` are rejected.

## Discovery

- `GET /databases` lists `_wiki` for all authenticated users when enabled.
- Start at [[index]]: `SELECT * FROM pages WHERE slug = 'index'` on database `_wiki`.

## Short schema hint

`SCHEMA` on any database still returns the compact WikiLLM cheat sheet from the engine. Full guides are here in `_wiki`.
