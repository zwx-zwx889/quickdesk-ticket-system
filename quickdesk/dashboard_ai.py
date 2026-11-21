"""DeepSeek operational insights for the administrator dashboard."""

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


VALID_HEALTH_LEVELS = {
    '正常',
    '关注',
    '风险',
}

VALID_CONFIDENCE_LEVELS = {
    '低',
    '中',
    '高',
}

VALID_PRIORITIES = {
    '低',
    '中',
    '高',
}


def _row_value(row, key, default=0):
    """Read a value from a dict or sqlite row."""
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

    normalized = []

    for item in value:
        text = str(item).strip()

        if text and text not in normalized:
            normalized.append(text)

    return normalized[:limit]


def _normalize_recommendations(value):
    """Normalize administrator action recommendations."""
    if not isinstance(value, list):
        return []

    recommendations = []

    for item in value[:5]:
        if not isinstance(item, dict):
            continue

        title = str(
            item.get('title', '')
        ).strip()

        action = str(
            item.get('action', '')
        ).strip()

        if not title or not action:
            continue

        priority = str(
            item.get('priority', '中')
        ).strip()

        if priority not in VALID_PRIORITIES:
            priority = '中'

        recommendations.append({
            'title': title,
            'priority': priority,
            'action': action,
            'expected_benefit': str(
                item.get(
                    'expected_benefit',
                    '',
                )
            ).strip(),
        })

    return recommendations


def _normalize_alerts(value):
    """Normalize AI-generated operational alerts."""
    if not isinstance(value, list):
        return []

    alerts = []

    for item in value[:5]:
        if not isinstance(item, dict):
            continue

        title = str(
            item.get('title', '')
        ).strip()

        reason = str(
            item.get('reason', '')
        ).strip()

        if not title or not reason:
            continue

        severity = str(
            item.get('severity', '中')
        ).strip()

        if severity not in VALID_PRIORITIES:
            severity = '中'

        alerts.append({
            'title': title,
            'severity': severity,
            'reason': reason,
        })

    return alerts


def _normalize_insight(result):
    """Validate the DeepSeek dashboard response."""
    health_level = str(
        result.get('health_level', '关注')
    ).strip()

    if health_level not in VALID_HEALTH_LEVELS:
        health_level = '关注'

    confidence = str(
        result.get('confidence', '低')
    ).strip()

    if confidence not in VALID_CONFIDENCE_LEVELS:
        confidence = '低'

    return {
        'health_level': health_level,
        'confidence': confidence,
        'executive_summary': str(
            result.get('executive_summary', '')
        ).strip(),
        'key_findings': _normalize_string_list(
            result.get('key_findings'),
        ),
        'sla_risks': _normalize_string_list(
            result.get('sla_risks'),
        ),
        'workload_findings': _normalize_string_list(
            result.get('workload_findings'),
        ),
        'alerts': _normalize_alerts(
            result.get('alerts'),
        ),
        'recommendations': _normalize_recommendations(
            result.get('recommendations'),
        ),
    }


def build_dashboard_context(
    summary,
    sla_rate,
    priority_counts,
    assignee_rows,
    trend_labels,
    created_trend,
    completed_trend,
):
    """Build a serializable operational dashboard context."""
    return {
        'analysis_version': 1,
        'summary': {
            'total_count': _row_value(
                summary,
                'total_count',
            ),
            'pending_count': _row_value(
                summary,
                'pending_count',
            ),
            'in_progress_count': _row_value(
                summary,
                'in_progress_count',
            ),
            'completed_count': _row_value(
                summary,
                'completed_count',
            ),
            'unassigned_count': _row_value(
                summary,
                'unassigned_count',
            ),
            'overdue_count': _row_value(
                summary,
                'overdue_count',
            ),
            'sla_met_count': _row_value(
                summary,
                'sla_met_count',
            ),
            'sla_rate': sla_rate,
        },
        'priority_counts': dict(priority_counts),
        'assignees': [
            {
                'display_name': _row_value(
                    row,
                    'display_name',
                    '',
                ),
                'department': _row_value(
                    row,
                    'department',
                    '',
                ),
                'max_active_tickets': _row_value(
                    row,
                    'max_active_tickets',
                ),
                'active_count': _row_value(
                    row,
                    'active_count',
                ),
                'completed_count': _row_value(
                    row,
                    'completed_count',
                ),
                'overdue_count': _row_value(
                    row,
                    'overdue_count',
                ),
            }
            for row in assignee_rows
        ],
        'seven_day_trend': [
            {
                'day': day,
                'created': created,
                'completed': completed,
            }
            for day, created, completed in zip(
                trend_labels,
                created_trend,
                completed_trend,
            )
        ],
    }


