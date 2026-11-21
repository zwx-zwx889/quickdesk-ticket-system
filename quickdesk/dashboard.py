"""Administrator dashboard for QuickDesk."""

from contextlib import closing
from datetime import datetime, timedelta
import json
from flask import (
    Blueprint,
    render_template,
)

from quickdesk.auth import (
    login_required,
    roles_required,
)
from quickdesk.db import connect_db


dashboard_bp = Blueprint(
    'dashboard',
    __name__,
    url_prefix='/dashboard',
)


@dashboard_bp.route('/')
@login_required
@roles_required('admin')
def dashboard():
    """Display ticket, SLA and workload statistics."""
    with closing(connect_db()) as conn:
        summary = conn.execute(
            '''
            SELECT COUNT(*) AS total_count,

                   COALESCE(SUM(
                       CASE
                           WHEN status = 'pending'
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS pending_count,

                   COALESCE(SUM(
                       CASE
                           WHEN status = 'in_progress'
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS in_progress_count,

                   COALESCE(SUM(
                       CASE
                           WHEN status = 'completed'
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS completed_count,

                   COALESCE(SUM(
                       CASE
                           WHEN assignee_id IS NULL
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS unassigned_count,

                   COALESCE(SUM(
                       CASE
                           WHEN status != 'completed'
                            AND sla_deadline IS NOT NULL
                            AND sla_deadline
                                < CURRENT_TIMESTAMP
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS overdue_count,

                   COALESCE(SUM(
                       CASE
                           WHEN status = 'completed'
                            AND sla_deadline IS NOT NULL
                            AND updated_at <= sla_deadline
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS sla_met_count
            FROM todos
            '''
        ).fetchone()

        completed_count = summary['completed_count']
        sla_met_count = summary['sla_met_count']

        sla_rate = (
            round(
                sla_met_count
                * 100
                / completed_count,
                1,
            )
            if completed_count
            else 0.0
        )

        priority_rows = conn.execute(
            '''
            SELECT priority,
                   COUNT(*) AS ticket_count
            FROM todos
            GROUP BY priority
            ORDER BY CASE priority
                WHEN 'urgent' THEN 1
                WHEN 'high' THEN 2
                WHEN 'medium' THEN 3
                WHEN 'low' THEN 4
                ELSE 5
            END
            '''
        ).fetchall()

        assignee_rows = conn.execute(
            '''
            SELECT u.id,
                   u.display_name,
                   u.department,
                   u.max_active_tickets,

                   COALESCE(SUM(
                       CASE
                           WHEN t.status != 'completed'
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS active_count,

                   COALESCE(SUM(
                       CASE
                           WHEN t.status = 'completed'
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS completed_count,

                   COALESCE(SUM(
                       CASE
                           WHEN t.status != 'completed'
                            AND t.sla_deadline
                                < CURRENT_TIMESTAMP
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS overdue_count

            FROM users u
            LEFT JOIN todos t
                   ON t.assignee_id = u.id
            WHERE u.role = 'assignee'
              AND u.active = 1
            GROUP BY u.id
            ORDER BY u.department,
                     active_count DESC,
                     u.display_name
            '''
        ).fetchall()

        created_rows = conn.execute(
            '''
            SELECT DATE(created_at) AS day,
                   COUNT(*) AS ticket_count
            FROM todos
            WHERE DATE(created_at)
                  >= DATE('now', 'localtime', '-6 days')
            GROUP BY DATE(created_at)
            '''
        ).fetchall()

        completed_rows = conn.execute(
            '''
            SELECT DATE(updated_at) AS day,
                   COUNT(*) AS ticket_count
            FROM todos
            WHERE status = 'completed'
              AND DATE(updated_at)
                  >= DATE('now', 'localtime', '-6 days')
            GROUP BY DATE(updated_at)
            '''
        ).fetchall()

        latest_ai_insight = conn.execute(
            """
            SELECT insight_json,
                   model,
                   created_at,
                   updated_at
            FROM ai_dashboard_insights
            ORDER BY updated_at DESC,
                     id DESC
            LIMIT 1
            """
        ).fetchone()

    ai_insight = None
    ai_insight_model = None
    ai_insight_created_at = None

    if latest_ai_insight is not None:
        try:
            ai_insight = json.loads(
                latest_ai_insight['insight_json']
            )
        except (
            json.JSONDecodeError,
            TypeError,
        ):
            ai_insight = None

        if isinstance(ai_insight, dict):
            ai_insight_model = (
                latest_ai_insight['model']
            )

            ai_insight_created_at = (
                latest_ai_insight['updated_at']
                or latest_ai_insight['created_at']
            )
        else:
            ai_insight = None

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

    priority_counts = {
        row['priority']: row['ticket_count']
        for row in priority_rows
    }

    return render_template(
        'dashboard.html',
        summary=summary,
        sla_rate=sla_rate,
        priority_counts=priority_counts,
        assignee_rows=assignee_rows,
        trend_labels=trend_labels,
        created_trend=created_trend,
        completed_trend=completed_trend,
        ai_insight=ai_insight,
        ai_insight_model=ai_insight_model,
        ai_insight_created_at=(
            ai_insight_created_at
        ),
    )