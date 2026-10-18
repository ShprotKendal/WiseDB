---
type: playbook
slug: wql-stream
title: Batch, parse, and WebSocket streaming
summary: POST batch and parse, WS mirror, WS[] rows
---
## Parse (no execution)

`POST /databases/{name}/parse` with `{"query": "..."}` returns statement kind and whether the caller's grants allow it.

## Batch (up to 32 statements)

`POST /databases/{name}/batch` with `{"queries": ["...", "..."]}` runs in order. Syntax and privileges are checked for all statements before the first runs; a later failure leaves earlier statements applied.

## WebSocket

`WS /ws` mirrors auth, users, databases, WQL execute/parse, and payload download. Streaming per row:

```wql
SELECT * FROM pages
WS[SELECT markdown FROM pages WHERE slug = $.slug]
```

Use the outer query to enumerate rows; the inner query runs per row with `$` bound to the outer row.

## HTTP execute

Single statement: `POST /databases/{name}/execute` — see [[wql-read]].
