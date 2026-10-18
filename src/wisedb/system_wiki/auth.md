---
type: playbook
slug: auth
title: Sessions, users, and grants
summary: Login, root, and per-database privileges
---
## Login

`POST /auth/login` with `{"username": "...", "password": "..."}` returns a bearer `token`. Send `Authorization: Bearer <token>` on protected routes. `DELETE /auth/session` revokes the session.

`root` authenticates with `WISEDB_ROOT_PASSWORD`. The server refuses non-loopback bind without that password set.

## Users and grants (root)

- `POST /users`, `GET /users`, `DELETE /users/{id}`
- `PUT /users/{id}/grants/{database}` with `{"privileges": ["read"]}` (also `write`, `admin`)
- `DELETE /users/{id}/grants/{database}` — clear grants
- `PUT /users/{id}/grants/{database}/optics/{optic}` — override `view` / `edit` / `delete` on a custom optic (not `content`)
- `DELETE /users/{id}/grants/{database}/optics/{optic}` — restore inherited optic privileges

`admin` on a database allows dropping it. Root bypasses grants on normal databases.

## Privileges vs WQL

- `read`: `SELECT`, `SEARCH`, `SCHEMA`, layout catalog reads
- `write`: `INSERT`, `UPDATE`, `DELETE`, folder/page layout statements
- `parse` reports required privilege without executing

## System wiki exception

Database `_wiki` is read-only for everyone, including root. Grants on `_wiki` are rejected. See [[system-wiki]].
