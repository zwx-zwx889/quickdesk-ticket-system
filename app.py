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

def get_todos_hierarchical():
    with closing(connect_db()) as conn:
        
        # Get all todos with their children count
        todos = conn.execute('''
            SELECT t.*, 
                   COUNT(c.id) as child_count,
                   COALESCE(p.task, '') as parent_task
            FROM todos t
            LEFT JOIN todos c ON t.id = c.parent_id
            LEFT JOIN todos p ON t.parent_id = p.id
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
    assignment_filter = request.args.get(
        'assignment',
        'all',
    ).strip()

    allowed_assignment_filters = {
        'all',
        'unassigned',
        'assigned',
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

    assignee_users = []

    if g.user['role'] == 'admin':
        with closing(connect_db()) as conn:
            assignee_users = conn.execute(
                '''
                SELECT id,
                       username,
                       display_name,
                       department,
                       skills,
                       max_active_tickets
                FROM users
                WHERE role = 'assignee'
                  AND active = 1
                ORDER BY department, display_name
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

        if not assignee_id_text:
            conn.execute(
                '''
                UPDATE todos
                SET assignee = '',
                    assignee_id = NULL,
                    status = 'pending',
                    completed = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (todo_id,),
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

        conn.execute(
            '''
            UPDATE todos
            SET assignee = ?,
                assignee_id = ?,
                status = 'pending',
                completed = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (
                assignee['display_name'],
                assignee['id'],
                todo_id,
            ),
        )
        conn.commit()

    flash(
        f'工单已分派给“{assignee["display_name"]}”！',
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