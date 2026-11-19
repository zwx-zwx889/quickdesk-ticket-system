import hashlib
import json
from contextlib import closing

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    url_for,
)

from quickdesk.ai_service import (
    DEFAULT_MODEL,
    analyze_ticket,
)
from quickdesk.auth import login_required
from quickdesk.db import connect_db


ai_bp = Blueprint(
    'ai',
    __name__,
    url_prefix='/ai',
)


def build_content_hash(todo) -> str:
    """为影响 AI 分析的工单内容生成稳定指纹。"""
    content = {
        'task': (todo['task'] or '').strip(),
        'description': (todo['description'] or '').strip(),
        'priority': (todo['priority'] or 'medium').strip(),
    }

    serialized = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )

    return hashlib.sha256(
        serialized.encode('utf-8')
    ).hexdigest()


def get_cached_analysis(todo):
    """
    读取当前工单内容对应的 AI 缓存。

    工单标题、描述或优先级变化后，内容指纹会改变，
    旧结果不会继续用于新内容。
    """
    content_hash = build_content_hash(todo)

    with closing(connect_db()) as conn:
        row = conn.execute(
            """
            SELECT id,
                   model,
                   analysis_json,
                   created_at,
                   updated_at
            FROM ai_analyses
            WHERE todo_id = ?
              AND content_hash = ?
              AND model = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                todo['id'],
                content_hash,
                DEFAULT_MODEL,
            ),
        ).fetchone()

    if row is None:
        return None

    try:
        analysis = json.loads(row['analysis_json'])
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(analysis, dict):
        return None

    return {
        'id': row['id'],
        'model': row['model'],
        'analysis': analysis,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'cached': True,
    }


def _can_analyze_ticket(todo) -> bool:
    """仅允许管理员分析尚未分派的工单。"""
    return (
        g.user['role'] == 'admin'
        and todo['assignee_id'] is None
    )


@ai_bp.post('/analyze/<int:todo_id>')
@login_required
def analyze_ticket_route(todo_id):
    """手动触发工单 AI 分析并保存缓存。"""
    with closing(connect_db()) as conn:
        todo = conn.execute(
            """
            SELECT id,
                   task,
                   description,
                   priority,
                   requester_id,
                   assignee_id
            FROM todos
            WHERE id = ?
            """,
            (todo_id,),
        ).fetchone()

    if todo is None:
        abort(404)

    if not _can_analyze_ticket(todo):
        abort(403)

    cached_result = get_cached_analysis(todo)

    if cached_result is not None:
        flash(
            '已读取该工单现有的 AI 分析结果，'
            '未产生新的 API 调用。',
            'info',
        )
        return redirect(
            url_for(
                'index',
                ai_ticket=todo_id,
            )
        )

    result = analyze_ticket(
        title=todo['task'],
        description=todo['description'],
        priority=todo['priority'],
    )

    if result['status'] == 'disabled':
        flash(
            'AI 服务尚未配置，管理员需要设置 '
            'DEEPSEEK_API_KEY。',
            'warning',
        )
        return redirect(url_for('index'))

    if not result['available']:
        flash(
            result.get(
                'message',
                'AI 服务暂时不可用，请稍后重试。',
            ),
            'warning',
        )
        return redirect(url_for('index'))

    analysis = result.get('analysis')

    if not isinstance(analysis, dict):
        flash('AI 未返回有效的分析结果。', 'warning')
        return redirect(url_for('index'))

    content_hash = build_content_hash(todo)
    analysis_json = json.dumps(
        analysis,
        ensure_ascii=False,
        sort_keys=True,
    )

    with closing(connect_db()) as conn:
        conn.execute(
            """
            INSERT INTO ai_analyses (
                todo_id,
                requested_by_id,
                content_hash,
                model,
                analysis_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (
                todo_id,
                content_hash,
                model
            )
            DO UPDATE SET
                requested_by_id =
                    excluded.requested_by_id,
                analysis_json =
                    excluded.analysis_json,
                updated_at =
                    CURRENT_TIMESTAMP
            """,
            (
                todo_id,
                g.user['id'],
                content_hash,
                result['model'],
                analysis_json,
            ),
        )
        conn.commit()

    flash('AI 工单分析已完成。', 'success')

    return redirect(
        url_for(
            'index',
            ai_ticket=todo_id,
        )
    )