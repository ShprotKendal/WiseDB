---
type: playbook
slug: wql-search
title: WQL search
summary: Full-text SEARCH and token budget
---
Requires `read` privilege.

```wql
SEARCH 'redis cache' LIMIT 5 BUDGET 512
```

`SEARCH` matches **whole words** (letters and digits) in `title`, `summary`, and `body`. Hyphens and punctuation split tokens (`redis-cache` matches `redis` and `cache`). ASCII and Cyrillic are case-insensitive.

The response `hits` array is the ranked list of matching **nodes** (metadata only; `body` is empty in hits). `LIMIT` caps how many nodes appear in `hits`. `BUDGET` limits total tokens in `packet` and `neighborhood`; use `BUDGET 0` when you only need the node list without bodies.

```wql
SEARCH 'redis' LIMIT 20 BUDGET 0
```

Use slugs from `hits`, then `SELECT * FROM pages WHERE slug = '...'` or [[wql-read]].
