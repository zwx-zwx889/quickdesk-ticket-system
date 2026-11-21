"""Routes for DeepSeek dashboard operational insights."""

from contextlib import closing
from datetime import datetime, timedelta
import json

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    url_for,
)

from quickdesk.ai_service import DEFAULT_MODEL
from quickdesk.auth import (
    login_required,
    roles_required,
)
from quickdesk.dashboard_ai import (
    build_dashboard_context,
    build_dashboard_context_hash,
    generate_dashboard_insight,
)
from quickdesk.db import connect_db


dashboard_ai_bp = Blueprint(
    'dashboard_ai',
    __name__,
    url_prefix='/dashboard',
)


def _load_operational_context(conn):
    """Load the same operational metrics used by the dashboard."""
    summary = conn.execute(
        """
        SELECT COUNT(*) AS total_count,
               COALESCE(SUM(
                   CASE
                       WHEN status = 'pending'
                       THEN 1 ELSE 0
                   END
               ), 0) AS pending_count,
               COALESCE(SUM(
                   CASE
                       WHEN status = 'in_progress'
                       THEN 1 ELSE 0
                   END
               ), 0) AS in_progress_count,
               COALESCE(SUM(
                   CASE
                       WHEN status = 'completed'
                       THEN 1 ELSE 0
                   END
               ), 0) AS completed_count,
               COALESCE(SUM(
                   CASE
                       WHEN assignee_id IS NULL
                       THEN 1 ELSE 0
                   END
               ), 0) AS unassigned_count,
               COALESCE(SUM(
                   CASE
                       WHEN status != 'completed'
                        AND sla_deadline IS NOT NULL
                        AND sla_deadline
                            < CURRENT_TIMESTAMP
                       THEN 1 ELSE 0
                   END
               ), 0) AS overdue_count,
               COALESCE(SUM(
                   CASE
                       WHEN status = 'completed'
                        AND sla_deadline IS NOT NULL
                        AND updated_at <= sla_deadline
                       THEN 1 ELSE 0
                   END
               ), 0) AS sla_met_count
        FROM todos
        """
    ).fetchone()

    completed_count = summary['completed_count']
    sla_met_count = summary['sla_met_count']

    sla_rate = (
        round(
            sla_met_count * 100 / completed_count,
            1,
        )
        if completed_count
        else 0.0
    )

    priority_rows = conn.execute(
        """
        SELECT priority,
               COUNT(*) AS ticket_count
        FROM todos
        GROUP BY priority
        """
    ).fetchall()

    priority_counts = {
        row['priority']: row['ticket_count']
        for row in priority_rows
    }

    assignee_rows = conn.execute(
        """
        SELECT u.id,
               u.display_name,
               u.department,
               u.max_active_tickets,
               COALESCE(SUM(
                   CASE
                       WHEN t.status != 'completed'
                       THEN 1 ELSE 0
                   END
               ), 0) AS active_count,
               COALESCE(SUM(
                   CASE
                       WHEN t.status = 'completed'
                       THEN 1 ELSE 0
                   END
               ), 0) AS completed_count,
               COALESCE(SUM(
                   CASE
                       WHEN t.status != 'completed'
                        AND t.sla_deadline IS NOT NULL
                        AND t.sla_deadline
                            < CURRENT_TIMESTAMP
                       THEN 1 ELSE 0
                   END
               ), 0) AS overdue_count
        FROM users u
        LEFT JOIN todos t
               ON t.assignee_id = u.id
        WHERE u.role = 'assignee'
          AND u.active = 1
        GROUP BY u.id,
                 u.display_name,
                 u.department,
                 u.max_active_tickets
        ORDER BY u.department,
                 active_count DESC,
                 u.display_name
        """
    ).fetchall()

    created_rows = conn.execute(
        """
        SELECT DATE(created_at) AS day,
               COUNT(*) AS ticket_count
        FROM todos
        WHERE DATE(created_at)
              >= DATE(
                  'now',
                  'localtime',
                  '-6 days'
              )
        GROUP BY DATE(created_at)
        """
    ).fetchall()

    completed_rows = conn.execute(
        """
        SELECT DATE(updated_at) AS day,
               COUNT(*) AS ticket_count
        FROM todos
        WHERE status = 'completed'
          AND DATE(updated_at)
              >= DATE(
                  'now',
                  'localtime',
                  '-6 days'
              )
        GROUP BY DATE(updated_at)
        """
    ).fetchall()

    created_by_day = {
        row['day']: row['ticket_count']
        for row in created_rows
    }

    completed_by_day = {
        row['day']: row['ticket_count']
        for row in completed_rows
    }

    trend_dates = [
        (
            datetime.now()
            - timedelta(days=offset)
        ).strftime('%Y-%m-%d')
        for offset in range(6, -1, -1)
    ]

    trend_labels = [
        date_value[5:]
        for date_value in trend_dates
    ]

    created_trend = [
        created_by_day.get(date_value, 0)
        for date_value in trend_dates
    ]

    completed_trend = [
        completed_by_day.get(date_value, 0)
        for date_value in trend_dates
    ]

    return build_dashboard_context(
        summary,
        sla_rate,
        priority_counts,
        assignee_rows,
        trend_labels,
        created_trend,
        completed_trend,
    )


@dashboard_ai_bp.route(
    '/ai-insight',
    methods=['POST'],
)
@login_required
@roles_required('admin')
def generate_ai_insight():
    """Generate or reuse an operational AI insight."""
    with closing(connect_db()) as conn:
        context = _load_operational_context(conn)
        context_hash = build_dashboard_context_hash(
            context
        )

        cached = conn.execute(
            """
            SELECT id
            FROM ai_dashboard_insights
            WHERE context_hash = ?
              AND model = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                context_hash,
                DEFAULT_MODEL,
            ),
        ).fetchone()

        if cached is not None:
            flash(
                '当前运营数据未变化，已载入缓存的AI洞察。',
                'info',
            )
            return redirect(
                url_for('dashboard.dashboard')
            )

        result = generate_dashboard_insight(
            context
        )

        if (
            not result['available']
            or result['insight'] is None
        ):
            flash(
                result['message'],
                'error',
            )
            return redirect(
                url_for('dashboard.dashboard')
            )

        conn.execute(
            """
            INSERT INTO ai_dashboard_insights (
                requested_by_id,
                context_hash,
                model,
                insight_json
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT (
                context_hash,
                model
            )
            DO UPDATE SET
                requested_by_id = excluded.requested_by_id,
                insight_json = excluded.insight_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                g.user['id'],
                context_hash,
                result['model'],
                json.dumps(
                    result['insight'],
                    ensure_ascii=False,
                ),
            ),
        )

        conn.commit()

    flash(
        'DeepSeek运营洞察已生成。',
        'success',
    )

    return redirect(
        url_for('dashboard.dashboard')
    )