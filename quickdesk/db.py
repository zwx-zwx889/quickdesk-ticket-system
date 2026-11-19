"""Database helpers for QuickDesk."""

from contextlib import closing
import os
import sqlite3

from werkzeug.security import generate_password_hash


DATABASE = os.getenv(
    "QUICKDESK_DATABASE",
    "quickdesk.db",
)

USER_COLUMNS = {
    "department": "TEXT NOT NULL DEFAULT ''",
    "skills": "TEXT NOT NULL DEFAULT ''",
    "max_active_tickets": "INTEGER NOT NULL DEFAULT 5",
    "must_change_password": "BOOLEAN NOT NULL DEFAULT FALSE",
    "password_changed_at": "TIMESTAMP",
}

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

def _ensure_user_columns(conn):
    """Add profile columns required by newer user versions."""
    existing_columns = _get_table_columns(
        conn,
        "users",
    )

    for column_name, definition in USER_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(
                f"""
                ALTER TABLE users
                ADD COLUMN {column_name} {definition}
                """
            )

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
    """Create or complete course-project demo users."""
    default_users = [
        (
            "admin",
            os.getenv(
                "QUICKDESK_ADMIN_PASSWORD",
                "Admin@123",
            ),
            "系统管理员",
            "admin",
            "运营管理部",
            "工单调度,SLA管理",
            20,
        ),
        (
            "admin2",
            "Admin2@123",
            "刘敏",
            "admin",
            "运营管理部",
            "工单调度,人员协调",
            20,
        ),
        (
            "requester",
            os.getenv(
                "QUICKDESK_REQUESTER_PASSWORD",
                "Requester@123",
            ),
            "王芳",
            "requester",
            "财务部",
            "",
            5,
        ),
        (
            "requester2",
            "Requester2@123",
            "李明",
            "requester",
            "人力资源部",
            "",
            5,
        ),
        (
            "requester3",
            "Requester3@123",
            "赵磊",
            "requester",
            "销售部",
            "",
            5,
        ),
        (
            "assignee",
            os.getenv(
                "QUICKDESK_ASSIGNEE_PASSWORD",
                "Assignee@123",
            ),
            "张伟",
            "assignee",
            "技术支持部",
            "账号登录,权限认证,应用系统",
            6,
        ),
        (
            "assignee2",
            "Assignee2@123",
            "李强",
            "assignee",
            "技术支持部",
            "网络故障,办公软件,应用系统",
            6,
        ),
        (
            "assignee3",
            "Assignee3@123",
            "陈宇",
            "assignee",
            "设备维护部",
            "打印机,电脑硬件,设备维修",
            5,
        ),
        (
            "assignee4",
            "Assignee4@123",
            "周敏",
            "assignee",
            "行政支持部",
            "办公用品,会议室,行政服务",
            5,
        ),
    ]

    for (
        username,
        password,
        display_name,
        role,
        department,
        skills,
        max_active_tickets,
    ) in default_users:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO users (
                username,
                password_hash,
                display_name,
                role,
                department,
                skills,
                max_active_tickets
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                generate_password_hash(password),
                display_name,
                role,
                department,
                skills,
                max_active_tickets,
            ),
        )

        # Existing demo users keep their passwords, but receive
        # profile information when the new fields are still empty.
        if cursor.rowcount == 0:
            conn.execute(
                """
                UPDATE users
                SET department = ?,
                    skills = ?,
                    max_active_tickets = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE username = ?
                  AND department = ''
                """,
                (
                    department,
                    skills,
                    max_active_tickets,
                    username,
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
                department TEXT NOT NULL DEFAULT '',
                skills TEXT NOT NULL DEFAULT '',
                max_active_tickets INTEGER NOT NULL DEFAULT 5,
                must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
                password_changed_at TIMESTAMP,
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

        _ensure_user_columns(conn)
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