def build_dashboard_context_hash(context):
    """Hash the metrics used to invalidate stale insights."""
    encoded = json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode('utf-8')

    return hashlib.sha256(encoded).hexdigest()


def _extract_response_content(response_data):
    """Extract a JSON object from the DeepSeek response."""
    choices = response_data.get('choices', [])

    if not choices:
        raise ValueError(
            'DeepSeek没有返回运营洞察'
        )

    message = choices[0].get('message', {})
    content = message.get('content', '')

    if not isinstance(content, str):
        raise ValueError(
            'DeepSeek返回内容格式错误'
        )

    content = content.strip()

    if not content:
        raise ValueError(
            'DeepSeek返回了空的运营洞察'
        )

    if content.startswith('```json'):
        content = content[7:].strip()
    elif content.startswith('```'):
        content = content[3:].strip()

    if content.endswith('```'):
        content = content[:-3].strip()

    object_start = content.find('{')

    if object_start < 0:
        raise ValueError(
            'DeepSeek返回内容中没有JSON对象'
        )

    decoder = json.JSONDecoder()

    result, _ = decoder.raw_decode(
        content[object_start:]
    )

    if not isinstance(result, dict):
        raise ValueError(
            '运营洞察必须是JSON对象'
        )

    return result


def _system_prompt():
    return """
你是 QuickDesk 企业工单系统的运营分析助手，服务对象是系统管理员。

你只能根据系统提供的汇总指标、最近七天趋势和处理人负载生成运营洞察。

必须遵守：
1. 不得虚构不存在的工单、人员、故障原因或业务数据。
2. 数据量较小时必须提醒样本有限，不能作过度推断。
3. 区分事实和建议，不得把推测描述成已经确认的结论。
4. 建议必须可执行，并说明预期收益。
5. 不得自动分派、审批、修改或删除任何工单。
6. 只返回JSON，不使用Markdown。
7. 内容保持简洁：每个列表最多4项，每项不超过80个汉字。
8. 必须保证JSON完整闭合，不得在JSON前后添加解释文字。

返回格式：
{
  "health_level": "正常、关注或风险",
  "confidence": "低、中或高",
  "executive_summary": "面向管理员的总体摘要",
  "key_findings": [
    "关键发现"
  ],
  "sla_risks": [
    "SLA风险"
  ],
  "workload_findings": [
    "处理人负载发现"
  ],
  "alerts": [
    {
      "title": "预警标题",
      "severity": "低、中或高",
      "reason": "触发该预警的指标依据"
    }
  ],
  "recommendations": [
    {
      "title": "改进建议标题",
      "priority": "低、中或高",
      "action": "管理员可执行的具体动作",
      "expected_benefit": "预期收益"
    }
  ]
}
""".strip()


def generate_dashboard_insight(context):
    """Generate operational dashboard insight with DeepSeek."""
    if not is_ai_configured():
        return {
            'available': False,
            'status': 'disabled',
            'message': (
                '系统尚未配置 DeepSeek API Key。'
            ),
            'model': None,
            'insight': None,
        }

    request_body = {
        'model': DEFAULT_MODEL,
        'messages': [
            {
                'role': 'system',
                'content': _system_prompt(),
            },
            {
                'role': 'user',
                'content': (
                    '请分析以下QuickDesk运营数据：\n'
                    + json.dumps(
                        context,
                        ensure_ascii=False,
                        indent=2,
                    )
                ),
            },
        ],
        'response_format': {
            'type': 'json_object',
        },
        'thinking': {
            'type': 'enabled',
        },
        'max_tokens': 3200,
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

        raw_insight = _extract_response_content(
            response_data
        )

        return {
            'available': True,
            'status': 'success',
            'message': 'DeepSeek运营洞察生成完成。',
            'model': DEFAULT_MODEL,
            'insight': _normalize_insight(
                raw_insight
            ),
        }

    except HTTPError as exc:
        return {
            'available': False,
            'status': 'error',
            'message': (
                'DeepSeek服务暂时不可用'
                f'（HTTP {exc.code}）。'
            ),
            'model': DEFAULT_MODEL,
            'insight': None,
        }

    except URLError:
        return {
            'available': False,
            'status': 'error',
            'message': '无法连接DeepSeek服务。',
            'model': DEFAULT_MODEL,
            'insight': None,
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
                'DeepSeek运营洞察解析失败：'
                f'{type(exc).__name__}'
            ),
            'model': DEFAULT_MODEL,
            'insight': None,
        }