---
type: playbook
slug: wql-write
title: WQL write queries
summary: INSERT, UPDATE, DELETE on pages
---
Requires `write` privilege on the database (not on read-only `_wiki`).

## Insert

```wql
INSERT INTO pages (slug, markdown) VALUES ('topic', '---\ntype: concept\nslug: topic\n---\nBody.')
```

Optional `rel_path` for folder placement; `file_base64` for binary media (with optional caption markdown).

## Update fields

```wql
UPDATE pages SET title = 'New title' WHERE slug = 'topic'
```

Set `type`, `title`, `summary`, `body`, or replace full document with `markdown = '...'`.

## Delete

```wql
DELETE FROM pages WHERE slug = 'topic'
```

## Page format

See [[pages]]. REST equivalents: [[http]].
