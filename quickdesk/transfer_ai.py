"""DeepSeek-assisted review for ticket transfer requests."""

import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from quickdesk.ai_service import (
    DEEPSEEK_API_URL,
    DEFAULT_MODEL,
    is_ai_configured,
)


VALID_DECISIONS = {
    'approve',
    'reject',
    'manual_review',
}

VALID_LEVELS = {
    '低',
    '中',
    '高',
}

VALID_REASON_QUALITY = {
    '充分',
    '部分充分',
    '不充分',
}


def _row_value(row, key, default=''):
    """Read a value from dicts or sqlite rows."""
    if row is None:
        return default

    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default

    return default if value is None else value


def _normalize_string_list(value, limit=6):
    """Normalize a model-generated string list."""
    if not isinstance(value, list):
        return []

    items = []

    for item in value:
        text = str(item).strip()

        if text and text not in items:
            items.append(text)

    return items[:limit]


def _extract_response_content(response_data):
    """Extract the JSON object returned by DeepSeek."""
    choices = response_data.get('choices', [])

    if not choices:
        raise ValueError('DeepSeek 没有返回转交评估结果')

    message = choices[0].get('message', {})
    content = message.get('content', '')

    if not isinstance(content, str):
        raise ValueError('DeepSeek 返回内容格式错误')

    content = content.strip()

    if content.startswith('```json'):
        content = content[7:]

    if content.startswith('```'):
        content = content[3:]

    if content.endswith('```'):
        content = content[:-3]

    content = content.strip()
    result = json.loads(content)

    if not isinstance(result, dict):
        raise ValueError('DeepSeek 转交评估必须是 JSON 对象')

    return result


def _normalize_review(result, candidates):
    """Validate the AI response and candidate identifiers."""
    decision = str(
        result.get('decision', 'manual_review')
    ).strip()

    if decision not in VALID_DECISIONS:
        decision = 'manual_review'

    confidence = str(
        result.get('confidence', '低')
    ).strip()

    if confidence not in VALID_LEVELS:
        confidence = '低'

    reason_quality = str(
        result.get('reason_quality', '不充分')
    ).strip()

    if reason_quality not in VALID_REASON_QUALITY:
        reason_quality = '不充分'

    sla_risk = str(
        result.get('sla_risk', '中')
    ).strip()

    if sla_risk not in VALID_LEVELS:
        sla_risk = '中'

    allowed_ids = {
        int(_row_value(candidate, 'id', 0))
        for candidate in candidates
        if int(_row_value(candidate, 'id', 0)) > 0
    }

    recommended_ids = []

    raw_ids = result.get(
        'recommended_assignee_ids',
        [],
    )

    if isinstance(raw_ids, list):
        for raw_id in raw_ids:
            try:
                candidate_id = int(raw_id)
            except (TypeError, ValueError):
                continue

            if (
                candidate_id in allowed_ids
                and candidate_id not in recommended_ids
            ):
                recommended_ids.append(candidate_id)

    return {
        'decision': decision,
        'confidence': confidence,
        'reason_quality': reason_quality,
        'skill_gap': bool(
            result.get('skill_gap', False)
        ),
        'sla_risk': sla_risk,
        'summary': str(
            result.get('summary', '')
        ).strip(),
        'evidence': _normalize_string_list(
            result.get('evidence'),
        ),
        'risks': _normalize_string_list(
            result.get('risks'),
        ),
        'questions': _normalize_string_list(
            result.get('questions'),
        ),
        'recommended_assignee_ids': (
            recommended_ids[:3]
        ),
    }


def build_transfer_context_hash(
    transfer_request,
    todo,
    current_assignee,
    candidates,
):
    """Build a hash used to invalidate stale cached reviews."""
    context = {
        'review_version': 1,
        'transfer_id': _row_value(
            transfer_request,
            'id',
        ),
        'reason': _row_value(
            transfer_request,
            'reason',
        ),
        'requested_department': _row_value(
            transfer_request,
            'requested_department',
        ),
        'ticket': {
            'id': _row_value(todo, 'id'),
            'task': _row_value(todo, 'task'),
            'description': _row_value(
                todo,
                'description',
            ),
            'priority': _row_value(
                todo,
                'priority',
            ),
            'status': _row_value(todo, 'status'),
            'sla_deadline': _row_value(
                todo,
                'sla_deadline',
            ),
        },
        'current_assignee': {
            'id': _row_value(
                current_assignee,
                'id',
            ),
            'department': _row_value(
                current_assignee,
                'department',
            ),
            'skills': _row_value(
                current_assignee,
                'skills',
            ),
        },
        'candidates': [
            {
                'id': _row_value(
                    candidate,
                    'id',
                ),
                'department': _row_value(
                    candidate,
                    'department',
                ),
                'skills': _row_value(
                    candidate,
                    'skills',
                ),
                'active_count': _row_value(
                    candidate,
                    'active_count',
                    0,
                ),
                'max_active_tickets': _row_value(
                    candidate,
                    'max_active_tickets',
                    0,
                ),
            }
            for candidate in candidates
        ],
    }

    encoded = json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode('utf-8')

    return hashlib.sha256(encoded).hexdigest()


