"""Audit-log helpers for QuickDesk ticket operations."""

from contextlib import closing

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