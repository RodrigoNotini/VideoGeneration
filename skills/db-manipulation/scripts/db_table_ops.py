#!/usr/bin/env python3
"""SQLite table operations for preview, clear, and deterministic delete-n."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CliError(Exception):
    """Raised for expected command-line and validation failures."""


def quote_identifier(name: str) -> str:
    if not IDENTIFIER_RE.fullmatch(name):
        raise CliError(
            f"Invalid table identifier '{name}'. Expected pattern: "
            r"^[A-Za-z_][A-Za-z0-9_]*$"
        )
    return f'"{name}"'


def validate_where(where: str | None, *, required: bool) -> str | None:
    if where is None:
        if required:
            raise CliError("Missing required --where filter.")
        return None

    cleaned = where.strip()
    if not cleaned and required:
        raise CliError("Missing required --where filter.")
    if not cleaned:
        return None

    if ";" in cleaned:
        raise CliError("The --where filter cannot contain ';'.")

    return cleaned


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.exists():
        raise CliError(f"Database path does not exist: {db_path}")
    if path.is_dir():
        raise CliError(f"Database path is a directory, not a file: {db_path}")

    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error as exc:
        raise CliError(f"Could not open SQLite database '{db_path}': {exc}") from exc

    conn.row_factory = sqlite3.Row
    return conn


def ensure_table_exists(conn: sqlite3.Connection, table: str) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None:
        raise CliError(f"Table '{table}' does not exist.")


def get_table_columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    table_sql = quote_identifier(table)
    rows = conn.execute(f"PRAGMA table_info({table_sql})").fetchall()
    if not rows:
        raise CliError(f"Could not load columns for table '{table}'.")
    return rows


def pick_order_expr(columns: Iterable[sqlite3.Row]) -> str:
    has_id = any(str(col["name"]).lower() == "id" for col in columns)
    return '"id" ASC' if has_id else "rowid ASC"


def run_print(args: argparse.Namespace) -> int:
    table_sql = quote_identifier(args.table)
    where = validate_where(args.where, required=False)

    with connect(args.db) as conn:
        ensure_table_exists(conn, args.table)
        columns = get_table_columns(conn, args.table)

        count_sql = f"SELECT COUNT(*) AS row_count FROM {table_sql}"
        preview_sql = f"SELECT * FROM {table_sql}"
        if where:
            count_sql += f" WHERE {where}"
            preview_sql += f" WHERE {where}"

        preview_sql += f" LIMIT {int(args.limit)}"

        row_count = int(conn.execute(count_sql).fetchone()["row_count"])
        preview_rows = conn.execute(preview_sql).fetchall()

    print(f"table={args.table}")
    print(f"row_count={row_count}")
    print(f"column_count={len(columns)}")
    print("columns:")
    for col in columns:
        print(
            "- cid={cid} name={name} type={ctype} notnull={notnull} "
            "default={dflt} pk={pk}".format(
                cid=col["cid"],
                name=col["name"],
                ctype=col["type"] or "",
                notnull=col["notnull"],
                dflt=col["dflt_value"],
                pk=col["pk"],
            )
        )

    print(f"preview_rows={len(preview_rows)}")
    if preview_rows:
        headers = list(preview_rows[0].keys())
        print("preview_columns=" + ",".join(headers))
        for row in preview_rows:
            values = [repr(row[h]) for h in headers]
            print("- " + " | ".join(values))

    return 0


def run_clear(args: argparse.Namespace) -> int:
    table_sql = quote_identifier(args.table)
    if args.confirm_table != args.table:
        raise CliError(
            "Confirmation mismatch: --confirm-table must exactly match --table."
        )

    with connect(args.db) as conn:
        ensure_table_exists(conn, args.table)
        before = int(
            conn.execute(f"SELECT COUNT(*) AS c FROM {table_sql}").fetchone()["c"]
        )
        conn.execute(f"DELETE FROM {table_sql}")
        conn.commit()

    print(f"table={args.table}")
    print(f"deleted_rows={before}")
    print("status=cleared")
    return 0


def run_delete_n(args: argparse.Namespace) -> int:
    table_sql = quote_identifier(args.table)
    if int(args.count) < 1:
        raise CliError("--count must be >= 1.")

    where = validate_where(args.where, required=True)

    with connect(args.db) as conn:
        ensure_table_exists(conn, args.table)
        columns = get_table_columns(conn, args.table)
        order_expr = pick_order_expr(columns)

        before_changes = conn.total_changes
        sql = f"""
        WITH target AS (
            SELECT rowid
            FROM {table_sql}
            WHERE {where}
            ORDER BY {order_expr}
            LIMIT ?
        )
        DELETE FROM {table_sql}
        WHERE rowid IN (SELECT rowid FROM target)
        """
        conn.execute(sql, (int(args.count),))
        conn.commit()
        deleted_rows = conn.total_changes - before_changes

    print(f"table={args.table}")
    print(f"deleted_rows={deleted_rows}")
    print(f"requested_count={int(args.count)}")
    print("status=ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Perform safe SQLite table operations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    print_parser = subparsers.add_parser(
        "print", help="Print table preview, row count, and column metadata."
    )
    print_parser.add_argument("--db", required=True, help="Path to SQLite database.")
    print_parser.add_argument("--table", required=True, help="Target table name.")
    print_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of preview rows to print (default: 50).",
    )
    print_parser.add_argument(
        "--where",
        required=False,
        help="Optional SQL predicate for preview/count filtering.",
    )
    print_parser.set_defaults(handler=run_print)

    clear_parser = subparsers.add_parser(
        "clear", help="Delete all rows from table with explicit confirmation."
    )
    clear_parser.add_argument("--db", required=True, help="Path to SQLite database.")
    clear_parser.add_argument("--table", required=True, help="Target table name.")
    clear_parser.add_argument(
        "--confirm-table",
        required=True,
        help="Must exactly match --table to proceed.",
    )
    clear_parser.set_defaults(handler=run_clear)

    delete_n_parser = subparsers.add_parser(
        "delete-n", help="Delete up to N rows matching --where in deterministic order."
    )
    delete_n_parser.add_argument(
        "--db", required=True, help="Path to SQLite database."
    )
    delete_n_parser.add_argument("--table", required=True, help="Target table name.")
    delete_n_parser.add_argument(
        "--count", type=int, required=True, help="Number of rows to delete (>= 1)."
    )
    delete_n_parser.add_argument(
        "--where",
        required=True,
        help="SQL predicate to select deletion candidates.",
    )
    delete_n_parser.set_defaults(handler=run_delete_n)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "print" and int(args.limit) < 1:
        raise CliError("--limit must be >= 1.")

    return int(args.handler(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
