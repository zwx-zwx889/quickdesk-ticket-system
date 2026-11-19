"""Administrative user-account management for QuickDesk."""

import re
import secrets
import sqlite3
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
from werkzeug.security import generate_password_hash

from quickdesk.auth import login_required, roles_required
from quickdesk.db import connect_db


users_bp = Blueprint(
    'users',
    __name__,
    url_prefix='/users',
)


ROLE_LABELS = {
    'admin': '管理员',
    'requester': '提交人',
    'assignee': '处理人',
}

ALLOWED_ROLES = frozenset(ROLE_LABELS)
USERNAME_PATTERN = re.compile(r'^[a-z0-9_.-]{3,30}$')


@users_bp.route('/')
@login_required
@roles_required('admin')
def user_list():
    """Display all QuickDesk accounts."""
    search_query = request.args.get('q', '').strip()

    sql = '''
        SELECT u.*,
               SUM(
                   CASE
                       WHEN t.status != 'completed'
                       THEN 1
                       ELSE 0
                   END
               ) AS active_ticket_count
        FROM users u
        LEFT JOIN todos t
               ON t.assignee_id = u.id
        WHERE 1 = 1
    '''
    parameters = []

    if search_query:
        keyword = f'%{search_query}%'
        sql += '''
            AND (
                u.username LIKE ?
                OR u.display_name LIKE ?
                OR u.department LIKE ?
                OR u.skills LIKE ?
            )
        '''
        parameters.extend([
            keyword,
            keyword,
            keyword,
            keyword,
        ])

    sql += '''
        GROUP BY u.id
        ORDER BY
            CASE u.role
                WHEN 'admin' THEN 1
                WHEN 'requester' THEN 2
                WHEN 'assignee' THEN 3
                ELSE 4
            END,
            u.department,
            u.display_name
    '''

    with closing(connect_db()) as conn:
        users = conn.execute(
            sql,
            parameters,
        ).fetchall()

    return render_template(
        'users.html',
        users=users,
        role_labels=ROLE_LABELS,
        search_query=search_query,
    )


@users_bp.route('/create', methods=['POST'])
@login_required
@roles_required('admin')
def create_user():
    """Create a QuickDesk system account."""
    username = request.form.get(
        'username',
        '',
    ).strip().lower()

    display_name = request.form.get(
        'display_name',
        '',
    ).strip()

    role = request.form.get(
        'role',
        '',
    ).strip()

    department = request.form.get(
        'department',
        '',
    ).strip()

    skills = request.form.get(
        'skills',
        '',
    ).strip()

    password = request.form.get(
        'password',
        '',
    )

    max_active_text = request.form.get(
        'max_active_tickets',
        '5',
    ).strip()

    if not USERNAME_PATTERN.fullmatch(username):
        flash(
            '用户名应为3至30位小写字母、数字、点、横线或下划线！',
            'error',
        )
        return redirect(url_for('users.user_list'))

    if not display_name:
        flash('姓名不能为空！', 'error')
        return redirect(url_for('users.user_list'))

    if role not in ALLOWED_ROLES:
        flash('请选择有效的系统角色！', 'error')
        return redirect(url_for('users.user_list'))

    if not department:
        flash('所属部门不能为空！', 'error')
        return redirect(url_for('users.user_list'))

    if len(password) < 8:
        flash('临时密码长度不能少于8位！', 'error')
        return redirect(url_for('users.user_list'))

    try:
        max_active_tickets = int(max_active_text)
    except ValueError:
        flash('最大在办工单数必须是整数！', 'error')
        return redirect(url_for('users.user_list'))

    if not 1 <= max_active_tickets <= 100:
        flash('最大在办工单数必须在1至100之间！', 'error')
        return redirect(url_for('users.user_list'))

    if role != 'assignee':
        skills = ''

    try:
        with closing(connect_db()) as conn:
            conn.execute(
                '''
                INSERT INTO users (
                    username,
                    password_hash,
                    display_name,
                    role,
                    active,
                    department,
                    skills,
                    max_active_tickets,
                    must_change_password
                )
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, 1)
                ''',
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
            conn.commit()
    except sqlite3.IntegrityError:
        flash('该用户名已经存在！', 'error')
        return redirect(url_for('users.user_list'))

    flash(
        f'账号“{username}”创建成功！',
        'success',
    )
    return redirect(url_for('users.user_list'))


@users_bp.route(
    '/<int:user_id>/reset-password',
    methods=['POST'],
)
@login_required
@roles_required('admin')
def reset_user_password(user_id):
    """Issue a one-time temporary password for an account."""
    if user_id == g.user['id']:
        flash(
            '当前账号请使用“修改密码”功能，不要自行重置！',
            'error',
        )
        return redirect(url_for('users.user_list'))

    temporary_password = f"QD-{secrets.token_urlsafe(6)}"

    with closing(connect_db()) as conn:
        user = conn.execute(
            'SELECT * FROM users WHERE id = ?',
            (user_id,),
        ).fetchone()

        if user is None:
            flash('未找到指定账号！', 'error')
            return redirect(url_for('users.user_list'))

        conn.execute(
            '''
            UPDATE users
            SET password_hash = ?,
                must_change_password = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (
                generate_password_hash(temporary_password),
                user_id,
            ),
        )
        conn.commit()

    flash(
        (
            f'账号“{user["username"]}”已重置。'
            f'一次性临时密码：{temporary_password}。'
            '请立即安全地告知本人，此密码仅显示一次。'
        ),
        'success',
    )
    return redirect(url_for('users.user_list'))


@users_bp.route(
    '/<int:user_id>/toggle-active',
    methods=['POST'],
)
@login_required
@roles_required('admin')
def toggle_user_active(user_id):
    """Activate or deactivate a system account."""
    if user_id == g.user['id']:
        flash('不能停用当前登录账号！', 'error')
        return redirect(url_for('users.user_list'))

    with closing(connect_db()) as conn:
        user = conn.execute(
            'SELECT * FROM users WHERE id = ?',
            (user_id,),
        ).fetchone()

        if user is None:
            flash('未找到指定账号！', 'error')
            return redirect(url_for('users.user_list'))

        if user['active'] and user['role'] == 'assignee':
            active_ticket_count = conn.execute(
                '''
                SELECT COUNT(*) AS count
                FROM todos
                WHERE assignee_id = ?
                  AND status != 'completed'
                ''',
                (user_id,),
            ).fetchone()['count']

            if active_ticket_count:
                flash(
                    '该处理人仍有未完成工单，请先转交后再停用账号！',
                    'error',
                )
                return redirect(url_for('users.user_list'))

        new_active = 0 if user['active'] else 1

        conn.execute(
            '''
            UPDATE users
            SET active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (new_active, user_id),
        )
        conn.commit()

    action = '启用' if new_active else '停用'
    flash(
        f'账号“{user["username"]}”已{action}！',
        'success',
    )
    return redirect(url_for('users.user_list'))
