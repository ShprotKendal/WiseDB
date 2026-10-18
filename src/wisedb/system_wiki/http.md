---
type: playbook
slug: http
title: HTTP REST for nodes and layout
summary: Tree, node CRUD, content, and media payload
---
Authenticated routes use bearer sessions from [[auth]]. Privileges: `read` for GET; `write` for mutations; `admin` to drop a database.

## Layout and catalog

- `GET /databases/{name}/tree` — nested folders and page catalog (no bodies).

## Nodes

- `GET /databases/{name}/nodes` — list nodes
- `GET /databases/{name}/search?q=word&limit=20` — keyword search (title, summary, body); returns full node objects in `nodes`
- `GET /databases/{name}/nodes/{slug}` — metadata; `?neighbors=1` for one-hop summaries
- `GET /databases/{name}/nodes/{slug}/content` — raw markdown
- `POST /databases/{name}/nodes` — create (`slug`, `markdown`, optional `rel_path`)
- `PATCH /databases/{name}/nodes/{slug}` — field updates
- `PUT /databases/{name}/nodes/{slug}` — replace full markdown
- `DELETE /databases/{name}/nodes/{slug}` — remove page and files

## Folders and moves

- `POST /databases/{name}/folders` — create folder
- `DELETE /databases/{name}/folders` — remove folder
- `POST /databases/{name}/folders/move`, `POST .../nodes/{slug}/move` — layout changes

Equivalent WQL is in [[wql-layout]]. Graph walks are WQL-only — see [[wql-graph]]. Search is also available via REST (`GET .../search`) or WQL `SEARCH` — see [[wql-search]].

## Media

`GET /databases/{name}/nodes/{slug}/payload` — binary body for media nodes (`read` grant).

## WQL primary path

`POST /databases/{name}/execute` runs one statement; see [[wql-read]] and [[wql-stream]].
