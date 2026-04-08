#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3

from sqlalchemy import create_engine, text

TABLE_ORDER = [
    "tariffs",
    "users",
    "subscriptions",
    "devices",
    "invoices",
]

BOOLEAN_COLUMNS = {
    "tariffs": {"is_active", "is_popular"},
    "users": {"is_active", "is_admin"},
    "subscriptions": {"auto_renew", "is_lifetime"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-path", required=True)
    parser.add_argument("--postgres-url", required=True)
    return parser.parse_args()


def fetch_rows(sqlite_path: str, table_name: str) -> tuple[list[str], list[dict]]:
    connection = sqlite3.connect(sqlite_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(f"SELECT * FROM {table_name}").fetchall()
        if not rows:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")]
            return columns, []
        columns = rows[0].keys()
        return list(columns), [normalize_row(table_name, dict(row)) for row in rows]
    finally:
        connection.close()


def normalize_row(table_name: str, row: dict) -> dict:
    for column_name in BOOLEAN_COLUMNS.get(table_name, set()):
        if column_name in row and row[column_name] is not None:
            row[column_name] = bool(row[column_name])
    return row


def truncate_target(connection) -> None:
    connection.execute(
        text(
            "TRUNCATE TABLE invoices, devices, subscriptions, users, tariffs RESTART IDENTITY CASCADE"
        )
    )


def insert_rows(connection, table_name: str, columns: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    column_sql = ", ".join(columns)
    values_sql = ", ".join(f":{column}" for column in columns)
    statement = text(
        f"INSERT INTO {table_name} ({column_sql}) VALUES ({values_sql})"
    )
    connection.execute(statement, rows)


def main() -> None:
    args = parse_args()
    target_engine = create_engine(args.postgres_url, future=True)

    with target_engine.begin() as connection:
        truncate_target(connection)
        for table_name in TABLE_ORDER:
            columns, rows = fetch_rows(args.sqlite_path, table_name)
            insert_rows(connection, table_name, columns, rows)
            print(f"{table_name}: {len(rows)}")


if __name__ == "__main__":
    main()
