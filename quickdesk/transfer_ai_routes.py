"""Routes for administrator AI transfer reviews."""

from contextlib import closing
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
from quickdesk.db import connect_db
from quickdesk.transfer_ai import (
    build_transfer_context_hash,
    generate_transfer_review,
)


transfer_ai_bp = Blueprint(
    'transfer_ai',
    __name__,
    url_prefix='/transfers',
)


def _transfer_queue_url(transfer_id=None):
    """Return to the transfer queue."""
    values = {
        'assignment': 'transfers',
    }

    if transfer_id is not None:
        values['transfer_review'] = transfer_id

    return url_for('index', **values)


@transfer_ai_bp.route(
    '/<int:transfer_id>/ai-review',
    methods=['POST'],
)
@login_required
@roles_required('admin')
def generate_ai_transfer_review(transfer_id):
    """Generate or reuse an AI review for a transfer request."""
    with closing(connect_db()) as conn:
        transfer_request = conn.execute(
            """
            SELECT *
            FROM transfer_requests
            WHERE id = ?
              AND status = 'pending'
            """,
            (transfer_id,),
        ).fetchone()

        if transfer_request is None:
            flash(
                '转交申请不存在或已经处理！',
                'error',
            )
            return redirect(_transfer_queue_url())

        todo = conn.execute(
            """
            SELECT *
            FROM todos
            WHERE id = ?
            """,
            (transfer_request['todo_id'],),
        ).fetchone()

        if todo is None:
            flash(
                '对应工单不存在！',
                'error',
            )
            return redirect(_transfer_queue_url())

        current_assignee = conn.execute(
            """
            SELECT id,
                   username,
                   display_name,
                   department,
                   skills,
                   max_active_tickets
            FROM users
            WHERE id = ?
              AND role = 'assignee'
            """,
            (
                transfer_request[
                    'from_assignee_id'
                ],
            ),
        ).fetchone()

        if current_assignee is None:
            flash(
                '当前处理人不存在，无法生成AI评估。',
                'error',
            )
            return redirect(_transfer_queue_url())

        candidates = conn.execute(
            """
            SELECT u.id,
                   u.username,
                   u.display_name,
                   u.department,
                   u.skills,
                   u.max_active_tickets,
                   COUNT(t.id) AS active_count
            FROM users u
            LEFT JOIN todos t
                   ON t.assignee_id = u.id
                  AND t.status IN (
                      'pending',
                      'in_progress'
                  )
            WHERE u.role = 'assignee'
              AND u.active = 1
              AND u.id != ?
            GROUP BY u.id,
                     u.username,
                     u.display_name,
                     u.department,
                     u.skills,
                     u.max_active_tickets
            ORDER BY u.department,
                     active_count,
                     u.display_name
            """,
            (current_assignee['id'],),
        ).fetchall()

        if not candidates:
            flash(
                '当前没有可接收该工单的处理人。',
                'error',
            )
            return redirect(_transfer_queue_url())

        context_hash = build_transfer_context_hash(
            transfer_request,
            todo,
            current_assignee,
            candidates,
        )

        cached = conn.execute(
            """
            SELECT id
            FROM ai_transfer_reviews
            WHERE transfer_request_id = ?
              AND context_hash = ?
              AND model = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                transfer_id,
                context_hash,
                DEFAULT_MODEL,
            ),
        ).fetchone()

        if cached is not None:
            flash(
                '已载入缓存的AI转交评估。',
                'info',
            )
            return redirect(
                _transfer_queue_url(transfer_id)
            )

        result = generate_transfer_review(
            transfer_request,
            todo,
            current_assignee,
            candidates,
        )

        if (
            not result['available']
            or result['review'] is None
        ):
            flash(
                result['message'],
                'error',
            )
            return redirect(_transfer_queue_url())

        conn.execute(
            """
            INSERT INTO ai_transfer_reviews (
                transfer_request_id,
                requested_by_id,
                context_hash,
                model,
                review_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (
                transfer_request_id,
                context_hash,
                model
            )
            DO UPDATE SET
                requested_by_id = excluded.requested_by_id,
                review_json = excluded.review_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                transfer_id,
                g.user['id'],
                context_hash,
                result['model'],
                json.dumps(
                    result['review'],
                    ensure_ascii=False,
                ),
            ),
        )

        conn.commit()

    flash(
        'AI转交评估已生成，请由管理员核实后决定。',
        'success',
    )

    return redirect(
        _transfer_queue_url(transfer_id)
    )