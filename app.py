from contextlib import closing
from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
DATABASE = 'quickdesk.db'

def init_db():
    with closing(sqlite3.connect(DATABASE)) as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'pending',
            requester TEXT NOT NULL DEFAULT '',
            assignee TEXT NOT NULL DEFAULT '',
            sla_deadline TIMESTAMP,
            completed BOOLEAN DEFAULT FALSE,
            parent_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parent_id)
                REFERENCES todos (id)
                ON DELETE CASCADE
        )       
            '''
        )

        try:
            conn.execute(
                'ALTER TABLE todos ADD COLUMN parent_id INTEGER'
            )
        except sqlite3.OperationalError:
            pass
            # 将旧版本的 new 状态迁移为 pending
        conn.execute(
            "UPDATE todos SET status = 'pending' WHERE status = 'new'"
        )
        conn.commit()


def get_todos_hierarchical():
    with closing(sqlite3.connect(DATABASE)) as conn:
        conn.row_factory = sqlite3.Row
        
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

@app.route('/')
def index():
    all_todos = get_todos_hierarchical()

    search_query = request.args.get('q', '').strip()
    status_filter = request.args.get('status', 'all').strip()

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

    current_time = datetime.now().strftime(
        '%Y-%m-%d %H:%M:%S'
    )

    return render_template(
        'index.html',
        todos=todos,
        all_todos=all_todos,
        current_time=current_time,
        search_query=search_query,
        status_filter=status_filter
    )

@app.route('/add', methods=['POST'])
def add_todo():
    task = request.form.get('task', '').strip()
    description = request.form.get('description', '').strip()
    priority = request.form.get('priority', 'medium')
    requester = request.form.get('requester', '').strip()
    assignee = request.form.get('assignee', '').strip()
    parent_id = request.form.get('parent_id') or None

    sla_hours = {
        'urgent': 4,
        'high': 8,
        'medium': 24,
        'low': 72
    }
    deadline = datetime.now() + timedelta(
        hours=sla_hours.get(priority, 24)
    )
    sla_deadline = deadline.strftime('%Y-%m-%d %H:%M:%S')

    if task:
        with closing(sqlite3.connect(DATABASE)) as conn:
            conn.execute(
                '''
                INSERT INTO todos (
                    task,
                    description,
                    priority,
                    status,
                    requester,
                    assignee,
                    sla_deadline,
                    parent_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    task,
                    description,
                    priority,
                    'pending',
                    requester,
                    assignee,
                    sla_deadline,
                    parent_id
                )
            )
            conn.commit()

        flash('工单创建成功！', 'success')
    else:
        flash('请输入工单标题！', 'error')

    return redirect(url_for('index'))

@app.route('/toggle/<int:todo_id>')
def toggle_todo(todo_id):
    with closing(sqlite3.connect(DATABASE)) as conn:
        current = conn.execute(
            'SELECT completed FROM todos WHERE id = ?',
            (todo_id,)
        ).fetchone()

        if current:
            new_completed = 0 if current[0] else 1
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
                (new_completed, new_status, todo_id)
            )
            conn.commit()

    return redirect(url_for('index'))

@app.route('/status/<int:todo_id>', methods=['POST'])
def update_status(todo_id):
    new_status = request.form.get('status', '').strip()

    allowed_statuses = {
        'pending': '待处理',
        'in_progress': '处理中',
        'completed': '已完成'
    }

    if new_status not in allowed_statuses:
        flash('无效的工单状态！', 'error')
        return redirect(url_for('index'))

    completed = 1 if new_status == 'completed' else 0

    with closing(sqlite3.connect(DATABASE)) as conn:
        conn.execute(
            '''
            UPDATE todos
            SET status = ?,
                completed = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (new_status, completed, todo_id)
        )
        conn.commit()

    flash(
        f'工单状态已更新为“{allowed_statuses[new_status]}”！',
        'success'
    )
    return redirect(url_for('index'))

@app.route('/delete/<int:todo_id>')
def delete_todo(todo_id):
    with closing(sqlite3.connect(DATABASE)) as conn:
        # Delete the todo and its children (CASCADE will handle children)
        conn.execute('DELETE FROM todos WHERE id = ?', (todo_id,))
        conn.commit()
    flash('工单已删除！', 'info')
    return redirect(url_for('index'))

@app.route('/toggle_with_children/<int:todo_id>')
def toggle_todo_with_children(todo_id):
    with closing(sqlite3.connect(DATABASE)) as conn:
        current = conn.execute(
            'SELECT completed FROM todos WHERE id = ?',
            (todo_id,)
        ).fetchone()

        if current:
            new_completed = 0 if current[0] else 1
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
                    todo_id
                )
            )
            conn.commit()

    return redirect(url_for('index'))

@app.route('/edit/<int:todo_id>', methods=['POST'])
def edit_todo(todo_id):
    new_task = request.form.get('task', '').strip()
    if new_task:
        with closing(sqlite3.connect(DATABASE)) as conn:
            conn.execute(
                'UPDATE todos SET task = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (new_task, todo_id)
            )
            conn.commit()
        flash('工单更新成功！', 'success')
    return redirect(url_for('index'))

@app.route('/about')
def about():
    return render_template('about.html')

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)