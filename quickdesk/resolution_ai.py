"""DeepSeek resolution copilot for assigned QuickDesk tickets."""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from quickdesk.ai_service import (
    DEEPSEEK_API_URL,
    DEFAULT_MODEL,
    is_ai_configured,
)
from quickdesk.case_retrieval import build_case_context


DEFAULT_PLAN = {
    'likely_causes': [],
    'clarification_questions': [],
    'diagnostic_steps': [],
    'recommended_solution': '',
    'risks_and_rollback': [],
    'transfer_advice': {
        'needed': False,
        'department': '',
        'reason': '',
    },
    'referenced_case_ids': [],
    'completion_draft': '',
    'confidence': '中',
}


def _normalize_string_list(value, limit=8):
    """Normalize list items and remove model-added numbering."""
    if not isinstance(value, list):
        return []

    normalized = []

    for item in value:
        text = str(item).strip()

        if not text:
            continue

        text = re.sub(
            r'^\s*(?:'
            r'\d+\s*[.．、)）]'
            r'|[-*•]'
            r')\s*',
            '',
            text,
        ).strip()

        if text:
            normalized.append(text)

    return normalized[:limit]


def _normalize_plan(result, cases):
    """Validate the plan and remove hallucinated case IDs."""
    normalized = {
        'likely_causes': _normalize_string_list(
            result.get('likely_causes'),
            limit=6,
        ),
        'clarification_questions':
            _normalize_string_list(
                result.get(
                    'clarification_questions'
                ),
                limit=6,
            ),
        'diagnostic_steps': _normalize_string_list(
            result.get('diagnostic_steps'),
            limit=10,
        ),
        'recommended_solution': '',
        'risks_and_rollback':
            _normalize_string_list(
                result.get('risks_and_rollback'),
                limit=6,
            ),
        'transfer_advice': {
            'needed': False,
            'department': '',
            'reason': '',
        },
        'referenced_case_ids': [],
        'completion_draft': '',
        'confidence': '中',
    }

    for field in (
        'recommended_solution',
        'completion_draft',
    ):
        value = result.get(field)

        if isinstance(value, str):
            normalized[field] = value.strip()

    confidence = result.get('confidence')

    if confidence in {'低', '中', '高'}:
        normalized['confidence'] = confidence

    transfer_advice = result.get(
        'transfer_advice',
        {},
    )

    if isinstance(transfer_advice, dict):
        normalized['transfer_advice'] = {
            'needed': bool(
                transfer_advice.get('needed', False)
            ),
            'department': str(
                transfer_advice.get(
                    'department',
                    '',
                )
            ).strip(),
            'reason': str(
                transfer_advice.get(
                    'reason',
                    '',
                )
            ).strip(),
        }

    allowed_case_ids = {
        int(case['id'])
        for case in cases
    }

    referenced_ids = result.get(
        'referenced_case_ids',
        [],
    )

    if isinstance(referenced_ids, list):
        for case_id in referenced_ids:
            try:
                normalized_id = int(case_id)
            except (TypeError, ValueError):
                continue

            if (
                normalized_id in allowed_case_ids
                and normalized_id
                not in normalized[
                    'referenced_case_ids'
                ]
            ):
                normalized[
                    'referenced_case_ids'
                ].append(normalized_id)

    return normalized


def _extract_response_content(response_data):
    """Read assistant content from Chat Completions JSON."""
    choices = response_data.get('choices', [])

    if not choices:
        raise ValueError(
            'DeepSeek 没有返回处理建议'
        )

    message = choices[0].get('message', {})
    content = message.get('content', '')

    if not isinstance(content, str):
        raise ValueError(
            'DeepSeek 返回内容格式错误'
        )

    content = content.strip()

    if not content:
        raise ValueError(
            'DeepSeek 返回了空的处理建议'
        )

    result = json.loads(content)

    if not isinstance(result, dict):
        raise ValueError(
            'DeepSeek 处理建议必须是 JSON 对象'
        )

    return result


