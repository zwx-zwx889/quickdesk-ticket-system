"""Transfer-request approval workflow for QuickDesk."""

from contextlib import closing

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from quickdesk.audit import record_ticket_action
from quickdesk.auth import login_required, roles_required
from quickdesk.db import connect_db


transfers_bp = Blueprint(
    'transfers',
    __name__,
    url_prefix='/transfers',
)

@transfers_bp.app_context_processor
def inject_pending_transfer_count():
    """Expose pending transfer count to navigation templates."""
    if g.user is None or g.user['role'] != 'admin':
        return {
            'pending_transfer_count': 0,
        }

    with closing(connect_db()) as conn:
        count = conn.execute(
            '''
            SELECT COUNT(*) AS count
            FROM transfer_requests
            WHERE status = 'pending'
            '''
        ).fetchone()['count']

    return {
        'pending_transfer_count': count,
    }

@transfers_bp.route('/')
@login_required
@roles_required('admin')
def transfer_list():
    """Redirect the old approval page to the admin queue."""
    return redirect(
        url_for(
            'index',
            assignment='transfers',
        )
    )


@transfers_bp.route(
    '/<int:transfer_id>/approve',
    methods=['POST'],
)
@login_required
@roles_required('admin')
def approve_transfer(transfer_id):
    """Approve a request and assign the ticket."""
    assignee_id_text = request.form.get(
        'assignee_id',
        '',
    ).strip()

    decision_note = request.form.get(
        'decision_note',
        '',
    ).strip()

    try:
        new_assignee_id = int(assignee_id_text)
    except ValueError:
        flash('请选择有效的接收处理人！', 'error')
        return redirect(url_for('transfers.transfer_list'))

    with closing(connect_db()) as conn:
        transfer_request = conn.execute(
            '''
            SELECT *
            FROM transfer_requests
            WHERE id = ?
              AND status = 'pending'
            ''',
            (transfer_id,),
        ).fetchone()

        if transfer_request is None:
            flash('转交申请不存在或已经处理！', 'error')
            return redirect(url_for('transfers.transfer_list'))

        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (transfer_request['todo_id'],),
        ).fetchone()

        if todo is None:
            flash('对应工单不存在！', 'error')
            return redirect(url_for('transfers.transfer_list'))

        if todo['status'] == 'completed':
            flash('已完成工单不能批准转交！', 'error')
            return redirect(url_for('transfers.transfer_list'))


        if new_assignee_id == todo['assignee_id']:
            flash('请选择不同的接收处理人！', 'error')
            return redirect(url_for('transfers.transfer_list'))

        new_assignee = conn.execute(
            '''
            SELECT id,
                   display_name,
                   department
            FROM users
            WHERE id = ?
              AND role = 'assignee'
              AND active = 1
            ''',
            (new_assignee_id,),
        ).fetchone()

        if new_assignee is None:
            flash('接收处理人不存在或已停用！', 'error')
            return redirect(url_for('transfers.transfer_list'))

        ticket_cursor = conn.execute(
            '''
            UPDATE todos
            SET assignee = ?,
                assignee_id = ?,
                status = 'pending',
                completed = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND assignee_id IS ?
              AND status != 'completed'
            ''',
            (
                new_assignee['display_name'],
                new_assignee['id'],
                todo['id'],
                todo['assignee_id'],
            ),
        )

        if ticket_cursor.rowcount != 1:
            conn.rollback()
            flash(
                '工单刚刚发生变化，请刷新后重试！',
                'error',
            )
            return redirect(url_for('transfers.transfer_list'))

        request_cursor = conn.execute(
            '''
            UPDATE transfer_requests
            SET status = 'approved',
                reviewed_by_id = ?,
                decision_note = ?,
                reviewed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'pending'
            ''',
            (
                g.user['id'],
                decision_note,
                transfer_id,
            ),
        )

        if request_cursor.rowcount != 1:
            conn.rollback()
            flash(
                '该申请刚刚已被其他管理员处理！',
                'error',
            )
            return redirect(url_for('transfers.transfer_list'))

        details = (
            f'转交原因：{transfer_request["reason"]}'
        )

        if decision_note:
            details += f'；审批说明：{decision_note}'

        record_ticket_action(
            conn,
            todo,
            g.user,
            'transfer_approved',
            old_value=todo['assignee'],
            new_value=new_assignee['display_name'],
            details=details,
        )
        conn.commit()

    flash(
        f'转交申请已批准，工单已分派给'
        f'“{new_assignee["display_name"]}”！',
        'success',
    )
    return redirect(url_for('transfers.transfer_list'))


@transfers_bp.route(
    '/<int:transfer_id>/reject',
    methods=['POST'],
)
@login_required
@roles_required('admin')
def reject_transfer(transfer_id):
    """Reject a pending transfer request."""
    decision_note = request.form.get(
        'decision_note',
        '',
    ).strip()

    if len(decision_note) < 2:
        flash('拒绝申请时请填写审批说明！', 'error')
        return redirect(url_for('transfers.transfer_list'))

    with closing(connect_db()) as conn:
        transfer_request = conn.execute(
            '''
            SELECT *
            FROM transfer_requests
            WHERE id = ?
              AND status = 'pending'
            ''',
            (transfer_id,),
        ).fetchone()

        if transfer_request is None:
            flash('转交申请不存在或已经处理！', 'error')
            return redirect(url_for('transfers.transfer_list'))

        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (transfer_request['todo_id'],),
        ).fetchone()

        request_cursor = conn.execute(
            '''
            UPDATE transfer_requests
            SET status = 'rejected',
                reviewed_by_id = ?,
                decision_note = ?,
                reviewed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status = 'pending'
            ''',
            (
                g.user['id'],
                decision_note,
                transfer_id,
            ),
        )

        if request_cursor.rowcount != 1:
            conn.rollback()
            flash(
                '该申请刚刚已被其他管理员处理！',
                'error',
            )
            return redirect(url_for('transfers.transfer_list'))

        if todo is not None:
            record_ticket_action(
                conn,
                todo,
                g.user,
                'transfer_rejected',
                old_value=todo['assignee'],
                new_value=todo['assignee'],
                details=(
                    f'转交原因：{transfer_request["reason"]}'
                    f'；拒绝说明：{decision_note}'
                ),
            )

        conn.commit()

    flash('转交申请已拒绝！', 'info')
    return redirect(url_for('transfers.transfer_list'))