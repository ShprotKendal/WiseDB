---
type: playbook
slug: wql-layout
title: WQL layout operations
summary: Folders and MOVE PAGE
---
Requires `write` privilege.

```wql
CREATE FOLDER 'Notes/2026'
DROP FOLDER 'Notes/2026'
MOVE FOLDER 'Old' TO 'New'
MOVE PAGE 'slug' TO 'path/to/file.md'
```

`MOVE PAGE` changes on-disk location; the canonical **slug** stays stable (see [[pages]]).

Read folder tree without bodies:

```wql
SELECT * FROM layout
```

REST equivalents: [[http]].
