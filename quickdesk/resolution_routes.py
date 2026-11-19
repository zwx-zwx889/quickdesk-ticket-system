"""Routes for the assignee-facing AI resolution copilot."""

from contextlib import closing
import hashlib
import json

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    url_for,
)

from quickdesk.ai_service import DEFAULT_MODEL
from quickdesk.auth import login_required
from quickdesk.case_retrieval import (
    find_similar_resolved_tickets,
)
from quickdesk.db import connect_db
from quickdesk.resolution_ai import (
    generate_resolution_plan,
)


resolution_ai_bp = Blueprint(
    'resolution_ai',
    __name__,
    url_prefix='/assistant',
)


def _hash_value(value):
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )

    return hashlib.sha256(
        serialized.encode('utf-8')
    ).hexdigest()


def build_ticket_content_hash(todo):
    """Hash fields that influence a resolution plan."""
    return _hash_value({
        'assistant_version': 2,
        'task': todo['task'] or '',
        'description': todo['description'] or '',
        'priority': todo['priority'] or '',
        'sla_deadline': todo['sla_deadline'] or '',
    })


def build_case_context_hash(cases):
    """Hash the retrieved historical-case context."""
    return _hash_value([
        {
            'id': case['id'],
            'similarity_score':
                case['similarity_score'],
            'root_cause':
                case['root_cause'],
            'resolution':
                case['resolution'],
            'resolved_at':
                case['resolved_at'],
        }
        for case in cases
    ])


def get_cached_resolution_plan(todo, cases):
    """Return a cached plan for the current content and cases."""
    content_hash = build_ticket_content_hash(todo)
    case_context_hash = build_case_context_hash(
        cases
    )

    with closing(connect_db()) as conn:
        row = conn.execute(
            '''
            SELECT id,
                   model,
                   plan_json,
                   created_at,
                   updated_at
            FROM ai_resolution_plans
            WHERE todo_id = ?
              AND content_hash = ?
              AND case_context_hash = ?
              AND model = ?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (
                todo['id'],
                content_hash,
                case_context_hash,
                DEFAULT_MODEL,
            ),
        ).fetchone()

    if row is None:
        return None

    try:
        plan = json.loads(row['plan_json'])
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(plan, dict):
        return None

    return {
        'id': row['id'],
        'model': row['model'],
        'plan': plan,
        'cases': cases,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'cached': True,
    }


def _is_current_assignee(todo):
    return (
        g.user['role'] == 'assignee'
        and todo['assignee_id'] == g.user['id']
    )


@resolution_ai_bp.post(
    '/resolution/<int:todo_id>'
)
@login_required
def generate_ticket_resolution(todo_id):
    """Generate or load an AI plan for the assigned handler."""
    with closing(connect_db()) as conn:
        todo = conn.execute(
            '''
            SELECT *
            FROM todos
            WHERE id = ?
            ''',
            (todo_id,),
        ).fetchone()

    if todo is None:
        abort(404)

    if not _is_current_assignee(todo):
        abort(403)

    if todo['status'] == 'completed':
        flash(
            '已完成工单不需要重新生成处理方案。',
            'info',
        )
        return redirect(url_for('index'))

    cases = find_similar_resolved_tickets(
        todo,
        limit=3,
    )

    cached_result = get_cached_resolution_plan(
        todo,
        cases,
    )

    if cached_result is not None:
        flash(
            '已读取现有故障处理方案，'
            '未产生新的 DeepSeek 调用。',
            'info',
        )

        return redirect(
            url_for(
                'index',
                assistant_ticket=todo_id,
            )
        )

    result = generate_resolution_plan(
        todo,
        cases,
    )

    if not result['available']:
        flash(
            result.get(
                'message',
                '故障处理助手暂时不可用。',
            ),
            'warning',
        )
        return redirect(url_for('index'))

    plan = result.get('plan')

    if not isinstance(plan, dict):
        flash(
            'DeepSeek 未返回有效的处理方案。',
            'warning',
        )
        return redirect(url_for('index'))

    content_hash = build_ticket_content_hash(
        todo
    )
    case_context_hash = build_case_context_hash(
        cases
    )
    plan_json = json.dumps(
        plan,
        ensure_ascii=False,
        sort_keys=True,
    )

    with closing(connect_db()) as conn:
        conn.execute(
            '''
            INSERT INTO ai_resolution_plans (
                todo_id,
                requested_by_id,
                content_hash,
                case_context_hash,
                model,
                plan_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                todo_id,
                content_hash,
                case_context_hash,
                model
            )
            DO UPDATE SET
                requested_by_id =
                    excluded.requested_by_id,
                plan_json =
                    excluded.plan_json,
                updated_at =
                    CURRENT_TIMESTAMP
            ''',
            (
                todo_id,
                g.user['id'],
                content_hash,
                case_context_hash,
                result['model'],
                plan_json,
            ),
        )
        conn.commit()

    flash(
        'DeepSeek 故障处理方案已生成。',
        'success',
    )

    return redirect(
        url_for(
            'index',
            assistant_ticket=todo_id,
        )
    )