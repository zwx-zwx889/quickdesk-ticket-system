"""Historical resolved-ticket retrieval for QuickDesk."""

from contextlib import closing
import re

from quickdesk.db import connect_db


def _normalize_text(value):
    """Normalize text before similarity calculation."""
    return re.sub(
        r'\s+',
        ' ',
        (value or '').lower().strip(),
    )


def _extract_terms(value):
    """
    Extract English words, numbers and Chinese character n-grams.

    Chinese text does not naturally contain spaces, so adjacent
    two-character terms are used for lightweight local matching.
    """
    text = _normalize_text(value)

    if not text:
        return set()

    terms = set()
    segments = re.findall(
        r'[a-z0-9]+|[\u4e00-\u9fff]+',
        text,
    )

    for segment in segments:
        if re.fullmatch(r'[a-z0-9]+', segment):
            terms.add(segment)
            continue

        if len(segment) <= 6:
            terms.add(segment)

        if len(segment) == 1:
            terms.add(segment)
            continue

        for index in range(len(segment) - 1):
            terms.add(
                segment[index:index + 2]
            )

    return terms


def _jaccard_similarity(left, right):
    """Return Jaccard similarity between two term sets."""
    if not left or not right:
        return 0.0

    union = left | right

    if not union:
        return 0.0

    return len(left & right) / len(union)


def _calculate_similarity(current, candidate):
    """Calculate an explainable local similarity score."""
    current_title = _extract_terms(
        current['task']
    )
    candidate_title = _extract_terms(
        candidate['task']
    )

    current_description = _extract_terms(
        current['description']
    )
    candidate_description = _extract_terms(
        candidate['description']
    )

    current_combined = (
        current_title
        | current_description
    )
    candidate_combined = (
        candidate_title
        | candidate_description
        | _extract_terms(candidate['root_cause'])
        | _extract_terms(
            candidate['resolution_summary']
        )
    )

    title_score = _jaccard_similarity(
        current_title,
        candidate_title,
    )
    description_score = _jaccard_similarity(
        current_description,
        candidate_description,
    )
    combined_score = _jaccard_similarity(
        current_combined,
        candidate_combined,
    )

    priority_bonus = (
        0.05
        if current['priority']
        == candidate['priority']
        else 0.0
    )

    weighted_score = (
        title_score * 0.50
        + description_score * 0.30
        + combined_score * 0.15
        + priority_bonus
    )

    return min(
        100,
        round(weighted_score * 100),
    )


def find_similar_resolved_tickets(
    todo,
    limit=3,
    minimum_score=5,
):
    """
    Return the most similar completed tickets with solutions.

    This function performs only local database retrieval and does
    not call an external AI service.
    """
    with closing(connect_db()) as conn:
        candidates = conn.execute(
            '''
            SELECT id,
                   task,
                   description,
                   priority,
                   assignee,
                   root_cause,
                   resolution,
                   resolution_summary,
                   resolved_at
            FROM todos
            WHERE status = 'completed'
              AND completed = 1
              AND resolution != ''
              AND id != ?
            ORDER BY resolved_at DESC,
                     id DESC
            LIMIT 200
            ''',
            (todo['id'],),
        ).fetchall()

    ranked_cases = []

    for candidate in candidates:
        score = _calculate_similarity(
            todo,
            candidate,
        )

        if score < minimum_score:
            continue

        ranked_cases.append({
            'id': candidate['id'],
            'task': candidate['task'],
            'description': candidate['description'],
            'priority': candidate['priority'],
            'assignee': candidate['assignee'],
            'root_cause': candidate['root_cause'],
            'resolution': candidate['resolution'],
            'resolution_summary': (
                candidate['resolution_summary']
            ),
            'resolved_at': candidate['resolved_at'],
            'similarity_score': score,
        })

    ranked_cases.sort(
        key=lambda item: (
            item['similarity_score'],
            item['resolved_at'] or '',
            item['id'],
        ),
        reverse=True,
    )

    return ranked_cases[:limit]


def build_case_context(cases):
    """Build safe prompt context from retrieved cases."""
    if not cases:
        return (
            '系统未找到足够相似且包含解决记录的'
            '历史工单。'
        )

    sections = []

    for case in cases:
        sections.append(
            '\n'.join([
                (
                    f"历史工单 #{case['id']}："
                    f"{case['task']}"
                ),
                (
                    '本地相似度：'
                    f"{case['similarity_score']}%"
                ),
                (
                    '问题描述：'
                    f"{case['description'] or '未记录'}"
                ),
                (
                    '根本原因：'
                    f"{case['root_cause']}"
                ),
                (
                    '解决方案：'
                    f"{case['resolution']}"
                ),
                (
                    '解决摘要：'
                    f"{case['resolution_summary']}"
                ),
                (
                    '历史处理人：'
                    f"{case['assignee'] or '未记录'}"
                ),
            ])
        )

    return '\n\n---\n\n'.join(sections)