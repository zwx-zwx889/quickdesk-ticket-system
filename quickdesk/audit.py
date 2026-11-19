"""Audit-log helpers for QuickDesk ticket operations."""

from contextlib import closing

from flask import (
    Blueprint,
    render_template,
    request,
)

from quickdesk.auth import (
    login_required,
    roles_required,
)
from quickdesk.db import connect_db


ACTION_LABELS = {
    'created': '创建工单',
    'edited': '编辑工单',
    'assigned': '分派工单',
    'reassigned': '重新分派',
    'unassigned': '取消分派',
    'transferred': '转交工单',
    'transfer_requested': '申请转交',
    'transfer_approved': '批准转交',
    'transfer_rejected': '拒绝转交',
    'status_changed': '更新状态',
    'deleted': '删除工单',
}

audit_bp = Blueprint(
    'audit',
    __name__,
    url_prefix='/audit',
)

@audit_bp.route('/')
@login_required
@roles_required('admin')
def audit_list():
    """Display searchable audit logs for administrators."""
    search_query = request.args.get(
        'q',
        '',
    ).strip()

    action_filter = request.args.get(
        'action',
        'all',
    ).strip()

    try:
        page = max(
            1,
            int(request.args.get('page', '1')),
        )
    except ValueError:
        page = 1

    if (
        action_filter != 'all'
        and action_filter not in ACTION_LABELS
    ):
        action_filter = 'all'

    conditions = []
    parameters = []

    if search_query:
        keyword = f'%{search_query}%'

        conditions.append(
            '''
            (
                audit_logs.ticket_title LIKE ?
                OR audit_logs.actor_name LIKE ?
                OR audit_logs.old_value LIKE ?
                OR audit_logs.new_value LIKE ?
                OR audit_logs.details LIKE ?
            )
            '''
        )

        parameters.extend([
            keyword,
            keyword,
            keyword,
            keyword,
            keyword,
        ])

    if action_filter != 'all':
        conditions.append(
            'audit_logs.action = ?'
        )
        parameters.append(action_filter)

    where_clause = ''

    if conditions:
        where_clause = (
            'WHERE '
            + ' AND '.join(conditions)
        )

    page_size = 20

    with closing(connect_db()) as conn:
        total_count = conn.execute(
            f'''
            SELECT COUNT(*) AS count
            FROM audit_logs
            {where_clause}
            ''',
            parameters,
        ).fetchone()['count']

        total_pages = max(
            1,
            (
                total_count
                + page_size
                - 1
            ) // page_size,
        )

        page = min(page, total_pages)
        offset = (page - 1) * page_size

        logs = conn.execute(
            f'''
            SELECT audit_logs.*,
                   users.username AS actor_username,
                   users.role AS actor_role,
                   users.department AS actor_department
            FROM audit_logs
            LEFT JOIN users
                   ON users.id = audit_logs.actor_id
            {where_clause}
            ORDER BY audit_logs.created_at DESC,
                     audit_logs.id DESC
            LIMIT ?
            OFFSET ?
            ''',
            [
                *parameters,
                page_size,
                offset,
            ],
        ).fetchall()

        today_count = conn.execute(
            '''
            SELECT COUNT(*) AS count
            FROM audit_logs
            WHERE DATE(created_at)
                  = DATE('now', 'localtime')
            '''
        ).fetchone()['count']

    return render_template(
        'audit_logs.html',
        logs=logs,
        action_labels=ACTION_LABELS,
        search_query=search_query,
        action_filter=action_filter,
        total_count=total_count,
        today_count=today_count,
        page=page,
        total_pages=total_pages,
    )

def record_ticket_action(
    conn,
    todo,
    actor,
    action,
    old_value='',
    new_value='',
    details='',
):
    """Write an audit entry using the current transaction."""
    if action not in ACTION_LABELS:
        raise ValueError(f'Unsupported audit action: {action}')

    conn.execute(
        '''
        INSERT INTO audit_logs (
            todo_id,
            actor_id,
            ticket_title,
            actor_name,
            action,
            old_value,
            new_value,
            details
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            todo['id'],
            actor['id'],
            todo['task'],
            actor['display_name'],
            action,
            old_value or '',
            new_value or '',
            details or '',
        ),
    )


def get_ticket_audit_logs(todo_id):
    """Return a ticket's audit history in reverse time order."""
    with closing(connect_db()) as conn:
        return conn.execute(
            '''
            SELECT audit_logs.*,
                   users.username AS actor_username
            FROM audit_logs
            LEFT JOIN users
                   ON users.id = audit_logs.actor_id
            WHERE audit_logs.todo_id = ?
            ORDER BY audit_logs.created_at DESC,
                     audit_logs.id DESC
            ''',
            (todo_id,),
        ).fetchall()


def get_action_label(action):
    """Return a Chinese label for an audit action."""
    return ACTION_LABELS.get(action, action)