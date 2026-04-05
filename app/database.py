from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK (role IN ('viewer', 'analyst', 'admin')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    entry_type TEXT NOT NULL CHECK (entry_type IN ('income', 'expense')),
    category TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_date
ON transactions(user_id, entry_date DESC);

CREATE INDEX IF NOT EXISTS idx_transactions_type
ON transactions(entry_type);

CREATE INDEX IF NOT EXISTS idx_transactions_category
ON transactions(category);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name_nocase
ON users(name COLLATE NOCASE);
"""

POSTGRES_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL,
        password_hash TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL CHECK (role IN ('viewer', 'analyst', 'admin')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        entry_type TEXT NOT NULL CHECK (entry_type IN ('income', 'expense')),
        category TEXT NOT NULL,
        entry_date TEXT NOT NULL,
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, entry_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(entry_type)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name_nocase ON users (LOWER(name))",
]


def is_postgres_database(db_path: str | Path) -> bool:
    raw = str(db_path)
    return raw.startswith("postgres://") or raw.startswith("postgresql://")


def database_backend(db_path: str | Path) -> str:
    return "postgres" if is_postgres_database(db_path) else "sqlite"


def adapt_placeholders(query: str, db_path: str | Path) -> str:
    if database_backend(db_path) == "postgres":
        return query.replace("?", "%s")
    return query


def connect(db_path: str | Path) -> sqlite3.Connection:
    if is_postgres_database(db_path):
        from psycopg import connect as pg_connect
        from psycopg.rows import dict_row

        return pg_connect(str(db_path), row_factory=dict_row)

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


@contextmanager
def managed_connection(db_path: str | Path):
    connection = connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


def initialize_database(db_path: str | Path) -> None:
    with managed_connection(db_path) as connection:
        if database_backend(db_path) == "postgres":
            for statement in POSTGRES_STATEMENTS:
                connection.execute(statement)
        else:
            connection.executescript(SQLITE_SCHEMA)
        user_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        } if database_backend(db_path) == "sqlite" else set()
        if database_backend(db_path) == "sqlite" and "password_hash" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
        if database_backend(db_path) == "postgres":
            connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT NOT NULL DEFAULT ''")
        connection.commit()
