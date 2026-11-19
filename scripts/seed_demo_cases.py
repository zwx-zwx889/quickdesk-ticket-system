"""Insert resolved demo tickets for QuickDesk presentations."""

from datetime import datetime, timedelta
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from quickdesk.db import connect_db, init_db


DEMO_CASES = [
    {
        'task': '财务系统认证服务登录失败',
        'description': (
            '多名财务人员输入正确账号密码后'
            '仍提示身份验证失败。'
        ),
        'priority': 'urgent',
        'requester_username': 'cw-wangfang123',
        'assignee_username': 'js-zhangwei123',
        'root_cause': (
            '统一认证服务连接池耗尽，'
            '导致认证请求持续超时。'
        ),
        'resolution': (
            '检查认证服务日志与连接池监控，'
            '确认连接未及时释放。重启异常节点，'
            '调整连接池上限并验证财务系统登录恢复。'
        ),
        'resolution_summary': (
            '恢复认证服务连接池并完成登录验证'
        ),
        'days_ago': 18,
    },
    {
        'task': '员工账号被锁定无法登录',
        'description': (
            '用户连续输入错误密码后，'
            '即使使用正确密码也无法登录系统。'
        ),
        'priority': 'medium',
        'requester_username': 'rl-liming123',
        'assignee_username': 'js-zhangwei123',
        'root_cause': (
            '账号触发连续登录失败安全策略，'
            '被统一认证平台临时锁定。'
        ),
        'resolution': (
            '核实用户身份与锁定记录，按账号管理流程'
            '解除锁定，提醒用户修改密码并重新登录验证。'
        ),
        'resolution_summary': (
            '解除账号锁定并完成登录验证'
        ),
        'days_ago': 14,
    },
    {
        'task': '财务系统凭证保存失败',
        'description': (
            '录入财务凭证后点击保存，'
            '页面提示提交失败且数据未写入。'
        ),
        'priority': 'high',
        'requester_username': 'cw-wangfang123',
        'assignee_username': 'js-liqiang123',
        'root_cause': (
            '财务应用服务器与数据库之间的连接超时。'
        ),
        'resolution': (
            '检查应用和数据库连接日志，修复异常网络策略，'
            '恢复数据库连接后重新提交测试凭证并核对数据。'
        ),
        'resolution_summary': (
            '修复数据库连接后凭证保存恢复正常'
        ),
        'days_ago': 11,
    },
    {
        'task': '办公网络频繁断开',
        'description': (
            '办公电脑访问内部系统时频繁断线，'
            '重新连接后短时间内再次中断。'
        ),
        'priority': 'high',
        'requester_username': 'xs-zhaolei123',
        'assignee_username': 'js-liqiang123',
        'root_cause': (
            '接入交换机端口存在大量丢包，'
            '网线水晶头接触不良。'
        ),
        'resolution': (
            '检查交换机端口错误计数，更换故障网线，'
            '重新协商端口速率并持续监测丢包情况。'
        ),
        'resolution_summary': (
            '更换故障网线并恢复稳定网络连接'
        ),
        'days_ago': 9,
    },
    {
        'task': '办公室打印机显示离线',
        'description': (
            '打印任务一直停留在队列中，'
            '多台电脑均显示打印机离线。'
        ),
        'priority': 'medium',
        'requester_username': 'rl-liming123',
        'assignee_username': 'sb-chenyu123',
        'root_cause': (
            '打印机网络地址发生变化，'
            '原打印端口仍指向旧地址。'
        ),
        'resolution': (
            '确认打印机当前网络地址，更新服务器打印端口，'
            '清理失败队列并分别执行测试页打印。'
        ),
        'resolution_summary': (
            '更新打印端口后恢复打印服务'
        ),
        'days_ago': 7,
    },
    {
        'task': '会议室投影设备无信号',
        'description': (
            '会议电脑连接投影设备后，'
            '投影画面持续显示无信号。'
        ),
        'priority': 'low',
        'requester_username': 'xs-zhaolei123',
        'assignee_username': 'xz-zhoumin123',
        'root_cause': (
            '投影设备输入源选择错误，'
            '同时转接器连接松动。'
        ),
        'resolution': (
            '重新连接转接器，切换至正确输入源，'
            '调整电脑显示输出并播放测试画面确认正常。'
        ),
        'resolution_summary': (
            '重新连接转接器并切换正确输入源'
        ),
        'days_ago': 4,
    },
]


SLA_HOURS = {
    'urgent': 4,
    'high': 8,
    'medium': 24,
    'low': 72,
}


def main():
    init_db()

    inserted = 0
    skipped = 0

    with connect_db() as conn:
        users = {
            row['username']: row
            for row in conn.execute(
                '''
                SELECT id,
                       username,
                       display_name
                FROM users
                '''
            ).fetchall()
        }

        for case in DEMO_CASES:
            existing = conn.execute(
                '''
                SELECT id
                FROM todos
                WHERE task = ?
                  AND resolution_summary = ?
                ''',
                (
                    case['task'],
                    case['resolution_summary'],
                ),
            ).fetchone()

            if existing is not None:
                skipped += 1
                continue

            requester = users.get(
                case['requester_username']
            )
            assignee = users.get(
                case['assignee_username']
            )

            if requester is None or assignee is None:
                raise RuntimeError(
                    '演示账号不存在，请先运行 init_db()'
                )

            created_at = (
                datetime.now()
                - timedelta(days=case['days_ago'])
            )
            resolved_at = (
                created_at
                + timedelta(hours=2)
            )
            sla_deadline = (
                created_at
                + timedelta(
                    hours=SLA_HOURS[
                        case['priority']
                    ]
                )
            )

            date_format = '%Y-%m-%d %H:%M:%S'

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
                    completed,
                    root_cause,
                    resolution,
                    resolution_summary,
                    resolved_by_id,
                    resolved_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, 'completed',
                    ?, ?, ?, ?, ?, 1,
                    ?, ?, ?, ?, ?, ?, ?
                )
                ''',
                (
                    case['task'],
                    case['description'],
                    case['priority'],
                    requester['display_name'],
                    assignee['display_name'],
                    requester['id'],
                    assignee['id'],
                    sla_deadline.strftime(
                        date_format
                    ),
                    case['root_cause'],
                    case['resolution'],
                    case['resolution_summary'],
                    assignee['id'],
                    resolved_at.strftime(
                        date_format
                    ),
                    created_at.strftime(
                        date_format
                    ),
                    resolved_at.strftime(
                        date_format
                    ),
                ),
            )

            inserted += 1

        conn.commit()

    print(
        f'演示历史案例初始化完成：'
        f'新增 {inserted} 条，'
        f'跳过 {skipped} 条。'
    )


if __name__ == '__main__':
    main()