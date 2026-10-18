---
type: playbook
slug: wql-graph
title: WQL graph queries
summary: LINKS, NEIGHBORS, and edges
---
Requires `read` privilege.

## One-hop neighbors

```wql
SELECT * FROM pages WHERE slug = 'topic' NEIGHBORS
```

## Multi-hop links

```wql
SELECT * FROM pages WHERE slug = 'topic' LINKS DEPTH 2 DIRECTION OUTGOING
```

`DIRECTION` may be `OUTGOING`, `INCOMING`, or `BOTH`. Response includes edges and `unknown_slugs` for broken links.

## Edges table

```wql
SELECT * FROM edges WHERE from_slug = 'topic'
SELECT * FROM edges WHERE to_slug = 'topic' AND type = 'links_to'
```

Wiki links in markdown become `links_to` edges when pages are stored.

When database **semantics** is enabled (`GET /databases/{name}/settings`), `{{Page Title}}` mentions in titles and bodies also become `links_to` edges to nodes with that exact title. The markdown file is not rewritten.