def _system_prompt():
    return """
你是 QuickDesk 企业工单系统中的转交审批辅助工具。

你的服务对象是负责审核转交申请的管理员。你需要根据工单内容、
转交原因、当前处理人能力、候选处理人的技能与当前负载，生成可解释
的审核建议。

必须遵守：
1. 只能提供辅助建议，不能声称已经批准、拒绝或重新分派工单。
2. 不得虚构日志、用户回答、处理结果或人员技能。
3. 推荐人员只能从系统提供的候选人员中选择。
4. 转交理由不充分时，应建议人工复核或拒绝，并列出需要补充的问题。
5. 应考虑技能匹配、部门匹配、当前负载和 SLA 风险。
6. 不得仅因为候选人员负载较低就推荐，技能匹配应优先。
7. 证据不足时使用 manual_review。
8. 只返回 JSON，不使用 Markdown。

返回格式：
{
  "decision": "approve、reject 或 manual_review",
  "confidence": "低、中或高",
  "reason_quality": "充分、部分充分或不充分",
  "skill_gap": true,
  "sla_risk": "低、中或高",
  "summary": "审核建议摘要",
  "evidence": [
    "支持该建议的证据"
  ],
  "risks": [
    "审批时需要注意的风险"
  ],
  "questions": [
    "需要向申请人补充确认的问题"
  ],
  "recommended_assignee_ids": [1, 2]
}
""".strip()


def generate_transfer_review(
    transfer_request,
    todo,
    current_assignee,
    candidates,
):
    """Generate an explainable transfer review with DeepSeek."""
    if not is_ai_configured():
        return {
            'available': False,
            'status': 'disabled',
            'message': (
                '系统尚未配置 DeepSeek API Key。'
            ),
            'model': None,
            'review': None,
        }

    candidate_lines = []

    for candidate in candidates:
        candidate_lines.append(
            '；'.join([
                (
                    f"人员ID："
                    f"{_row_value(candidate, 'id')}"
                ),
                (
                    f"姓名："
                    f"{_row_value(candidate, 'display_name')}"
                ),
                (
                    f"部门："
                    f"{_row_value(candidate, 'department')}"
                ),
                (
                    f"技能："
                    f"{_row_value(candidate, 'skills') or '未填写'}"
                ),
                (
                    f"当前负载："
                    f"{_row_value(candidate, 'active_count', 0)}"
                    f"/"
                    f"{_row_value(candidate, 'max_active_tickets', 0)}"
                ),
            ])
        )

    user_prompt = '\n'.join([
        '待审批的转交申请：',
        (
            f"转交申请编号："
            f"{_row_value(transfer_request, 'id')}"
        ),
        (
            f"工单编号："
            f"{_row_value(todo, 'id')}"
        ),
        (
            f"工单标题："
            f"{_row_value(todo, 'task')}"
        ),
        (
            f"问题描述："
            f"{_row_value(todo, 'description') or '未填写'}"
        ),
        (
            f"优先级："
            f"{_row_value(todo, 'priority')}"
        ),
        (
            f"当前状态："
            f"{_row_value(todo, 'status')}"
        ),
        (
            f"SLA截止时间："
            f"{_row_value(todo, 'sla_deadline') or '未设置'}"
        ),
        '',
        '当前处理人：',
        (
            f"姓名："
            f"{_row_value(current_assignee, 'display_name')}"
        ),
        (
            f"部门："
            f"{_row_value(current_assignee, 'department')}"
        ),
        (
            f"技能："
            f"{_row_value(current_assignee, 'skills') or '未填写'}"
        ),
        '',
        (
            f"申请转交原因："
            f"{_row_value(transfer_request, 'reason')}"
        ),
        (
            f"建议接收部门："
            f"{_row_value(transfer_request, 'requested_department') or '未指定'}"
        ),
        '',
        '可选接收处理人：',
        *candidate_lines,
        '',
        (
            '请评估该转交是否合理，并从可选人员中给出'
            '最多3名接收建议。'
        ),
    ])

    request_body = {
        'model': DEFAULT_MODEL,
        'messages': [
            {
                'role': 'system',
                'content': _system_prompt(),
            },
            {
                'role': 'user',
                'content': user_prompt,
            },
        ],
        'response_format': {
            'type': 'json_object',
        },
        'thinking': {
            'type': 'enabled',
        },
        'max_tokens': 1800,
        'stream': False,
    }

    api_key = os.getenv(
        'DEEPSEEK_API_KEY',
        '',
    ).strip()

    request_data = json.dumps(
        request_body,
        ensure_ascii=False,
    ).encode('utf-8')

    http_request = Request(
        DEEPSEEK_API_URL,
        data=request_data,
        method='POST',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )

    try:
        with urlopen(
            http_request,
            timeout=60,
        ) as response:
            response_data = json.loads(
                response.read().decode('utf-8')
            )

        raw_review = _extract_response_content(
            response_data
        )

        review = _normalize_review(
            raw_review,
            candidates,
        )

        return {
            'available': True,
            'status': 'success',
            'message': 'DeepSeek 转交评估完成。',
            'model': DEFAULT_MODEL,
            'review': review,
        }

    except HTTPError as exc:
        return {
            'available': False,
            'status': 'error',
            'message': (
                'DeepSeek 服务暂时不可用'
                f'（HTTP {exc.code}）。'
            ),
            'model': DEFAULT_MODEL,
            'review': None,
        }

    except URLError:
        return {
            'available': False,
            'status': 'error',
            'message': '无法连接 DeepSeek 服务。',
            'model': DEFAULT_MODEL,
            'review': None,
        }

    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        return {
            'available': False,
            'status': 'error',
            'message': (
                'DeepSeek 转交评估结果解析失败：'
                f'{type(exc).__name__}'
            ),
            'model': DEFAULT_MODEL,
            'review': None,
        }