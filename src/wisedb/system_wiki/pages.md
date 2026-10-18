---
type: concept
slug: pages
title: WikiLLM pages
summary: Frontmatter, types, wiki links, and media
---
Each document is markdown with YAML frontmatter:

```markdown
---
type: concept
slug: my-topic
title: My Topic
summary: One-line catalog line
---
Body with [[other-slug]] wiki links.
```

## Types

`entity`, `concept`, `source`, `comparison`, `synthesis`, `playbook`, `media`.

## Slugs

The API slug must match frontmatter `slug`. Links use `[[slug]]` in the body.

## Database settings

Per-database options live in `_wisedb/settings.json`. The first setting is **semantics**:

- When `semantics` is `false` (default), `{{Some Title}}` in a title or body is plain text.
- When `semantics` is `true`, each `{{page title}}` mention creates `links_to` edges to every node whose `title` matches exactly (case-sensitive). Wiki `[[slug]]` links always work.

Read with `GET /databases/{name}/settings`. Change with `PATCH /databases/{name}/settings` (`admin` required).

## Media

Binary files (jpg, png, mp3, mp4) live beside captions in a sidecar `file.jpg.md`. Download bytes via `GET /databases/{name}/nodes/{slug}/payload` when the node is media.

## Human-friendly edits

Prefer field updates over full-document replace when a UI edits content:

- WQL: `UPDATE pages SET title = '...' WHERE slug = '...'`
- HTTP: `PATCH /databases/{name}/nodes/{slug}`
- Raw file: `SELECT markdown FROM pages WHERE slug = '...'` or `GET .../content`

See [[wql-write]] and [[http]].
