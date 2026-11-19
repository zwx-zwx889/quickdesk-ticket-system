"""Explainable ticket-assignment recommendations for QuickDesk."""

import re
from contextlib import closing

from quickdesk.db import connect_db


DEPARTMENT_KEYWORDS = {
    '技术支持部': {
        '账号',
        '登录',
        '密码',
        '权限',
        '认证',
        '系统',
        '应用',
        '软件',
        '网络',
    },
    '设备维护部': {
        '打印机',
        '电脑',
        '硬件',
        '设备',
        '维修',
        '故障',
        '显示器',
    },
    '行政支持部': {
        '办公用品',
        '会议室',
        '行政',
        '采购',
        '座位',
        '门禁',
    },
}


def _normalized_text(value):
    """Return lowercase text without unnecessary whitespace."""
    return re.sub(
        r'\s+',
        '',
        value or '',
    ).lower()


def _text_features(value):
    """Create word and character features for similarity checks."""
    text = _normalized_text(value)

    if not text:
        return set()

    features = set(
        re.findall(
            r'[a-z0-9]+',
            text,
        )
    )

    features.update(
        text[index:index + 2]
        for index in range(len(text) - 1)
    )

    return features


def _similarity(left, right):
    """Calculate Jaccard similarity between two texts."""
    left_features = _text_features(left)
    right_features = _text_features(right)

    if not left_features or not right_features:
        return 0.0

    intersection = (
        left_features
        & right_features
    )

    union = (
        left_features
        | right_features
    )

    return len(intersection) / len(union)


def _split_skills(skills):
    """Split a stored skill string into individual skills."""
    return [
        skill.strip()
        for skill in re.split(
            r'[,，;；\n]+',
            skills or '',
        )
        if skill.strip()
    ]


def _skill_matches(ticket_text, skills):
    """Return handler skills that match the ticket text."""
    normalized_ticket = _normalized_text(ticket_text)
    matches = []

    for skill in _split_skills(skills):
        normalized_skill = _normalized_text(skill)

        direct_match = (
            normalized_skill
            and normalized_skill
            in normalized_ticket
        )

        partial_match = any(
            part in normalized_ticket
            for part in _text_features(
                normalized_skill
            )
            if len(part) >= 2
        )

        if direct_match or partial_match:
            matches.append(skill)

    return matches


def _matching_departments(ticket_text):
    """Infer possible departments using business keywords."""
    normalized_ticket = _normalized_text(ticket_text)
    matches = []

    for department, keywords in (
        DEPARTMENT_KEYWORDS.items()
    ):
        if any(
            keyword.lower() in normalized_ticket
            for keyword in keywords
        ):
            matches.append(department)

    return matches


def recommend_assignees(todo_id, limit=3):
    """Return ranked and explainable assignee recommendations."""
    with closing(connect_db()) as conn:
        ticket = conn.execute(
            '''
            SELECT *
            FROM todos
            WHERE id = ?
            ''',
            (todo_id,),
        ).fetchone()

        if ticket is None:
            return []

        assignees = conn.execute(
            '''
            SELECT u.id,
                   u.username,
                   u.display_name,
                   u.department,
                   u.skills,
                   u.max_active_tickets,

                   COALESCE(SUM(
                       CASE
                           WHEN t.status != 'completed'
                           THEN 1
                           ELSE 0
                       END
                   ), 0) AS active_count

            FROM users u
            LEFT JOIN todos t
                   ON t.assignee_id = u.id
            WHERE u.role = 'assignee'
              AND u.active = 1
            GROUP BY u.id
            '''
        ).fetchall()

        history_rows = conn.execute(
            '''
            SELECT assignee_id,
                   task,
                   description
            FROM todos
            WHERE status = 'completed'
              AND assignee_id IS NOT NULL
            '''
        ).fetchall()

    ticket_text = ' '.join([
        ticket['task'] or '',
        ticket['description'] or '',
    ])

    matching_departments = (
        _matching_departments(ticket_text)
    )

    history_by_assignee = {}

    for history in history_rows:
        history_by_assignee.setdefault(
            history['assignee_id'],
            [],
        ).append(
            ' '.join([
                history['task'] or '',
                history['description'] or '',
            ])
        )

    recommendations = []

    for assignee in assignees:
        score = 0
        reasons = []

        skill_matches = _skill_matches(
            ticket_text,
            assignee['skills'],
        )

        if skill_matches:
            skill_score = min(
                50,
                len(skill_matches) * 20,
            )

            score += skill_score

            reasons.append(
                '技能匹配：'
                + '、'.join(skill_matches)
            )

        if (
            assignee['department']
            in matching_departments
        ):
            score += 20

            reasons.append(
                f'问题类型匹配'
                f'{assignee["department"]}'
            )

        histories = history_by_assignee.get(
            assignee['id'],
            [],
        )

        best_similarity = max(
            (
                _similarity(
                    ticket_text,
                    history_text,
                )
                for history_text in histories
            ),
            default=0.0,
        )

        history_score = round(
            best_similarity * 25
        )

        if history_score >= 3:
            score += history_score

            reasons.append(
                f'存在相似历史工单'
                f'（相似度'
                f'{best_similarity * 100:.0f}%）'
            )

        max_active = max(
            1,
            assignee['max_active_tickets'],
        )

        active_count = assignee['active_count']

        load_rate = (
            active_count
            / max_active
        )

        if load_rate >= 1:
            score -= 30
            reasons.append('当前负载已达到上限')
        else:
            load_score = round(
                (1 - load_rate) * 25
            )

            score += load_score

            reasons.append(
                f'当前负载'
                f'{active_count}/{max_active}'
            )

        if (
            ticket['priority']
            in {'urgent', 'high'}
            and load_rate >= 0.8
        ):
            score -= 10
            reasons.append(
                '高优先级工单需规避高负载'
            )

        recommendations.append({
            'user_id': assignee['id'],
            'username': assignee['username'],
            'display_name': (
                assignee['display_name']
            ),
            'department': (
                assignee['department']
            ),
            'skills': assignee['skills'],
            'active_count': active_count,
            'max_active_tickets': max_active,
            'load_rate': round(
                load_rate * 100,
                1,
            ),
            'score': max(0, score),
            'reasons': reasons,
        })

    recommendations.sort(
        key=lambda item: (
            -item['score'],
            item['active_count'],
            item['display_name'],
        )
    )

    return recommendations[:limit]