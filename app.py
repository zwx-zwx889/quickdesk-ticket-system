from contextlib import closing
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    g,
)
from quickdesk.transfers import transfers_bp
from quickdesk.audit import record_ticket_action
from datetime import datetime
from quickdesk.db import connect_db, init_db
from quickdesk.sla import (
    calculate_sla_deadline,
    normalize_priority,
)
import os
from quickdesk.auth import (
    auth_bp,
    login_required,
    roles_required,
)
from quickdesk.users import users_bp

app = Flask(__name__)
app.secret_key = os.getenv(
    "QUICKDESK_SECRET_KEY",
    "quickdesk-course-secret-key",
)
app.register_blueprint(auth_bp)
app.register_blueprint(users_bp)
app.register_blueprint(transfers_bp)

def get_todos_hierarchical():
    with closing(connect_db()) as conn:
        
        # Get all todos with their children count
        todos = conn.execute('''
            SELECT t.*,
                   COUNT(c.id) AS child_count,
                   COALESCE(p.task, '') AS parent_task,
                   tr.id AS pending_transfer_id,
                   tr.requested_department AS transfer_requested_department,
                   tr.reason AS transfer_reason
            FROM todos t
            LEFT JOIN todos c
                   ON t.id = c.parent_id
            LEFT JOIN todos p
                   ON t.parent_id = p.id
            LEFT JOIN transfer_requests tr
                   ON tr.todo_id = t.id
                  AND tr.status = 'pending'
            GROUP BY t.id
            ORDER BY 
                COALESCE(t.parent_id, t.id),
                CASE WHEN t.parent_id IS NULL THEN 0 ELSE 1 END,
                t.completed ASC,
                t.created_at DESC
        ''').fetchall()
        
        return todos

def get_todos():
    return get_todos_hierarchical()

def filter_todos_for_user(todos, user):
    """Return only the tickets visible to the current user."""
    if user is None:
        return []

    role = user['role']
    user_id = user['id']
    display_name = user['display_name']

    if role == 'admin':
        return list(todos)

    if role == 'requester':
        return [
            todo for todo in todos
            if todo['requester_id'] == user_id
            or (
                todo['requester_id'] is None
                and todo['requester'] == display_name
            )
        ]

    if role == 'assignee':
        return [
            todo for todo in todos
            if todo['assignee_id'] == user_id
            or (
                todo['assignee_id'] is None
                and todo['assignee'] == display_name
            )
        ]

    return []

def find_user_id(conn, identity, role=None):
    """Find a user ID by username or display name."""
    if not identity:
        return None

    sql = '''
        SELECT id
        FROM users
        WHERE active = 1
          AND (username = ? OR display_name = ?)
    '''
    parameters = [identity, identity]

    if role:
        sql += ' AND role = ?'
        parameters.append(role)

    user = conn.execute(
        sql,
        parameters,
    ).fetchone()

    return user['id'] if user else None

def is_ticket_requester(todo, user):
    """Check whether the ticket belongs to the requester."""
    return (
        todo['requester_id'] == user['id']
        or (
            todo['requester_id'] is None
            and todo['requester'] == user['display_name']
        )
    )


def is_ticket_assignee(todo, user):
    """Check whether the ticket is assigned to the user."""
    return (
        todo['assignee_id'] == user['id']
        or (
            todo['assignee_id'] is None
            and todo['assignee'] == user['display_name']
        )
    )


def can_edit_ticket(todo, user):
    """Admins can edit all tickets; requesters can edit pending own tickets."""
    if user['role'] == 'admin':
        return True

    return (
        user['role'] == 'requester'
        and todo['status'] == 'pending'
        and is_ticket_requester(todo, user)
    )


def can_process_ticket(todo, user):
    """Admins and assigned handlers can update ticket status."""
    if user['role'] == 'admin':
        return True

    return (
        user['role'] == 'assignee'
        and is_ticket_assignee(todo, user)
    )


def can_delete_ticket(todo, user):
    """Admins can delete all; requesters can delete pending own tickets."""
    if user['role'] == 'admin':
        return True

    return (
        user['role'] == 'requester'
        and todo['status'] == 'pending'
        and is_ticket_requester(todo, user)
    )