def _system_prompt():
    return """
你是 QuickDesk 企业工单系统中的故障处理助手。

你的服务对象是已经接收工单的处理人员，而不是负责派单的管理员。
你需要结合当前工单与系统检索出的历史解决案例，生成可执行、
可验证、可回退的故障处理建议。

必须遵守：
1. 历史案例只能作为参考，不能声称当前问题已经确认。
2. 不得虚构不存在的日志、配置、检查结果或用户回答。
3. 不得要求用户提供密码、密钥、验证码等敏感信息。
4. 对可能影响生产系统的操作必须给出风险和回退措施。
5. 没有足够证据时，应明确标注“需要进一步确认”。
6. 只有确实引用了提供的历史案例，才能返回对应工单编号。
7. completion_draft 只是解决摘要草稿，必须由处理人执行并确认后使用。
8. 只返回 JSON，不使用 Markdown，不输出 JSON 以外的文字。

返回格式：

{
  "likely_causes": [
    "可能原因1",
    "可能原因2"
  ],
  "clarification_questions": [
    "需要向提交人确认的问题"
  ],
  "diagnostic_steps": [
    "步骤1",
    "步骤2"
  ],
  "recommended_solution": "推荐解决思路",
  "risks_and_rollback": [
    "风险与回退措施"
  ],
  "transfer_advice": {
    "needed": false,
    "department": "",
    "reason": ""
  },
  "referenced_case_ids": [1, 2],
  "completion_draft": "解决摘要草稿",
  "confidence": "低、中或高"
}
""".strip()


def generate_resolution_plan(todo, cases):
    """Generate a role-specific resolution plan with DeepSeek."""
    if not is_ai_configured():
        return {
            'available': False,
            'status': 'disabled',
            'message': (
                '系统尚未配置 DeepSeek API Key。'
            ),
            'model': None,
            'plan': None,
        }

    case_context = build_case_context(cases)

    user_prompt = '\n'.join([
        '当前工单：',
        f"编号：#{todo['id']}",
        f"标题：{todo['task']}",
        (
            '问题描述：'
            f"{todo['description'] or '未填写'}"
        ),
        f"优先级：{todo['priority']}",
        (
            'SLA截止时间：'
            f"{todo['sla_deadline'] or '未设置'}"
        ),
        '',
        '系统检索出的历史案例：',
        case_context,
        '',
        (
            '请基于以上信息生成处理人可执行的'
            '故障诊断与解决计划。'
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
        'max_tokens': 2400,
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

        raw_plan = _extract_response_content(
            response_data
        )
        plan = _normalize_plan(
            raw_plan,
            cases,
        )

        return {
            'available': True,
            'status': 'success',
            'message': (
                'DeepSeek 故障处理方案已生成。'
            ),
            'model': DEFAULT_MODEL,
            'plan': plan,
        }

    except HTTPError as exc:
        if exc.code == 401:
            message = (
                'DeepSeek API Key 无效或已失效。'
            )
        elif exc.code == 402:
            message = (
                'DeepSeek API 账户余额不足。'
            )
        elif exc.code == 429:
            message = (
                'DeepSeek 请求过于频繁，'
                '请稍后再试。'
            )
        else:
            message = (
                'DeepSeek API 请求失败'
                f'（HTTP {exc.code}）。'
            )

        return {
            'available': False,
            'status': 'api_error',
            'message': message,
            'model': DEFAULT_MODEL,
            'plan': None,
        }

    except URLError:
        return {
            'available': False,
            'status': 'network_error',
            'message': (
                '无法连接 DeepSeek API，'
                '请检查网络。'
            ),
            'model': DEFAULT_MODEL,
            'plan': None,
        }

    except TimeoutError:
        return {
            'available': False,
            'status': 'timeout',
            'message': (
                'DeepSeek 故障分析请求超时。'
            ),
            'model': DEFAULT_MODEL,
            'plan': None,
        }

    except Exception as exc:
        return {
            'available': False,
            'status': 'error',
            'message': (
                '故障处理助手暂时不可用：'
                f'{type(exc).__name__}'
            ),
            'model': DEFAULT_MODEL,
            'plan': None,
        }