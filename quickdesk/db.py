"""Database helpers for QuickDesk."""

from contextlib import closing
import os
import sqlite3


DATABASE = os.getenv(
    "QUICKDESK_DATABASE",
    "quickdesk.db",
)


TODO_COLUMNS = {
    "description": "TEXT NOT NULL DEFAULT ''",
    "priority": "TEXT NOT NULL DEFAULT 'medium'",
    "status": "TEXT NOT NULL DEFAULT 'pending'",
    "requester": "TEXT NOT NULL DEFAULT ''",
    "assignee": "TEXT NOT NULL DEFAULT ''",
    "sla_deadline": "TIMESTAMP",
    "parent_id": "INTEGER",
}


def connect_db():
    """Create a configured SQLite connection."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_table_columns(conn, table_name):
    """Return the existing column names for a table."""
    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def _ensure_todo_columns(conn):
    """Add columns required by newer QuickDesk versions."""
    existing_columns = _get_table_columns(
        conn,
        "todos",
    )

    for column_name, definition in TODO_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(
                f"""
                ALTER TABLE todos
                ADD COLUMN {column_name} {definition}
                """
            )


def init_db():
    """Create and upgrade the QuickDesk database."""
    with closing(connect_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'pending',
                requester TEXT NOT NULL DEFAULT '',
                assignee TEXT NOT NULL DEFAULT '',
                sla_deadline TIMESTAMP,
                completed BOOLEAN DEFAULT FALSE,
                parent_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id)
                    REFERENCES todos (id)
                    ON DELETE CASCADE
            )
            """
        )

        _ensure_todo_columns(conn)

        conn.execute(
            """
            UPDATE todos
            SET status = 'pending'
            WHERE status = 'new'
            """
        )

        conn.commit()