@app.route('/')
@login_required
def index():
    all_todos = filter_todos_for_user(
        get_todos_hierarchical(),
        g.user,
    )

    search_query = request.args.get('q', '').strip()
    status_filter = request.args.get('status', 'all').strip()
    default_assignment_filter = (
        'unassigned'
        if g.user['role'] == 'admin'
        else 'all'
    )

    assignment_filter = request.args.get(
        'assignment',
        default_assignment_filter,
    ).strip()

    allowed_assignment_filters = {
        'unassigned',
        'assigned',
        'transfers',
    }

    if (
            g.user['role'] != 'admin'
            or assignment_filter not in allowed_assignment_filters
    ):
        assignment_filter = 'all'

    allowed_statuses = {
        'all',
        'pending',
        'in_progress',
        'completed'
    }

    if status_filter not in allowed_statuses:
        status_filter = 'all'

    unassigned_count = len([
        todo for todo in all_todos
        if todo['assignee_id'] is None
    ])

    assigned_count = len([
        todo for todo in all_todos
        if todo['assignee_id'] is not None
    ])

    pending_transfer_requests = []
    transfer_count = 0

    todos = all_todos

    # 按标题、描述、提交人和处理人搜索
    if search_query:
        keyword = search_query.lower()
        todos = [
            todo for todo in todos
            if keyword in (todo['task'] or '').lower()
            or keyword in (todo['description'] or '').lower()
            or keyword in (todo['requester'] or '').lower()
            or keyword in (todo['assignee'] or '').lower()
        ]

    # 按工单状态筛选
    if status_filter != 'all':
        todos = [
            todo for todo in todos
            if todo['status'] == status_filter
        ]

    if assignment_filter == 'unassigned':
        todos = [
            todo for todo in todos
            if todo['assignee_id'] is None
        ]
    elif assignment_filter == 'assigned':
        todos = [
            todo for todo in todos
            if todo['assignee_id'] is not None
        ]
    elif assignment_filter == 'transfers':
        todos = []

    assignee_users = []
    transfer_departments = []

    # 管理员分派工单时使用完整处理人名单
    if g.user['role'] == 'admin':
        with closing(connect_db()) as conn:
            assignee_users = conn.execute(
                '''
                SELECT u.id,
                       u.username,
                       u.display_name,
                       u.department,
                       u.skills,
                       u.max_active_tickets,
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
                WHERE u.role = 'assignee'
                  AND u.active = 1
                GROUP BY u.id
                ORDER BY u.department,
                         active_ticket_count,
                         u.display_name
                '''
            ).fetchall()

            pending_transfer_requests = conn.execute(
                '''
                SELECT tr.*,
                       t.task AS ticket_title,
                       t.status AS ticket_status,
                       t.priority AS ticket_priority,
                       t.assignee AS current_assignee,
                       t.assignee_id AS current_assignee_id,
                       requester.display_name AS requested_by_name,
                       requester.department AS requested_by_department
                FROM transfer_requests tr
                JOIN todos t
                     ON t.id = tr.todo_id
                JOIN users requester
                     ON requester.id = tr.requested_by_id
                WHERE tr.status = 'pending'
                ORDER BY tr.created_at ASC,
                         tr.id ASC
                '''
            ).fetchall()

            transfer_count = len(
                pending_transfer_requests
            )

    # 处理人申请转交时使用可选目标部门
    if g.user['role'] == 'assignee':
        with closing(connect_db()) as conn:
            transfer_departments = conn.execute(
                '''
                SELECT DISTINCT department
                FROM users
                WHERE role = 'assignee'
                  AND active = 1
                  AND department != ''
                ORDER BY department
                '''
            ).fetchall()


    current_time = datetime.now().strftime(
        '%Y-%m-%d %H:%M:%S'
    )


    return render_template(
        'index.html',
        todos=todos,
        all_todos=all_todos,
        current_time=current_time,
        search_query=search_query,
        status_filter=status_filter,
        assignment_filter=assignment_filter,
        assignee_users=assignee_users,
        can_edit_ticket=can_edit_ticket,
        can_process_ticket=can_process_ticket,
        can_delete_ticket=can_delete_ticket,
        transfer_departments=transfer_departments,
        unassigned_count=unassigned_count,
        assigned_count=assigned_count,
        pending_transfer_requests=pending_transfer_requests,
        transfer_count=transfer_count,
    )

