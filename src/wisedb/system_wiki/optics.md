---
type: concept
slug: optics
title: Optics (optical layers)
summary: Typed views per node slug with inherited and per-user grants
---
## Model

- **`content`** — built-in optic (`markdown`): WikiLLM markdown for each page (`GET .../nodes/{slug}/content`, WQL `pages`).
- **Custom optics** — one immutable **kind** per optic name. A node can hold several optics at once (for example `content` markdown plus `meta` json plus `shot` image).

### Kinds

| Kind | Role |
|------|------|
| `markdown` | Universal UTF-8 text (default for custom optics). |
| `json` | UTF-8 JSON; validated on write. |
| `image` | PNG, JPEG, GIF, or WebP bytes. |
| `video` | MP4 or WebM bytes. |
| `link` | Single absolute `http`/`https` URL. |
| `file` | Arbitrary bytes with filename and media type. |
| `span` | Live slice of a file under the database `code_root` (see below). |

Markdown may still embed images or JSON in the document body; that does not create other optics.

## HTTP

- `GET /databases/{db}/optics` — list optics you can `view`, with `kind` and effective privileges.
- `POST /databases/{db}/optics` — create optic with `name`, optional `description`, optional `kind` (`admin`).
- `GET|PUT|DELETE /databases/{db}/nodes/{slug}/optics/{optic}` — text kinds and span bind/edit (`view` / `edit` / `delete`). `content` is not accepted here.
- `GET|PUT /databases/{db}/nodes/{slug}/optics/{optic}/bytes` — binary kinds (`image`, `video`, `file`).
- `PATCH /databases/{db}/settings` — set local `code_root` for span optics (`_wisedb/workspace.json`).

### Span

1. Admin sets `code_root` to an absolute checkout path on this machine.
2. Bind: `PUT` with `path`, `start_line`, `end_line` (1-based, inclusive).
3. Read: `GET` returns current file lines for that range and a `sha256` of the fragment.
4. Edit: `PUT` with `text` and `base_sha256` from the last read; WiseDB replaces those lines in the real file. `409` if the file changed elsewhere.

## Grants

Database grants still gate access to the database. For custom optics, effective privileges default from database grants:

- `read` → `view`
- `write` or `admin` → `view`, `edit`, `delete`

Root can set overrides: `PUT /users/{id}/grants/{database}/optics/{optic}` with `{"privileges": ["view", "edit"]}`. An empty list explicitly denies optic access. `DELETE` on that path restores inheritance.

## WQL

- `CREATE OPTIC name [JSON|IMAGE|VIDEO|LINK|FILE|SPAN|MARKDOWN] ['description']` / `DROP OPTIC name` (`admin`)
- `SELECT * FROM optics [WHERE slug = '...' [AND name = '...']]`
- `UPDATE optics SET text = '...' WHERE slug = '...' AND name = '...'` (not for binary kinds; span uses stored fragment hash)
- `DELETE FROM optics WHERE slug = '...' AND name = '...'`

See also [[auth]] for session and database grants.
