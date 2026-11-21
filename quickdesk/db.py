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
    "root_cause": "TEXT NOT NULL DEFAULT ''",
    "resolution": "TEXT NOT NULL DEFAULT ''",
    "resolution_summary": "TEXT NOT NULL DEFAULT ''",
    "resolved_by_id": "INTEGER",
    "resolved_at": "TIMESTAMP",
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

def _migrate_demo_accounts(conn):
    """Rename old demo accounts to presentation-friendly names."""
    account_migrations = [
        ('zwx-zwx889', 'yy-zwx123', 'Qd@yy-zwx123'),
        ('admin', 'yy-admin123', 'Qd@yy-admin123'),
        ('admin2', 'yy-liumin123', 'Qd@yy-liumin123'),
        ('requester', 'cw-wangfang123', 'Qd@cw-wangfang123'),
        ('requester2', 'rl-liming123', 'Qd@rl-liming123'),
        ('requester3', 'xs-zhaolei123', 'Qd@xs-zhaolei123'),
        ('assignee', 'js-zhangwei123', 'Qd@js-zhangwei123'),
        ('assignee2', 'js-liqiang123', 'Qd@js-liqiang123'),
        ('assignee3', 'sb-chenyu123', 'Qd@sb-chenyu123'),
        ('assignee4', 'xz-zhoumin123', 'Qd@xz-zhoumin123'),
    ]

    for old_username, new_username, password in account_migrations:
        old_user = conn.execute(
            'SELECT id FROM users WHERE username = ?',
            (old_username,),
        ).fetchone()

        new_user = conn.execute(
            'SELECT id FROM users WHERE username = ?',
            (new_username,),
        ).fetchone()

        if old_user is not None and new_user is None:
            conn.execute(
                '''
                UPDATE users
                SET username = ?,
                    password_hash = ?,
                    must_change_password = 0,
                    password_changed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (
                    new_username,
                    generate_password_hash(password),
                    old_user['id'],
                ),
            )


def _create_default_users(conn):
    """Create or complete course-project demo users."""
    default_users = [
        (
            'yy-zwx123',
            'Qd@yy-zwx123',
            'zwx',
            'admin',
            '运营管理部',
            '工单调度,系统管理',
            20,
        ),
        (
            'yy-admin123',
            'Qd@yy-admin123',
            '系统管理员',
            'admin',
            '运营管理部',
            '工单调度,SLA管理',
            20,
        ),
        (
            'yy-liumin123',
            'Qd@yy-liumin123',
            '刘敏',
            'admin',
            '运营管理部',
            '工单调度,人员协调',
            20,
        ),
        (
            'cw-wangfang123',
            'Qd@cw-wangfang123',
            '王芳',
            'requester',
            '财务部',
            '',
            5,
        ),
        (
            'rl-liming123',
            'Qd@rl-liming123',
            '李明',
            'requester',
            '人力资源部',
            '',
            5,
        ),
        (
            'xs-zhaolei123',
            'Qd@xs-zhaolei123',
            '赵磊',
            'requester',
            '销售部',
            '',
            5,
        ),
        (
            'js-zhangwei123',
            'Qd@js-zhangwei123',
            '张伟',
            'assignee',
            '技术支持部',
            '账号登录,权限认证,应用系统',
            6,
        ),
        (
            'js-liqiang123',
            'Qd@js-liqiang123',
            '李强',
            'assignee',
            '技术支持部',
            '网络故障,办公软件,应用系统',
            6,
        ),
        (
            'sb-chenyu123',
            'Qd@sb-chenyu123',
            '陈宇',
            'assignee',
            '设备维护部',
            '打印机,电脑硬件,设备维修',
            5,
        ),
        (
            'xz-zhoumin123',
            'Qd@xz-zhoumin123',
            '周敏',
            'assignee',
            '行政支持部',
            '办公用品,会议室,行政服务',
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
                root_cause TEXT NOT NULL DEFAULT '',
                resolution TEXT NOT NULL DEFAULT '',
                resolution_summary TEXT NOT NULL DEFAULT '',
                resolved_by_id INTEGER,
                resolved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (requester_id)
                    REFERENCES users (id),
                                FOREIGN KEY (assignee_id)
                    REFERENCES users (id),
                FOREIGN KEY (resolved_by_id)
                    REFERENCES users (id)
                    ON DELETE SET NULL,
                FOREIGN KEY (parent_id)
                    REFERENCES todos (id)
                    ON DELETE CASCADE
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                todo_id INTEGER,
                actor_id INTEGER,
                ticket_title TEXT NOT NULL DEFAULT '',
                actor_name TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                old_value TEXT NOT NULL DEFAULT '',
                new_value TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (todo_id)
                    REFERENCES todos (id)
                    ON DELETE SET NULL,
                FOREIGN KEY (actor_id)
                    REFERENCES users (id)
                    ON DELETE SET NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transfer_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                todo_id INTEGER NOT NULL,
                requested_by_id INTEGER NOT NULL,
                from_assignee_id INTEGER NOT NULL,
                requested_department TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (
                        status IN (
                            'pending',
                            'approved',
                            'rejected',
                            'cancelled'
                        )
                    ),
                reviewed_by_id INTEGER,
                decision_note TEXT NOT NULL DEFAULT '',
                reviewed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (todo_id)
                    REFERENCES todos (id)
                    ON DELETE CASCADE,
                FOREIGN KEY (requested_by_id)
                    REFERENCES users (id),
                FOREIGN KEY (from_assignee_id)
                    REFERENCES users (id),
                FOREIGN KEY (reviewed_by_id)
                    REFERENCES users (id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                todo_id INTEGER NOT NULL,
                requested_by_id INTEGER,
                content_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (todo_id)
                    REFERENCES todos (id)
                    ON DELETE CASCADE,
                FOREIGN KEY (requested_by_id)
                    REFERENCES users (id)
                    ON DELETE SET NULL,
                UNIQUE (
                    todo_id,
                    content_hash,
                    model
                )
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_ai_analyses_todo
            ON ai_analyses (
                todo_id,
                created_at
            )
            """
        )


        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            ai_resolution_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                todo_id INTEGER NOT NULL,
                requested_by_id INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                case_context_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (todo_id)
                    REFERENCES todos (id)
                    ON DELETE CASCADE,
                FOREIGN KEY (requested_by_id)
                    REFERENCES users (id)
                    ON DELETE CASCADE,
                UNIQUE (
                    todo_id,
                    content_hash,
                    case_context_hash,
                    model
                )
            )

            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_transfer_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transfer_request_id INTEGER NOT NULL,
                requested_by_id INTEGER NOT NULL,
                context_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                review_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (transfer_request_id)
                    REFERENCES transfer_requests (id)
                    ON DELETE CASCADE,
                FOREIGN KEY (requested_by_id)
                    REFERENCES users (id),
                UNIQUE (
                    transfer_request_id,
                    context_hash,
                    model
                )
            )
            """

        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_ai_resolution_plans_todo
            ON ai_resolution_plans (
                todo_id,
                created_at
            )
            """
        )


        _ensure_user_columns(conn)
        _ensure_todo_columns(conn)
        _migrate_demo_accounts(conn)
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

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_todos_resolved_at
            ON todos (
                status,
                resolved_at
            )
            """
        )


        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_audit_logs_todo_id
            ON audit_logs (todo_id, created_at)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_audit_logs_actor_id
            ON audit_logs (actor_id, created_at)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_transfer_requests_status
            ON transfer_requests (status, created_at)
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_transfer_requests_one_pending
            ON transfer_requests (todo_id)
            WHERE status = 'pending'
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            ai_dashboard_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_by_id INTEGER NOT NULL,
                context_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                insight_json TEXT NOT NULL,
                created_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
                    DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (requested_by_id)
                    REFERENCES users (id),
                UNIQUE (
                    context_hash,
                    model
                )
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_ai_dashboard_insights_created
            ON ai_dashboard_insights (
                created_at
            )
            """
        )


        conn.commit()