@app.route('/add', methods=['POST'])
@login_required
@roles_required('admin', 'requester')
def add_todo():
    task = request.form.get('task', '').strip()
    description = request.form.get(
        'description',
        '',
    ).strip()

    priority = normalize_priority(
        request.form.get('priority', 'medium')
    )

    if g.user['role'] == 'requester':
        requester = g.user['display_name']
        requester_id = g.user['id']
    else:
        requester = request.form.get(
            'requester',
            '',
        ).strip()
        requester_id = None

    assignee = request.form.get(
        'assignee',
        '',
    ).strip()

    parent_id = request.form.get('parent_id') or None
    sla_deadline = calculate_sla_deadline(priority)

    if not task:
        flash('请输入工单标题！', 'error')
        return redirect(url_for('index'))

    with closing(connect_db()) as conn:
        if requester_id is None:
            requester_id = find_user_id(
                conn,
                requester,
                role='requester',
            )

        assignee_id = find_user_id(
            conn,
            assignee,
            role='assignee',
        )

        conn.execute(
            '''
            INSERT INTO todos (
                task,
                description,
                priority,
                status,
                requester,
                assignee,
                requester_id,
                assignee_id,
                sla_deadline,
                parent_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                task,
                description,
                priority,
                'pending',
                requester,
                assignee,
                requester_id,
                assignee_id,
                sla_deadline,
                parent_id,
            )
        )
        conn.commit()

    flash('工单创建成功！', 'success')
    return redirect(url_for('index'))

@app.route('/toggle/<int:todo_id>')
@login_required
def toggle_todo(todo_id):
    with closing(connect_db()) as conn:
        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (todo_id,),
        ).fetchone()

        if todo is None:
            flash('未找到指定工单！', 'error')
            return redirect(url_for('index'))

        if not can_process_ticket(todo, g.user):
            flash('你没有权限更新此工单状态！', 'error')
            return redirect(url_for('index'))

        new_completed = 0 if todo['completed'] else 1
        new_status = (
            'completed' if new_completed else 'pending'
        )

        conn.execute(
            '''
            UPDATE todos
            SET completed = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (new_completed, new_status, todo_id),
        )
        conn.commit()

    return redirect(url_for('index'))

