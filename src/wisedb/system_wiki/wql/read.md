---
type: playbook
slug: wql-read
title: WQL read queries
summary: SELECT, SCHEMA, markdown, and layout catalog
---
Run via `wiki.query(...)` or `POST /databases/{name}/execute`.

## Page by slug

```wql
SELECT * FROM pages WHERE slug = 'my-topic'
```

Add `NEIGHBORS` for one-hop neighbor summaries (same as `read(..., neighbors=True)`).

## Raw markdown

```wql
SELECT markdown FROM pages WHERE slug = 'my-topic'
```

## Layout tree

```wql
SELECT * FROM layout
```

## Agent cheat sheet

```wql
SCHEMA
```

Returns compact WikiLLM format text. Longer guides are in `_wiki` — see [[index]].

## Edges table

```wql
SELECT * FROM edges WHERE from_slug = 'a' AND type = 'links_to'
```

Graph walks: [[wql-graph]].
