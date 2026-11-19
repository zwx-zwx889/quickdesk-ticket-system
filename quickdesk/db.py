"""Database helpers for QuickDesk."""

from contextlib import closing
import os
import sqlite3

from werkzeug.security import generate_password_hash


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
    "requester_id": "INTEGER",
    "assignee_id": "INTEGER",
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


def _create_default_users(conn):
    """Create course-project demo users when none exist."""
    user_count = conn.execute(
        "SELECT COUNT(*) AS count FROM users"
    ).fetchone()["count"]

    if user_count:
        return

    default_users = [
        (
            "admin",
            os.getenv(
                "QUICKDESK_ADMIN_PASSWORD",
                "Admin@123",
            ),
            "系统管理员",
            "admin",
        ),
        (
            "requester",
            os.getenv(
                "QUICKDESK_REQUESTER_PASSWORD",
                "Requester@123",
            ),
            "王芳",
            "requester",
        ),
        (
            "assignee",
            os.getenv(
                "QUICKDESK_ASSIGNEE_PASSWORD",
                "Assignee@123",
            ),
            "张伟",
            "assignee",
        ),
    ]

    for username, password, display_name, role in default_users:
        conn.execute(
            """
            INSERT INTO users (
                username,
                password_hash,
                display_name,
                role
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                username,
                generate_password_hash(password),
                display_name,
                role,
            ),
        )


def init_db():
    """Create and upgrade the QuickDesk database."""
    with closing(connect_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK (
                        role IN (
                            'admin',
                            'requester',
                            'assignee'
                        )
                    ),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

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
                requester_id INTEGER,
                assignee_id INTEGER,
                sla_deadline TIMESTAMP,
                completed BOOLEAN DEFAULT FALSE,
                parent_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (requester_id)
                    REFERENCES users (id),
                FOREIGN KEY (assignee_id)
                    REFERENCES users (id),
                FOREIGN KEY (parent_id)
                    REFERENCES todos (id)
                    ON DELETE CASCADE
            )
            """
        )

        _ensure_todo_columns(conn)
        _create_default_users(conn)

        conn.execute(
            """
            UPDATE todos
            SET status = 'pending'
            WHERE status = 'new'
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_todos_status
            ON todos (status)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_todos_assignee_id
            ON todos (assignee_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_todos_created_at
            ON todos (created_at)
            """
        )

        conn.commit()