"""
Simple, working tests for FlaskTasks application.

These tests use a direct approach and work with the actual application.
"""

import unittest
import tempfile
import os
import sqlite3
from app import app, init_db
from contextlib import closing
from quickdesk.recommendation import recommend_assignees
from unittest.mock import patch
from quickdesk.case_retrieval import (
    find_similar_resolved_tickets,
)


class FlaskTasksTestCase(unittest.TestCase):
    """Test Flask application functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Create a temporary directory for the test database
        self.test_dir = tempfile.mkdtemp()
        self.original_db_path = 'todos.db'
        self.test_db_path = os.path.join(self.test_dir, 'test_todos.db')
        
        # Configure Flask for testing
        app.config['TESTING'] = True
        self.client = app.test_client()
        
        # Change working directory temporarily
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        
        # Initialize test database
        self.init_test_db()
        self.client.post(
            '/login',
            data={
                'username': 'yy-admin123',
                'password': 'Qd@yy-admin123',
            },
            follow_redirects=True,
        )
    
    def tearDown(self):
        """Clean up after tests."""
        # Change back to original directory
        os.chdir(self.original_cwd)
        
        # Clean up test directory
        import shutil
        shutil.rmtree(self.test_dir)

    def test_login_page_loads(self):
        """Test that the login page loads."""
        self.client.post('/logout')

        response = self.client.get('/login')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '用户登录'.encode('utf-8'),
            response.data,
        )

    def test_index_requires_login(self):
        """Test that anonymous users cannot access tickets."""
        self.client.post('/logout')

        response = self.client.get(
            '/',
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            '/login',
            response.headers['Location'],
        )

    def test_invalid_login_is_rejected(self):
        """Test that an invalid password is rejected."""
        self.client.post('/logout')

        response = self.client.post(
            '/login',
            data={
                'username': 'yy-admin123',
                'password': 'wrong-password',
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '用户名或密码错误！'.encode('utf-8'),
            response.data,
        )

    def init_test_db(self):
        """Initialize the test database."""
        init_db()

    def _login_as(self, username, password):
        """Log in using the requested demonstration account."""
        self.client.post('/logout')

        return self.client.post(
            '/login',
            data={
                'username': username,
                'password': password,
            },
            follow_redirects=True,
        )

    def _create_assigned_transfer_ticket(self, task):
        """Create a ticket and assign it to Zhang Wei."""
        self.client.post(
            '/add',
            data={
                'task': task,
                'description': '用于测试工单转交审批流程',
                'priority': 'high',
                'requester': '王芳',
            },
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            ticket_id = conn.execute(
                'SELECT id FROM todos WHERE task = ?',
                (task,),
            ).fetchone()[0]

            zhangwei_id = conn.execute(
                '''
                SELECT id
                FROM users
                WHERE username = 'js-zhangwei123'
                '''
            ).fetchone()[0]

            liqiang_id = conn.execute(
                '''
                SELECT id
                FROM users
                WHERE username = 'js-liqiang123'
                '''
            ).fetchone()[0]

        self.client.post(
            f'/assign/{ticket_id}',
            data={
                'assignee_id': str(zhangwei_id),
            },
            follow_redirects=True,
        )

        return ticket_id, zhangwei_id, liqiang_id

    def test_index_page_loads(self):
        """Test that the index page loads successfully."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'QuickDesk', response.data)
        self.assertIn('暂无工单'.encode('utf-8'), response.data)

    def test_about_page(self):
        """Test QuickDesk about page loads."""
        response = self.client.get('/about')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '关于 QuickDesk'.encode('utf-8'),
            response.data
        )
        self.assertIn(
            '核心功能'.encode('utf-8'),
            response.data
        )
        self.assertIn(
            'SLA 规则'.encode('utf-8'),
            response.data
        )
        self.assertIn(
            '项目说明'.encode('utf-8'),
            response.data
        )
    
    def test_add_task_success(self):
        """Test adding a task successfully."""
        # Add a task
        response = self.client.post('/add', data={'task': 'Test Task'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Test Task', response.data)
        self.assertIn('工单创建成功！'.encode('utf-8'), response.data)

    def test_ticket_metadata_display(self):
        """Test that ticket metadata is saved and displayed."""
        response = self.client.post(
            '/add',
            data={
                'task': '财务系统无法登录',
                'description': '用户输入正确账号密码后仍提示登录失败',
                'priority': 'high',
                'requester': '王芳',
                'assignee': '技术支持'
            },
            follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('财务系统无法登录'.encode('utf-8'), response.data)
        self.assertIn('用户输入正确账号密码后仍提示登录失败'.encode('utf-8'), response.data)
        self.assertIn('高 · 8小时'.encode('utf-8'), response.data)
        self.assertIn('提交人：王芳'.encode('utf-8'), response.data)
        self.assertIn('处理人：技术支持'.encode('utf-8'), response.data)
        self.assertIn('SLA截止：'.encode('utf-8'), response.data)

    def test_ticket_status_workflow(self):
        """Test status flow with required resolution record."""
        self.client.post(
            '/add',
            data={'task': '状态流转测试工单'},
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            ticket_id = conn.execute(
                '''
                SELECT id
                FROM todos
                WHERE task = ?
                ''',
                ('状态流转测试工单',),
            ).fetchone()[0]

            assignee = conn.execute(
                '''
                SELECT id, display_name
                FROM users
                WHERE username = ?
                ''',
                ('js-zhangwei123',),
            ).fetchone()

            self.assertIsNotNone(assignee)

            conn.execute(
                '''
                UPDATE todos
                SET assignee_id = ?,
                    assignee = ?
                WHERE id = ?
                ''',
                (
                    assignee[0],
                    assignee[1],
                    ticket_id,
                ),
            )
            conn.commit()

        self.client.post('/logout')

        login_response = self.client.post(
            '/login',
            data={
                'username': 'js-zhangwei123',
                'password': 'Qd@js-zhangwei123',
            },
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)

        # 待处理 -> 处理中
        response = self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'in_progress'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            status, completed = conn.execute(
                '''
                SELECT status, completed
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()

            self.assertEqual(status, 'in_progress')
            self.assertEqual(completed, 0)

        # 不能通过状态接口绕过解决记录
        response = self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'completed'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '填写解决记录'.encode('utf-8'),
            response.data,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            status, completed = conn.execute(
                '''
                SELECT status, completed
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()

            self.assertEqual(status, 'in_progress')
            self.assertEqual(completed, 0)

        # 填写解决记录后完成工单
        response = self.client.post(
            f'/resolve/{ticket_id}',
            data={
                'root_cause': '认证服务连接超时',
                'resolution': (
                    '重启认证服务并检查连接状态，'
                    '确认财务系统恢复正常登录。'
                ),
                'resolution_summary': (
                    '恢复认证服务连接后登录正常'
                ),
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '解决记录已保存'.encode('utf-8'),
            response.data,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            result = conn.execute(
                '''
                SELECT status,
                       completed,
                       root_cause,
                       resolution,
                       resolution_summary,
                       resolved_by_id,
                       resolved_at
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()

            self.assertEqual(result[0], 'completed')
            self.assertEqual(result[1], 1)
            self.assertEqual(
                result[2],
                '认证服务连接超时',
            )
            self.assertIn(
                '重启认证服务',
                result[3],
            )
            self.assertEqual(
                result[4],
                '恢复认证服务连接后登录正常',
            )
            self.assertEqual(
                result[5],
                assignee[0],
            )
            self.assertIsNotNone(result[6])

        # 已完成 -> 待处理，历史解决记录仍然保留
        self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'pending'},
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            result = conn.execute(
                '''
                SELECT status,
                       completed,
                       root_cause,
                       resolution
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()

            self.assertEqual(result[0], 'pending')
            self.assertEqual(result[1], 0)
            self.assertEqual(
                result[2],
                '认证服务连接超时',
            )
            self.assertIn(
                '重启认证服务',
                result[3],
            )

    def test_sla_status_display(self):
        """Test normal and overdue SLA display."""
        response = self.client.post(
            '/add',
            data={
                'task': 'SLA测试工单',
                'priority': 'urgent'
            },
            follow_redirects=True
        )

        # 新建工单的截止时间在未来，应显示 SLA 正常
        self.assertIn('SLA正常'.encode('utf-8'), response.data)

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            row = conn.execute(
                'SELECT id FROM todos WHERE task = ?',
                ('SLA测试工单',)
            ).fetchone()
            self.assertIsNotNone(row)
            ticket_id = row[0]

            # 将截止时间设置到过去，模拟超时
            conn.execute(
                '''
                UPDATE todos
                SET sla_deadline = ?
                WHERE id = ?
                ''',
                ('2000-01-01 00:00:00', ticket_id)
            )
            conn.commit()

        response = self.client.get('/')

        # 未完成且超过截止时间，应显示 SLA 已超时
        self.assertIn(
            'SLA已超时'.encode('utf-8'),
            response.data
        )

    def test_ticket_search_and_filter(self):
        """Test ticket keyword search and status filter."""
        self.client.post(
            '/add',
            data={
                'task': '财务系统无法登录',
                'description': '账号登录失败',
                'requester': '王芳',
                'assignee': '技术支持'
            },
            follow_redirects=True
        )

        self.client.post(
            '/add',
            data={
                'task': '打印机无法使用',
                'description': '办公室打印机没有响应',
                'requester': '李明',
                'assignee': '设备支持'
            },
            follow_redirects=True
        )

        # 按提交人搜索
        response = self.client.get(
            '/?q=王芳'
        )
        self.assertIn(
            '财务系统无法登录'.encode('utf-8'),
            response.data
        )
        self.assertNotIn(
            '打印机无法使用'.encode('utf-8'),
            response.data
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            row = conn.execute(
                'SELECT id FROM todos WHERE task = ?',
                ('打印机无法使用',)
            ).fetchone()
            ticket_id = row[0]

        # 将第二张工单改为处理中
        self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'in_progress'},
            follow_redirects=True
        )

        # 按处理中状态筛选
        response = self.client.get(
            '/?status=in_progress'
        )
        self.assertIn(
            '打印机无法使用'.encode('utf-8'),
            response.data
        )
        self.assertNotIn(
            '财务系统无法登录'.encode('utf-8'),
            response.data
        )

    def test_full_ticket_edit(self):
        """Test complete ticket information editing."""
        self.client.post(
            '/add',
            data={
                'task': '原始工单标题',
                'description': '原始问题描述',
                'priority': 'medium',
                'requester': '原提交人',
                'assignee': '原处理人'
            },
            follow_redirects=True
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            row = conn.execute(
                'SELECT id FROM todos WHERE task = ?',
                ('原始工单标题',)
            ).fetchone()
            self.assertIsNotNone(row)
            ticket_id = row[0]

        # 编辑页面能够加载并回填原数据
        response = self.client.get(
            f'/edit/{ticket_id}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '编辑工单'.encode('utf-8'),
            response.data
        )
        self.assertIn(
            '原始问题描述'.encode('utf-8'),
            response.data
        )

        # 提交完整修改
        response = self.client.post(
            f'/edit/{ticket_id}',
            data={
                'task': '更新后的工单标题',
                'description': '更新后的问题描述',
                'priority': 'urgent',
                'requester': '王芳',
                'assignee': '张伟'
            },
            follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '工单信息更新成功！'.encode('utf-8'),
            response.data
        )
        self.assertIn(
            '更新后的工单标题'.encode('utf-8'),
            response.data
        )
        self.assertIn(
            '紧急 · 4小时'.encode('utf-8'),
            response.data
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            row = conn.execute(
                '''
                SELECT task,
                       description,
                       priority,
                       requester,
                       assignee,
                       sla_deadline
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,)
            ).fetchone()

        self.assertEqual(row[0], '更新后的工单标题')
        self.assertEqual(row[1], '更新后的问题描述')
        self.assertEqual(row[2], 'urgent')
        self.assertEqual(row[3], '王芳')
        self.assertEqual(row[4], '张伟')
        self.assertIsNotNone(row[5])

    def test_add_empty_task_fails(self):
        """Test that adding empty task shows error."""
        response = self.client.post('/add', data={'task': ''}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('请输入工单标题！'.encode('utf-8'), response.data)
    
    def test_task_operations_workflow(self):
        """Test complete task workflow: add, edit, toggle, delete."""
        # Add a task
        response = self.client.post('/add', data={'task': 'Workflow Task'}, follow_redirects=True)
        self.assertIn(b'Workflow Task', response.data)
        
        # Get the task ID from database
        with closing(sqlite3.connect('quickdesk.db')) as conn:
            cursor = conn.execute('SELECT id FROM todos WHERE task = ?', ('Workflow Task',))
            task_row = cursor.fetchone()
            self.assertIsNotNone(task_row)
            task_id = task_row[0]
        
        # Edit the task
        response = self.client.post(f'/edit/{task_id}', 
                                  data={'task': 'Updated Workflow Task'}, 
                                  follow_redirects=True)
        self.assertIn(b'Updated Workflow Task', response.data)
        self.assertIn(
            '工单信息更新成功！'.encode('utf-8'),
            response.data
        )
        
        # Toggle task completion
        response = self.client.get(f'/toggle/{task_id}', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify task is completed in database
        with closing(sqlite3.connect('quickdesk.db')) as conn:
            cursor = conn.execute('SELECT completed FROM todos WHERE id = ?', (task_id,))
            completed_row = cursor.fetchone()
            self.assertIsNotNone(completed_row)
            self.assertTrue(completed_row[0])
        
        # Delete the task
        response = self.client.get(f'/delete/{task_id}', follow_redirects=True)
        self.assertNotIn(b'Updated Workflow Task', response.data)
        self.assertIn('工单已删除！'.encode('utf-8'), response.data)
    
    def test_hierarchical_tasks(self):
        """Test parent-child task relationships."""
        # Add parent task
        response = self.client.post('/add', data={'task': 'Parent Task'}, follow_redirects=True)
        self.assertIn(b'Parent Task', response.data)
        
        # Get parent task ID
        with closing(sqlite3.connect('quickdesk.db')) as conn:
            cursor = conn.execute('SELECT id FROM todos WHERE task = ?', ('Parent Task',))
            parent_id = cursor.fetchone()[0]
        
        # Add subtask
        response = self.client.post('/add', 
                                  data={'task': 'Child Task', 'parent_id': parent_id}, 
                                  follow_redirects=True)
        self.assertIn(b'Child Task', response.data)
        self.assertIn(b'Parent Task', response.data)

        # 父子工单不能绕过解决记录批量完成
        response = self.client.get(
            f'/toggle_with_children/{parent_id}',
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '批量完成已停用'.encode('utf-8'),
            response.data,
        )

        # 验证父子工单均未被直接完成
        with closing(sqlite3.connect('quickdesk.db')) as conn:
            results = conn.execute(
                '''
                SELECT completed
                FROM todos
                WHERE id IN (?, ?)
                ''',
                (
                    parent_id,
                    parent_id + 1,
                ),
            ).fetchall()

            for result in results:
                self.assertEqual(result[0], 0)
    
    def test_database_structure(self):
        """Test that database has correct structure."""
        with closing(sqlite3.connect('quickdesk.db')) as conn:
            # Check table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='todos'"
            )
            self.assertIsNotNone(cursor.fetchone())
            
            # Check columns
            cursor = conn.execute("PRAGMA table_info(todos)")
            columns = cursor.fetchall()
            column_names = [col[1] for col in columns]

            expected_columns = [
                'id',
                'task',
                'completed',
                'parent_id',
                'root_cause',
                'resolution',
                'resolution_summary',
                'resolved_by_id',
                'resolved_at',
                'created_at',
                'updated_at',
            ]
            for col in expected_columns:
                self.assertIn(col, column_names)
    
    def test_error_handling(self):
        """Test error handling for invalid operations."""
        # Test operations on non-existent task
        invalid_id = 99999
        
        # These should not crash the application
        response = self.client.get(f'/toggle/{invalid_id}', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        
        response = self.client.get(f'/delete/{invalid_id}', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        
        response = self.client.post(f'/edit/{invalid_id}', 
                                  data={'task': 'Updated'}, 
                                  follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def test_requester_only_sees_own_tickets(self):
        """Test that requesters only see tickets they submitted."""
        self.client.post(
            '/add',
            data={
                'task': '王芳提交的工单',
                'description': '提交人自己的工单',
                'priority': 'medium',
                'requester': '王芳',
                'assignee': '张伟',
            },
            follow_redirects=True,
        )

        self.client.post(
            '/add',
            data={
                'task': '其他用户提交的工单',
                'description': '不应向王芳显示',
                'priority': 'low',
                'requester': '李明',
                'assignee': '张伟',
            },
            follow_redirects=True,
        )

        self.client.post('/logout')
        self.client.post(
            '/login',
            data={
                'username': 'cw-wangfang123',
                'password': 'Qd@cw-wangfang123',
            },
            follow_redirects=True,
        )

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '王芳提交的工单'.encode('utf-8'),
            response.data,
        )
        self.assertNotIn(
            '其他用户提交的工单'.encode('utf-8'),
            response.data,
        )

    def test_assignee_only_sees_assigned_tickets(self):
        """Test that assignees only see tickets assigned to them."""
        self.client.post(
            '/add',
            data={
                'task': '分配给张伟的工单',
                'description': '张伟应当可以看到',
                'priority': 'high',
                'requester': '王芳',
                'assignee': '张伟',
            },
            follow_redirects=True,
        )

        self.client.post(
            '/add',
            data={
                'task': '分配给其他人员的工单',
                'description': '张伟不应看到',
                'priority': 'medium',
                'requester': '王芳',
                'assignee': '李强',
            },
            follow_redirects=True,
        )

        self.client.post('/logout')
        self.client.post(
            '/login',
            data={
                'username': 'js-zhangwei123',
                'password': 'Qd@js-zhangwei123',
            },
            follow_redirects=True,
        )

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '分配给张伟的工单'.encode('utf-8'),
            response.data,
        )
        self.assertNotIn(
            '分配给其他人员的工单'.encode('utf-8'),
            response.data,
        )

    def test_role_operation_permissions(self):
        """Test requester and assignee operation permissions."""
        self.client.post(
            '/add',
            data={
                'task': '角色权限测试工单',
                'description': '验证状态、编辑和删除权限',
                'priority': 'high',
                'requester': '王芳',
                'assignee': '张伟',
            },
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            ticket_id = conn.execute(
                'SELECT id FROM todos WHERE task = ?',
                ('角色权限测试工单',),
            ).fetchone()[0]

        # 提交人不能更新工单状态
        self.client.post('/logout')
        self.client.post(
            '/login',
            data={
                'username': 'cw-wangfang123',
                'password': 'Qd@cw-wangfang123',
            },
            follow_redirects=True,
        )

        self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'in_progress'},
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            status = conn.execute(
                'SELECT status FROM todos WHERE id = ?',
                (ticket_id,),
            ).fetchone()[0]

        self.assertEqual(status, 'pending')

        # 被分派的处理人可以更新状态
        self.client.post('/logout')
        self.client.post(
            '/login',
            data={
                'username': 'js-zhangwei123',
                'password': 'Qd@js-zhangwei123',
            },
            follow_redirects=True,
        )

        self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'in_progress'},
            follow_redirects=True,
        )

        # 处理人不能修改原始工单内容
        self.client.post(
            f'/edit/{ticket_id}',
            data={
                'task': '越权修改后的标题',
                'description': '不应保存',
                'priority': 'urgent',
                'requester': '王芳',
                'assignee': '张伟',
            },
            follow_redirects=True,
        )

        # 处理人不能删除工单
        self.client.get(
            f'/delete/{ticket_id}',
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            ticket = conn.execute(
                '''
                SELECT task, status
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()

        self.assertIsNotNone(ticket)
        self.assertEqual(ticket[0], '角色权限测试工单')
        self.assertEqual(ticket[1], 'in_progress')

    def test_new_account_must_change_temporary_password(self):
        """Test first-login password replacement for a new account."""
        self.client.post(
            '/users/create',
            data={
                'username': 'newemployee',
                'display_name': '新员工',
                'role': 'requester',
                'department': '测试部',
                'password': 'Temporary@123',
                'max_active_tickets': '5',
            },
            follow_redirects=True,
        )

        self.client.post('/logout')
        response = self.client.post(
            '/login',
            data={
                'username': 'newemployee',
                'password': 'Temporary@123',
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            '/change-password',
            response.headers['Location'],
        )

        response = self.client.post(
            '/change-password',
            data={
                'current_password': 'Temporary@123',
                'new_password': 'MySecure@456',
                'confirm_password': 'MySecure@456',
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('密码修改成功'.encode('utf-8'), response.data)

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            must_change = conn.execute(
                '''
                SELECT must_change_password
                FROM users
                WHERE username = 'newemployee'
                '''
            ).fetchone()[0]

        self.assertEqual(must_change, 0)

    def test_admin_can_reset_user_password(self):
        """Test that an admin can issue a one-time password."""
        with closing(sqlite3.connect('quickdesk.db')) as conn:
            user_id, old_hash = conn.execute(
                '''
                SELECT id, password_hash
                FROM users
                WHERE username = 'rl-liming123'
                '''
            ).fetchone()

        response = self.client.post(
            f'/users/{user_id}/reset-password',
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('一次性临时密码'.encode('utf-8'), response.data)

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            new_hash, must_change = conn.execute(
                '''
                SELECT password_hash, must_change_password
                FROM users
                WHERE id = ?
                ''',
                (user_id,),
            ).fetchone()

        self.assertNotEqual(new_hash, old_hash)
        self.assertEqual(must_change, 1)

    def test_user_management_requires_admin(self):
        """Test that non-admin users cannot manage accounts."""
        self.client.post('/logout')
        self.client.post(
            '/login',
            data={
                'username': 'cw-wangfang123',
                'password': 'Qd@cw-wangfang123',
            },
        )

        response = self.client.get(
            '/users/',
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 403)

    def test_assignee_can_request_ticket_transfer(self):
        """Test that an assignee can submit a transfer request."""
        ticket_id, zhangwei_id, _ = (
            self._create_assigned_transfer_ticket(
                '转交申请测试工单'
            )
        )

        self._login_as(
            'js-zhangwei123',
            'Qd@js-zhangwei123',
        )

        response = self.client.post(
            f'/transfer-request/{ticket_id}',
            data={
                'requested_department': '设备维护部',
                'reason': '该问题属于设备故障，需要设备维护部处理',
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            transfer = conn.execute(
                '''
                SELECT status,
                       requested_by_id,
                       from_assignee_id
                FROM transfer_requests
                WHERE todo_id = ?
                ''',
                (ticket_id,),
            ).fetchone()

        self.assertIsNotNone(transfer)
        self.assertEqual(transfer[0], 'pending')
        self.assertEqual(transfer[1], zhangwei_id)
        self.assertEqual(transfer[2], zhangwei_id)

    def test_admin_can_approve_ticket_transfer(self):
        """Test approving a request changes the ticket assignee."""
        ticket_id, _, liqiang_id = (
            self._create_assigned_transfer_ticket(
                '批准转交测试工单'
            )
        )

        self._login_as(
            'js-zhangwei123',
            'Qd@js-zhangwei123',
        )

        self.client.post(
            f'/transfer-request/{ticket_id}',
            data={
                'requested_department': '技术支持部',
                'reason': '当前工作量较大，申请转交其他处理人',
            },
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            transfer_id = conn.execute(
                '''
                SELECT id
                FROM transfer_requests
                WHERE todo_id = ?
                  AND status = 'pending'
                ''',
                (ticket_id,),
            ).fetchone()[0]

        self._login_as(
            'yy-admin123',
            'Qd@yy-admin123',
        )

        response = self.client.post(
            f'/transfers/{transfer_id}/approve',
            data={
                'assignee_id': str(liqiang_id),
                'decision_note': '批准转交给李强处理',
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            ticket = conn.execute(
                '''
                SELECT assignee_id, assignee
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()

            transfer_status = conn.execute(
                '''
                SELECT status
                FROM transfer_requests
                WHERE id = ?
                ''',
                (transfer_id,),
            ).fetchone()[0]

        self.assertEqual(ticket[0], liqiang_id)
        self.assertEqual(ticket[1], '李强')
        self.assertEqual(transfer_status, 'approved')

    def test_admin_can_reject_ticket_transfer(self):
        """Test rejecting a request keeps the current assignee."""
        ticket_id, zhangwei_id, _ = (
            self._create_assigned_transfer_ticket(
                '拒绝转交测试工单'
            )
        )

        self._login_as(
            'js-zhangwei123',
            'Qd@js-zhangwei123',
        )

        self.client.post(
            f'/transfer-request/{ticket_id}',
            data={
                'requested_department': '',
                'reason': '申请由管理员重新安排处理人',
            },
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            transfer_id = conn.execute(
                '''
                SELECT id
                FROM transfer_requests
                WHERE todo_id = ?
                  AND status = 'pending'
                ''',
                (ticket_id,),
            ).fetchone()[0]

        self._login_as(
            'yy-admin123',
            'Qd@yy-admin123',
        )

        response = self.client.post(
            f'/transfers/{transfer_id}/reject',
            data={
                'decision_note': '当前处理人仍具备处理能力',
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            ticket_assignee_id = conn.execute(
                '''
                SELECT assignee_id
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()[0]

            transfer_status = conn.execute(
                '''
                SELECT status
                FROM transfer_requests
                WHERE id = ?
                ''',
                (transfer_id,),
            ).fetchone()[0]

        self.assertEqual(ticket_assignee_id, zhangwei_id)
        self.assertEqual(transfer_status, 'rejected')

    def test_admin_can_view_and_filter_audit_logs(self):
            """Test that administrators can search audit logs."""
            self.client.post(
                '/add',
                data={
                    'task': '操作日志查询测试工单',
                    'description': '验证管理员日志查询和类型筛选',
                    'priority': 'medium',
                    'requester': '王芳',
                },
                follow_redirects=True,
            )

            response = self.client.get(
                '/audit/',
                query_string={
                    'q': '操作日志查询测试工单',
                    'action': 'created',
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertIn(
                '系统操作日志'.encode('utf-8'),
                response.data,
            )
            self.assertIn(
                '操作日志查询测试工单'.encode('utf-8'),
                response.data,
            )
            self.assertIn(
                '创建工单'.encode('utf-8'),
                response.data,
            )

    def test_audit_logs_require_admin(self):
            """Test that non-admin users cannot view audit logs."""
            self._login_as(
                'cw-wangfang123',
                'Qd@cw-wangfang123',
            )

            response = self.client.get(
                '/audit/',
                follow_redirects=False,
            )

            self.assertEqual(response.status_code, 403)

    def test_admin_can_view_dashboard(self):
        """Test that administrators can view dashboard statistics."""
        self.client.post(
            '/add',
            data={
                'task': '仪表盘统计测试工单',
                'description': '验证管理员数据看板',
                'priority': 'urgent',
                'requester': '王芳',
            },
            follow_redirects=True,
        )

        response = self.client.get('/dashboard/')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '工单数据仪表盘'.encode('utf-8'),
            response.data,
        )
        self.assertIn(
            '工单总数'.encode('utf-8'),
            response.data,
        )
        self.assertIn(
            'SLA达标率'.encode('utf-8'),
            response.data,
        )
        self.assertIn(
            '处理人工作负载'.encode('utf-8'),
            response.data,
        )


    def test_dashboard_requires_admin(self):
        """Test that non-admin users cannot view the dashboard."""
        self._login_as(
            'cw-wangfang123',
            'Qd@cw-wangfang123',
        )

        response = self.client.get(
            '/dashboard/',
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 403)

    def test_recommendation_prefers_matching_skills(self):
        """Test that matching skills improve assignment ranking."""
        self.client.post(
            '/add',
            data={
                'task': '账号登录权限认证异常',
                'description': (
                    '用户无法进入应用系统，'
                    '需要检查账号登录和权限认证'
                ),
                'priority': 'high',
                'requester': '王芳',
            },
            follow_redirects=True,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            ticket_id = conn.execute(
                '''
                SELECT id
                FROM todos
                WHERE task = ?
                ''',
                ('账号登录权限认证异常',),
            ).fetchone()[0]

        recommendations = recommend_assignees(
            ticket_id,
            limit=3,
        )

        self.assertTrue(recommendations)
        self.assertEqual(
            recommendations[0]['display_name'],
            '张伟',
        )
        self.assertGreater(
            recommendations[0]['score'],
            0,
        )
        self.assertTrue(
            any(
                '技能匹配' in reason
                for reason in recommendations[0]['reasons']
            )
        )


    def test_admin_sees_assignment_recommendations(self):
        """Test that recommendations appear for unassigned tickets."""
        self.client.post(
            '/add',
            data={
                'task': '应用系统无法登录',
                'description': '用户账号认证失败',
                'priority': 'medium',
                'requester': '王芳',
            },
            follow_redirects=True,
        )

        response = self.client.get(
            '/',
            query_string={
                'assignment': 'unassigned',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '智能推荐处理人'.encode('utf-8'),
            response.data,
        )
        self.assertIn(
            '推荐结果仅供参考'.encode('utf-8'),
            response.data,
        )
        self.assertIn(
            '张伟'.encode('utf-8'),
            response.data,
        )
    def _create_ai_assigned_ticket(self):
        """Create a ticket assigned to Zhang Wei."""
        with closing(sqlite3.connect('quickdesk.db')) as conn:
            requester = conn.execute(
                '''
                SELECT id, display_name
                FROM users
                WHERE username = ?
                ''',
                ('cw-wangfang123',),
            ).fetchone()

            assignee = conn.execute(
                '''
                SELECT id, display_name
                FROM users
                WHERE username = ?
                ''',
                ('js-zhangwei123',),
            ).fetchone()

            cursor = conn.execute(
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
                    completed
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ''',
                (
                    '财务系统登录异常',
                    '正确账号密码仍然无法登录财务系统',
                    'high',
                    'in_progress',
                    requester[1],
                    assignee[1],
                    requester[0],
                    assignee[0],
                ),
            )
            conn.commit()

            return cursor.lastrowid

    def _login_as_demo_assignee(
        self,
        username='js-zhangwei123',
    ):
        self.client.post('/logout')

        return self.client.post(
            '/login',
            data={
                'username': username,
                'password': f'Qd@{username}',
            },
            follow_redirects=True,
        )

    def _fake_resolution_result(self):
        return {
            'available': True,
            'status': 'success',
            'message': '测试处理方案已生成',
            'model': 'deepseek-flash',
            'plan': {
                'likely_causes': [
                    '认证服务连接异常',
                ],
                'clarification_questions': [
                    '是否有其他用户出现相同问题？',
                ],
                'diagnostic_steps': [
                    '检查账号锁定状态',
                    '检查认证服务日志',
                ],
                'recommended_solution': (
                    '确认故障原因后恢复认证服务连接。'
                ),
                'risks_and_rollback': [
                    '修改前备份原配置。',
                ],
                'transfer_advice': {
                    'needed': False,
                    'department': '',
                    'reason': '',
                },
                'referenced_case_ids': [],
                'completion_draft': (
                    '检查认证服务并恢复系统登录。'
                ),
                'confidence': '中',
            },
        }

    def test_similar_resolved_ticket_retrieval(self):
        """Test retrieval of similar resolved tickets."""
        ticket_id = self._create_ai_assigned_ticket()

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            assignee = conn.execute(
                '''
                SELECT id, display_name
                FROM users
                WHERE username = ?
                ''',
                ('js-zhangwei123',),
            ).fetchone()

            conn.execute(
                '''
                INSERT INTO todos (
                    task,
                    description,
                    priority,
                    status,
                    requester,
                    assignee,
                    assignee_id,
                    completed,
                    root_cause,
                    resolution,
                    resolution_summary,
                    resolved_by_id,
                    resolved_at
                )
                VALUES (
                    ?, ?, ?, 'completed',
                    ?, ?, ?, 1,
                    ?, ?, ?, ?,
                    CURRENT_TIMESTAMP
                )
                ''',
                (
                    '财务系统认证登录失败',
                    '财务人员无法通过认证服务登录',
                    'high',
                    '王芳',
                    assignee[1],
                    assignee[0],
                    '认证服务连接超时',
                    '恢复认证服务连接并验证登录',
                    '恢复认证服务后登录正常',
                    assignee[0],
                ),
            )
            conn.commit()

            conn.row_factory = sqlite3.Row
            todo = conn.execute(
                '''
                SELECT *
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()

        cases = find_similar_resolved_tickets(
            todo,
            limit=3,
        )

        self.assertTrue(cases)
        self.assertEqual(
            cases[0]['task'],
            '财务系统认证登录失败',
        )
        self.assertGreater(
            cases[0]['similarity_score'],
            0,
        )
        self.assertIn(
            '恢复认证服务',
            cases[0]['resolution'],
        )

    def test_only_current_assignee_can_use_ai_copilot(self):
        """Test resolution copilot role and ownership checks."""
        ticket_id = self._create_ai_assigned_ticket()

        self._login_as_demo_assignee(
            'js-liqiang123'
        )

        with patch(
            'quickdesk.resolution_routes.'
            'generate_resolution_plan'
        ) as mocked_generate:
            response = self.client.post(
                f'/assistant/resolution/{ticket_id}',
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 403)
        mocked_generate.assert_not_called()

    def test_resolution_plan_is_cached(self):
        """Test that cached plans avoid repeated API calls."""
        ticket_id = self._create_ai_assigned_ticket()
        self._login_as_demo_assignee()

        with patch(
            'quickdesk.resolution_routes.'
            'generate_resolution_plan',
            return_value=self._fake_resolution_result(),
        ) as first_call:
            response = self.client.post(
                f'/assistant/resolution/{ticket_id}',
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        first_call.assert_called_once()

        with patch(
            'quickdesk.resolution_routes.'
            'generate_resolution_plan',
            side_effect=AssertionError(
                '不应再次调用DeepSeek'
            ),
        ) as second_call:
            response = self.client.post(
                f'/assistant/resolution/{ticket_id}',
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        second_call.assert_not_called()
        self.assertIn(
            '未产生新的 DeepSeek 调用'.encode(
                'utf-8'
            ),
            response.data,
        )
        self.assertIn(
            'DeepSeek 故障处理助手'.encode(
                'utf-8'
            ),
            response.data,
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            cache_count = conn.execute(
                '''
                SELECT COUNT(*)
                FROM ai_resolution_plans
                WHERE todo_id = ?
                ''',
                (ticket_id,),
            ).fetchone()[0]

        self.assertEqual(cache_count, 1)

    def _create_pending_ai_transfer(self):
        """Create a pending transfer and log in as admin."""
        ticket_id, zhangwei_id, liqiang_id = (
            self._create_assigned_transfer_ticket(
                'AI转交审核测试工单'
            )
        )

        self._login_as(
            'js-zhangwei123',
            'Qd@js-zhangwei123',
        )

        self.client.post(
            f'/transfer-request/{ticket_id}',
            data={
                'requested_department': '技术支持部',
                'reason': (
                    '当前工单需要网络故障技能，'
                    '申请转交给更合适的处理人'
                ),
            },
            follow_redirects=True,
        )

        with closing(
            sqlite3.connect('quickdesk.db')
        ) as conn:
            transfer_id = conn.execute(
                '''
                SELECT id
                FROM transfer_requests
                WHERE todo_id = ?
                  AND status = 'pending'
                ''',
                (ticket_id,),
            ).fetchone()[0]

        self._login_as(
            'yy-admin123',
            'Qd@yy-admin123',
        )

        return (
            ticket_id,
            transfer_id,
            zhangwei_id,
            liqiang_id,
        )

    def _fake_transfer_review_result(
        self,
        candidate_id,
    ):
        """Return a deterministic DeepSeek transfer review."""
        return {
            'available': True,
            'status': 'success',
            'message': '测试转交评估已生成',
            'model': 'deepseek-flash',
            'review': {
                'decision': 'approve',
                'confidence': '中',
                'reason_quality': '充分',
                'skill_gap': True,
                'sla_risk': '中',
                'summary': (
                    '当前处理人技能与工单问题存在差异，'
                    '建议管理员核实后批准转交。'
                ),
                'evidence': [
                    '转交理由说明了所需技能',
                    '候选处理人具备相关技能',
                ],
                'risks': [
                    '转交后需要确认新处理人及时接单',
                ],
                'questions': [
                    '当前处理人是否已经完成部分排查？',
                ],
                'recommended_assignee_ids': [
                    candidate_id,
                ],
            },
        }

    def test_admin_can_generate_ai_transfer_review(self):
        """Test admin AI review is cached without auto approval."""
        (
            ticket_id,
            transfer_id,
            zhangwei_id,
            liqiang_id,
        ) = self._create_pending_ai_transfer()

        with patch(
            'quickdesk.transfer_ai_routes.'
            'generate_transfer_review',
            return_value=(
                self._fake_transfer_review_result(
                    liqiang_id
                )
            ),
        ) as mocked_generate:
            response = self.client.post(
                (
                    f'/transfers/{transfer_id}'
                    '/ai-review'
                ),
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        mocked_generate.assert_called_once()

        with closing(
            sqlite3.connect('quickdesk.db')
        ) as conn:
            cache_count = conn.execute(
                '''
                SELECT COUNT(*)
                FROM ai_transfer_reviews
                WHERE transfer_request_id = ?
                ''',
                (transfer_id,),
            ).fetchone()[0]

            transfer_status = conn.execute(
                '''
                SELECT status
                FROM transfer_requests
                WHERE id = ?
                ''',
                (transfer_id,),
            ).fetchone()[0]

            ticket_assignee = conn.execute(
                '''
                SELECT assignee_id
                FROM todos
                WHERE id = ?
                ''',
                (ticket_id,),
            ).fetchone()[0]

        self.assertEqual(cache_count, 1)
        self.assertEqual(
            transfer_status,
            'pending',
        )
        self.assertEqual(
            ticket_assignee,
            zhangwei_id,
        )

    def test_ai_transfer_review_uses_cache(self):
        """Test repeated review avoids another API call."""
        (
            _,
            transfer_id,
            _,
            liqiang_id,
        ) = self._create_pending_ai_transfer()

        with patch(
            'quickdesk.transfer_ai_routes.'
            'generate_transfer_review',
            return_value=(
                self._fake_transfer_review_result(
                    liqiang_id
                )
            ),
        ) as first_call:
            first_response = self.client.post(
                (
                    f'/transfers/{transfer_id}'
                    '/ai-review'
                ),
                follow_redirects=False,
            )

        self.assertEqual(
            first_response.status_code,
            302,
        )
        first_call.assert_called_once()

        with patch(
            'quickdesk.transfer_ai_routes.'
            'generate_transfer_review',
            side_effect=AssertionError(
                '缓存存在时不应再次调用DeepSeek'
            ),
        ) as second_call:
            second_response = self.client.post(
                (
                    f'/transfers/{transfer_id}'
                    '/ai-review'
                ),
                follow_redirects=True,
            )

        self.assertEqual(
            second_response.status_code,
            200,
        )
        second_call.assert_not_called()

        self.assertIn(
            'DeepSeek 转交审核建议'.encode(
                'utf-8'
            ),
            second_response.data,
        )

        self.assertIn(
            '建议批准'.encode('utf-8'),
            second_response.data,
        )

    def test_assignee_cannot_generate_transfer_review(self):
        """Test transfer AI review requires admin role."""
        (
            _,
            transfer_id,
            _,
            liqiang_id,
        ) = self._create_pending_ai_transfer()

        self._login_as(
            'js-zhangwei123',
            'Qd@js-zhangwei123',
        )

        with patch(
            'quickdesk.transfer_ai_routes.'
            'generate_transfer_review',
            return_value=(
                self._fake_transfer_review_result(
                    liqiang_id
                )
            ),
        ) as mocked_generate:
            response = self.client.post(
                (
                    f'/transfers/{transfer_id}'
                    '/ai-review'
                ),
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 403)
        mocked_generate.assert_not_called()

    def _fake_dashboard_insight_result(self):
        """Return a deterministic dashboard AI result."""
        return {
            'available': True,
            'status': 'success',
            'message': '测试运营洞察已生成',
            'model': 'deepseek-flash',
            'insight': {
                'health_level': '关注',
                'confidence': '中',
                'executive_summary': (
                    '测试运营摘要：当前存在少量待处理工单。'
                ),
                'key_findings': [
                    '待处理工单需要及时跟进',
                ],
                'sla_risks': [
                    '未分派工单可能增加响应等待时间',
                ],
                'workload_findings': [
                    '当前处理人负载总体可控',
                ],
                'alerts': [
                    {
                        'title': '存在未分派工单',
                        'severity': '中',
                        'reason': '当前存在待管理员分派的工单',
                    },
                ],
                'recommendations': [
                    {
                        'title': '及时完成工单分派',
                        'priority': '高',
                        'action': '管理员核实并分派未分派工单',
                        'expected_benefit': '缩短首次响应时间',
                    },
                ],
            },
        }

    def test_admin_can_generate_dashboard_ai_insight(self):
        """Test that an admin can generate an insight."""
        with patch(
            'quickdesk.dashboard_ai_routes.'
            'generate_dashboard_insight',
            return_value=(
                self._fake_dashboard_insight_result()
            ),
        ) as mocked_generate:
            response = self.client.post(
                '/dashboard/ai-insight',
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        mocked_generate.assert_called_once()

        with closing(
            sqlite3.connect('quickdesk.db')
        ) as conn:
            cache_count = conn.execute(
                '''
                SELECT COUNT(*)
                FROM ai_dashboard_insights
                '''
            ).fetchone()[0]

        self.assertEqual(cache_count, 1)

    def test_dashboard_ai_insight_uses_cache(self):
        """Test unchanged data avoids another API call."""
        with patch(
            'quickdesk.dashboard_ai_routes.'
            'generate_dashboard_insight',
            return_value=(
                self._fake_dashboard_insight_result()
            ),
        ) as first_call:
            first_response = self.client.post(
                '/dashboard/ai-insight',
                follow_redirects=False,
            )

        self.assertEqual(
            first_response.status_code,
            302,
        )
        first_call.assert_called_once()

        with patch(
            'quickdesk.dashboard_ai_routes.'
            'generate_dashboard_insight',
            side_effect=AssertionError(
                '缓存存在时不应再次调用DeepSeek'
            ),
        ) as second_call:
            second_response = self.client.post(
                '/dashboard/ai-insight',
                follow_redirects=True,
            )

        self.assertEqual(
            second_response.status_code,
            200,
        )
        second_call.assert_not_called()

        self.assertIn(
            'DeepSeek 运营洞察'.encode('utf-8'),
            second_response.data,
        )
        self.assertIn(
            '测试运营摘要'.encode('utf-8'),
            second_response.data,
        )

    def test_dashboard_ai_insight_requires_admin(self):
        """Test that assignees cannot generate insights."""
        self._login_as(
            'js-zhangwei123',
            'Qd@js-zhangwei123',
        )

        with patch(
            'quickdesk.dashboard_ai_routes.'
            'generate_dashboard_insight',
            return_value=(
                self._fake_dashboard_insight_result()
            ),
        ) as mocked_generate:
            response = self.client.post(
                '/dashboard/ai-insight',
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 403)
        mocked_generate.assert_not_called()


class BasicUITestCase(unittest.TestCase):
    """Test basic UI components."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()
        app.config['TESTING'] = True
        self.client = app.test_client()

        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)

        # Initialize test database using the application schema
        init_db()

        # Log in before testing protected UI pages
        self.client.post(
            '/login',
            data={
                'username': 'yy-admin123',
                'password': 'Qd@yy-admin123',
            },
            follow_redirects=True,
        )
    
    def tearDown(self):
        """Clean up after tests."""
        os.chdir(self.original_cwd)
        import shutil
        shutil.rmtree(self.test_dir)

    def test_navbar_elements(self):
        """Test that navbar contains correct elements."""
        response = self.client.get('/')
        self.assertIn(b'QuickDesk', response.data)
        self.assertIn('工单管理'.encode('utf-8'), response.data)
        self.assertIn('关于系统'.encode('utf-8'), response.data)

    def test_form_elements(self):
        """Test that form elements are present."""
        response = self.client.get('/')
        self.assertIn(b'name="task"', response.data)
        self.assertIn(b'name="description"', response.data)
        self.assertIn(b'name="priority"', response.data)
        self.assertIn(b'name="requester"', response.data)
        self.assertIn(b'name="assignee"', response.data)
        self.assertIn('创建工单'.encode('utf-8'), response.data)
        self.assertIn(
            'placeholder="请输入工单标题"'.encode('utf-8'),
            response.data
        )

    def test_footer_present(self):
        """Test that footer is present."""
        response = self.client.get('/')
        self.assertIn(b'2026 QuickDesk', response.data)

    def test_responsive_elements(self):
        """Test that responsive CSS classes are present."""
        response = self.client.get('/')
        # Check for Bootstrap classes
        self.assertIn(b'container', response.data)
        self.assertIn(b'col-lg-8', response.data)
        self.assertIn(b'btn btn-primary', response.data)


if __name__ == '__main__':
    # Run tests with detailed output
    unittest.main(verbosity=2)
