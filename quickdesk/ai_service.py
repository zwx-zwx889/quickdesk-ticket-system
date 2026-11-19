import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEEPSEEK_API_URL = os.getenv(
    'DEEPSEEK_API_URL',
    'https://api.deepseek.com/chat/completions',
)

DEFAULT_MODEL = os.getenv(
    'QUICKDESK_AI_MODEL',
    'deepseek-flash',
)

DEFAULT_RESULT = {
    'category': '暂未分类',
    'summary': '',
    'keywords': [],
    'recommended_department': '由管理员决定',
    'urgency_assessment': '普通',
    'duplicate_risk': '未知',
    'sla_risk': '未知',
    'reasoning': '',
}


def is_ai_configured() -> bool:
    """判断系统是否已经配置 DeepSeek API Key。"""
    return bool(
        os.getenv('DEEPSEEK_API_KEY', '').strip()
    )


def _extract_json(text: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象。"""
    content = (text or '').strip()

    content = re.sub(
        r'^```(?:json)?\s*',
        '',
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(
        r'\s*```$',
        '',
        content,
    )

    if not content:
        raise ValueError('DeepSeek 返回了空内容')

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(
            r'\{.*\}',
            content,
            flags=re.DOTALL,
        )

        if not match:
            raise ValueError(
                'DeepSeek 返回内容中没有有效 JSON'
            )

        result = json.loads(match.group(0))

    if not isinstance(result, dict):
        raise ValueError(
            'DeepSeek 返回结果必须是 JSON 对象'
        )

    return result


def _normalize_result(
    result: dict[str, Any],
) -> dict[str, Any]:
    """校验并补全 DeepSeek 分析结果。"""
    normalized = DEFAULT_RESULT.copy()

    for field in (
        'category',
        'summary',
        'recommended_department',
        'urgency_assessment',
        'duplicate_risk',
        'sla_risk',
        'reasoning',
    ):
        value = result.get(field)

        if isinstance(value, str) and value.strip():
            normalized[field] = value.strip()

    keywords = result.get('keywords', [])

    if isinstance(keywords, list):
        normalized['keywords'] = [
            str(keyword).strip()
            for keyword in keywords
            if str(keyword).strip()
        ][:8]

    elif isinstance(keywords, str):
        normalized['keywords'] = [
            keyword.strip()
            for keyword in re.split(
                r'[,，、]',
                keywords,
            )
            if keyword.strip()
        ][:8]

    return normalized


def _build_instructions() -> str:
    return """
你是 QuickDesk 企业工单协作系统中的智能工单分析助手。

你的职责是分析工单内容，为管理员和处理人员提供辅助判断，
但不能代替管理员执行工单分派，也不能虚构工单中不存在的信息。

请严格返回 JSON 对象，不要使用 Markdown，不要添加代码块，
不要输出 JSON 以外的任何文字。

JSON 格式如下：

{
  "category": "工单问题类别",
  "summary": "一句话问题摘要",
  "keywords": ["关键词1", "关键词2"],
  "recommended_department": "建议接收部门",
  "urgency_assessment": "低、普通、较高或紧急",
  "duplicate_risk": "低、中或高",
  "sla_risk": "低、中或高",
  "reasoning": "简洁说明分类、部门和风险判断依据"
}

要求：
1. keywords 最多返回 8 个。
2. summary 不超过 80 个汉字。
3. recommended_department 优先从技术支持部、设备维护部、
   行政支持部中选择。
4. 无法确定部门时填写“由管理员决定”。
5. duplicate_risk 只能根据当前文本判断，
   不能声称已经查询过数据库。
6. 不得补充或猜测个人敏感信息。
7. 所有字段均使用中文。
""".strip()


def _read_http_error(exc: HTTPError) -> str:
    """将 DeepSeek HTTP 错误转换为安全提示。"""
    if exc.code == 401:
        return 'DeepSeek API Key 无效或已经失效。'

    if exc.code == 402:
        return 'DeepSeek API 账户余额不足。'

    if exc.code == 403:
        return '当前 DeepSeek API Key 没有调用权限。'

    if exc.code == 429:
        return (
            'DeepSeek API 请求过于频繁，'
            '或者账户可用额度不足。'
        )

    return (
        'DeepSeek API 请求失败'
        f'（HTTP {exc.code}）。'
    )


def analyze_ticket(
    title: str,
    description: str,
    priority: str,
) -> dict[str, Any]:
    """
    使用 DeepSeek 分析工单。

    没有配置密钥或请求失败时，不影响工单系统运行。
    """
    api_key = os.getenv(
        'DEEPSEEK_API_KEY',
        '',
    ).strip()

    if not api_key:
        return {
            'available': False,
            'status': 'disabled',
            'message': (
                '系统尚未配置 DeepSeek API Key。'
            ),
            'model': None,
            'analysis': None,
        }

    clean_title = (title or '').strip()
    clean_description = (
        description or ''
    ).strip()
    clean_priority = (
        priority or 'medium'
    ).strip()

    if not clean_title:
        return {
            'available': False,
            'status': 'invalid_input',
            'message': '工单标题不能为空。',
            'model': None,
            'analysis': None,
        }

    ticket_input = (
        f'工单标题：{clean_title}\n'
        f'问题描述：'
        f'{clean_description or "未填写"}\n'
        f'当前优先级：{clean_priority}'
    )

    request_body = {
        'model': DEFAULT_MODEL,
        'messages': [
            {
                'role': 'system',
                'content': _build_instructions(),
            },
            {
                'role': 'user',
                'content': ticket_input,
            },
        ],
        'response_format': {
            'type': 'json_object',
        },
        'thinking': {
            'type': 'disabled',
        },
        'max_tokens': 1200,
        'stream': False,
    }

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
            timeout=30,
        ) as response:
            response_text = response.read().decode(
                'utf-8'
            )

        response_data = json.loads(response_text)

        choices = response_data.get('choices', [])

        if not choices:
            raise ValueError(
                'DeepSeek 没有返回分析结果'
            )

        message = choices[0].get('message', {})
        raw_result = message.get('content', '')

        parsed_result = _extract_json(raw_result)
        analysis = _normalize_result(
            parsed_result
        )

        return {
            'available': True,
            'status': 'success',
            'message': 'DeepSeek 工单分析完成。',
            'model': DEFAULT_MODEL,
            'analysis': analysis,
        }

    except HTTPError as exc:
        return {
            'available': False,
            'status': 'api_error',
            'message': _read_http_error(exc),
            'model': DEFAULT_MODEL,
            'analysis': None,
        }

    except URLError:
        return {
            'available': False,
            'status': 'network_error',
            'message': (
                '无法连接 DeepSeek API，'
                '请检查网络连接。'
            ),
            'model': DEFAULT_MODEL,
            'analysis': None,
        }

    except TimeoutError:
        return {
            'available': False,
            'status': 'timeout',
            'message': 'DeepSeek API 请求超时。',
            'model': DEFAULT_MODEL,
            'analysis': None,
        }

    except Exception as exc:
        return {
            'available': False,
            'status': 'error',
            'message': (
                'DeepSeek 服务暂时不可用：'
                f'{type(exc).__name__}'
            ),
            'model': DEFAULT_MODEL,
            'analysis': None,
        }