@app.route('/status/<int:todo_id>', methods=['POST'])
@login_required
def update_status(todo_id):
    new_status = request.form.get('status', '').strip()

    allowed_statuses = {
        'pending': '待处理',
        'in_progress': '处理中',
        'completed': '已完成',
    }

    if new_status not in allowed_statuses:
        flash('无效的工单状态！', 'error')
        return redirect(url_for('index'))

    with closing(connect_db()) as conn:
        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (todo_id,),
        ).fetchone()

        if todo is None:
            flash('未找到指定工单！', 'error')
            return redirect(url_for('index'))

        if not can_process_ticket(todo, g.user):
            flash('你没有权限更新此工单状态！', 'error')
            return redirect(url_for('index'))

        completed = 1 if new_status == 'completed' else 0

        conn.execute(
            '''
            UPDATE todos
            SET status = ?,
                completed = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (new_status, completed, todo_id),
        )
        conn.commit()

    flash(
        f'工单状态已更新为“{allowed_statuses[new_status]}”！',
        'success',
    )
    return redirect(url_for('index'))

@app.route('/assign/<int:todo_id>', methods=['POST'])
@login_required
@roles_required('admin')
def assign_ticket(todo_id):
    assignee_id_text = request.form.get(
        'assignee_id',
        '',
    ).strip()

    with closing(connect_db()) as conn:
        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (todo_id,),
        ).fetchone()

        if todo is None:
            flash('未找到指定工单！', 'error')
            return redirect(url_for('index'))

        if todo['status'] == 'completed':
            flash(
                '已完成工单需要重新打开后才能分派！',
                'error',
            )
            return redirect(url_for('index'))

        old_assignee = todo['assignee'] or '未分派'
        old_assignee_id = todo['assignee_id']

        # 取消现有分派
        if not assignee_id_text:
            if old_assignee_id is None:
                flash('该工单当前尚未分派！', 'info')
                return redirect(url_for('index'))

            cursor = conn.execute(
                '''
                UPDATE todos
                SET assignee = '',
                    assignee_id = NULL,
                    status = 'pending',
                    completed = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND assignee_id IS ?
                ''',
                (
                    todo_id,
                    old_assignee_id,
                ),
            )

            if cursor.rowcount != 1:
                conn.rollback()
                flash(
                    '该工单刚刚已被其他管理员修改，请刷新后重试！',
                    'error',
                )
                return redirect(url_for('index'))

            conn.execute(
                '''
                UPDATE transfer_requests
                SET status = 'cancelled',
                    reviewed_by_id = ?,
                    decision_note = '管理员取消分派，原转交申请自动取消',
                    reviewed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE todo_id = ?
                  AND status = 'pending'
                ''',
                (
                    g.user['id'],
                    todo_id,
                ),
            )

            conn.execute(
                '''
                UPDATE transfer_requests
                SET status = 'cancelled',
                    reviewed_by_id = ?,
                    decision_note = '管理员重新分派工单，原转交申请自动取消',
                    reviewed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE todo_id = ?
                  AND status = 'pending'
                ''',
                (
                    g.user['id'],
                    todo_id,
                ),
            )

            record_ticket_action(
                conn,
                todo,
                g.user,
                'unassigned',
                old_value=old_assignee,
                new_value='未分派',
                details='管理员取消工单分派',
            )
            conn.commit()

            flash('工单已取消分派！', 'info')
            return redirect(url_for('index'))

        try:
            assignee_id = int(assignee_id_text)
        except ValueError:
            flash('无效的处理人！', 'error')
            return redirect(url_for('index'))

        assignee = conn.execute(
            '''
            SELECT id, display_name
            FROM users
            WHERE id = ?
              AND role = 'assignee'
              AND active = 1
            ''',
            (assignee_id,),
        ).fetchone()

        if assignee is None:
            flash('未找到可用的处理人账号！', 'error')
            return redirect(url_for('index'))

        if old_assignee_id == assignee['id']:
            flash(
                f'该工单已经分派给“{assignee["display_name"]}”！',
                'info',
            )
            return redirect(url_for('index'))

        action = (
            'assigned'
            if old_assignee_id is None
            else 'reassigned'
        )

        cursor = conn.execute(
            '''
            UPDATE todos
            SET assignee = ?,
                assignee_id = ?,
                status = 'pending',
                completed = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND assignee_id IS ?
            ''',
            (
                assignee['display_name'],
                assignee['id'],
                todo_id,
                old_assignee_id,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            flash(
                '该工单刚刚已被其他管理员分派，请刷新后查看！',
                'error',
            )
            return redirect(url_for('index'))

        record_ticket_action(
            conn,
            todo,
            g.user,
            action,
            old_value=old_assignee,
            new_value=assignee['display_name'],
            details=(
                '管理员首次分派工单'
                if action == 'assigned'
                else '管理员重新分派工单'
            ),
        )
        conn.commit()

    action_text = (
        '分派'
        if action == 'assigned'
        else '重新分派'
    )

    flash(
        f'工单已{action_text}给“{assignee["display_name"]}”！',
        'success',
    )
    return redirect(url_for('index'))

@app.route(
    '/transfer-request/<int:todo_id>',
    methods=['POST'],
)
@login_required
@roles_required('assignee')
def request_ticket_transfer(todo_id):
    reason = request.form.get(
        'reason',
        '',
    ).strip()

    requested_department = request.form.get(
        'requested_department',
        '',
    ).strip()

    if len(reason) < 3:
        flash('请填写至少3个字的转交原因！', 'error')
        return redirect(url_for('index'))

    if len(reason) > 500:
        flash('转交原因不能超过500个字！', 'error')
        return redirect(url_for('index'))

    with closing(connect_db()) as conn:
        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (todo_id,),
        ).fetchone()

        if todo is None:
            flash('未找到指定工单！', 'error')
            return redirect(url_for('index'))

        if todo['status'] == 'completed':
            flash('已完成工单不能申请转交！', 'error')
            return redirect(url_for('index'))

        if todo['assignee_id'] != g.user['id']:
            flash('你只能申请转交自己的工单！', 'error')
            return redirect(url_for('index'))

        existing_request = conn.execute(
            '''
            SELECT id
            FROM transfer_requests
            WHERE todo_id = ?
              AND status = 'pending'
            ''',
            (todo_id,),
        ).fetchone()

        if existing_request is not None:
            flash(
                '该工单已经存在待审批的转交申请！',
                'info',
            )
            return redirect(url_for('index'))

        if requested_department:
            department_exists = conn.execute(
                '''
                SELECT 1
                FROM users
                WHERE role = 'assignee'
                  AND active = 1
                  AND department = ?
                LIMIT 1
                ''',
                (requested_department,),
            ).fetchone()

            if department_exists is None:
                flash('建议的目标部门不存在！', 'error')
                return redirect(url_for('index'))

        cursor = conn.execute(
            '''
            INSERT OR IGNORE INTO transfer_requests (
                todo_id,
                requested_by_id,
                from_assignee_id,
                requested_department,
                reason,
                status
            )
            VALUES (?, ?, ?, ?, ?, 'pending')
            ''',
            (
                todo_id,
                g.user['id'],
                g.user['id'],
                requested_department,
                reason,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            flash(
                '该工单刚刚已经提交了转交申请！',
                'info',
            )
            return redirect(url_for('index'))

        record_ticket_action(
            conn,
            todo,
            g.user,
            'transfer_requested',
            old_value=todo['assignee'],
            new_value=(
                requested_department
                or '由管理员决定接收部门'
            ),
            details=reason,
        )
        conn.commit()

    flash(
        '转交申请已提交，管理员审批前仍由你负责处理！',
        'success',
    )
    return redirect(url_for('index'))
@app.route('/delete/<int:todo_id>')
@login_required
def delete_todo(todo_id):
    with closing(connect_db()) as conn:
        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (todo_id,),
        ).fetchone()

        if todo is None:
            flash('未找到指定工单！', 'error')
            return redirect(url_for('index'))

        if not can_delete_ticket(todo, g.user):
            flash('你没有权限删除此工单！', 'error')
            return redirect(url_for('index'))

        conn.execute(
            'DELETE FROM todos WHERE id = ?',
            (todo_id,),
        )
        conn.commit()

    flash('工单已删除！', 'info')
    return redirect(url_for('index'))

@app.route('/toggle_with_children/<int:todo_id>')
@login_required
def toggle_todo_with_children(todo_id):
    with closing(connect_db()) as conn:
        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (todo_id,),
        ).fetchone()

        if todo is None:
            flash('未找到指定工单！', 'error')
            return redirect(url_for('index'))

        if not can_process_ticket(todo, g.user):
            flash('你没有权限批量更新此工单！', 'error')
            return redirect(url_for('index'))

        new_completed = 0 if todo['completed'] else 1
        new_status = (
            'completed' if new_completed else 'pending'
        )

        conn.execute(
            '''
            UPDATE todos
            SET completed = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? OR parent_id = ?
            ''',
            (
                new_completed,
                new_status,
                todo_id,
                todo_id,
            ),
        )
        conn.commit()

    return redirect(url_for('index'))

@app.route('/edit/<int:todo_id>', methods=['GET', 'POST'])
@login_required
def edit_todo(todo_id):
    with closing(connect_db()) as conn:

        todo = conn.execute(
            'SELECT * FROM todos WHERE id = ?',
            (todo_id,)
        ).fetchone()

    if todo is None:
        flash('未找到指定工单！', 'error')
        return redirect(url_for('index'))

    if not can_edit_ticket(todo, g.user):
        flash('你没有权限编辑此工单！', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        task = request.form.get(
            'task',
            todo['task']
        ).strip()

        description = request.form.get(
            'description',
            todo['description']
        ).strip()

        priority = normalize_priority(
            request.form.get(
                'priority',
                todo['priority']
            ),
            fallback=todo['priority']
        )

        requester = request.form.get(
            'requester',
            todo['requester']
        ).strip()

        if g.user['role'] == 'requester':
            assignee = ''
        else:
            assignee = request.form.get(
                'assignee',
                '',
            ).strip()



        if not task:
            flash('工单标题不能为空！', 'error')
            return redirect(
                url_for('edit_todo', todo_id=todo_id)
            )

        sla_deadline = calculate_sla_deadline(priority)

        with closing(connect_db()) as conn:
            conn.execute(
                '''
                UPDATE todos
                SET task = ?,
                    description = ?,
                    priority = ?,
                    requester = ?,
                    assignee = ?,
                    sla_deadline = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (
                    task,
                    description,
                    priority,
                    requester,
                    assignee,
                    sla_deadline,
                    todo_id
                )
            )
            conn.commit()

        flash('工单信息更新成功！', 'success')
        return redirect(url_for('index'))

    return render_template(
        'edit.html',
        todo=todo
    )

@app.route('/about')
@login_required
def about():
    return render_template('about.html')

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)