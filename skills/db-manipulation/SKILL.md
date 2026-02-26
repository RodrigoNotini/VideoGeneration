---
name: db-manipulation
description: Perform SQLite table operations safely. Use when you need to preview table data with row/column counts, clear all rows from a table with explicit table-name confirmation, or delete a fixed number of rows from a table using a required WHERE filter and deterministic ordering.
---

# DB Manipulation

Use `scripts/db_table_ops.py` for deterministic SQLite table operations.

## Command Recipes

```bash
python skills/db-manipulation/scripts/db_table_ops.py print \
  --db data/db/app.sqlite --table articles --limit 50
```

```bash
python skills/db-manipulation/scripts/db_table_ops.py print \
  --db data/db/app.sqlite --table articles --limit 20 --where "status = 'pending'"
```

```bash
python skills/db-manipulation/scripts/db_table_ops.py clear \
  --db data/db/app.sqlite --table articles --confirm-table articles
```

```bash
python skills/db-manipulation/scripts/db_table_ops.py delete-n \
  --db data/db/app.sqlite --table articles --count 10 --where "status = 'failed'"
```

## Safety Checklist Before Mutation

1. Confirm DB path points to the intended SQLite file.
2. Confirm `--table` is correct and exists.
3. For `clear`, require `--confirm-table` exactly matching `--table`.
4. For `delete-n`, require `--count >= 1` and a non-empty `--where` filter.
5. Never run `DROP TABLE` as part of this skill.

## Behavior and Outputs

- `print`
- Validate DB path and table identifier.
- Validate table exists.
- Print column definitions from `PRAGMA table_info(...)` and `column_count`.
- Print `row_count` from `SELECT COUNT(*)` (respecting `--where` when provided).
- Print preview rows up to `--limit` (default `50`).

- `clear`
- Abort with non-zero exit if `--confirm-table` does not exactly match `--table`.
- Delete all rows from the table.
- Print deleted row summary.

- `delete-n`
- Abort with non-zero exit if `--where` is missing/empty.
- Delete up to `N` matching rows in deterministic order:
- `id ASC` when an `id` column exists, otherwise `rowid ASC`.
- Print deleted row summary.

## Common Failure Cases

- Invalid DB path.
- Invalid table identifier (must match `^[A-Za-z_][A-Za-z0-9_]*$`).
- Table does not exist.
- `clear` confirmation mismatch.
- `delete-n` missing/empty `--where`.
- `delete-n` with `--count < 1`.
