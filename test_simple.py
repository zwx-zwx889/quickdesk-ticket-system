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
    
    def tearDown(self):
        """Clean up after tests."""
        # Change back to original directory
        os.chdir(self.original_cwd)
        
        # Clean up test directory
        import shutil
        shutil.rmtree(self.test_dir)

    def init_test_db(self):
        """Initialize the test database."""
        init_db()

    def test_index_page_loads(self):
        """Test that the index page loads successfully."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'QuickDesk', response.data)
        self.assertIn('暂无工单'.encode('utf-8'), response.data)
    
    def test_about_page_loads(self):
        """Test that the about page loads successfully."""
        response = self.client.get('/about')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'About FlaskTasks', response.data)
        self.assertIn(b'task management application', response.data)
    
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
        """Test ticket status workflow."""
        self.client.post(
            '/add',
            data={'task': '状态流转测试工单'},
            follow_redirects=True
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            row = conn.execute(
                'SELECT id FROM todos WHERE task = ?',
                ('状态流转测试工单',)
            ).fetchone()
            self.assertIsNotNone(row)
            ticket_id = row[0]

        # 待处理 -> 处理中
        response = self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'in_progress'},
            follow_redirects=True
        )
        self.assertIn('处理中'.encode('utf-8'), response.data)

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            status, completed = conn.execute(
                'SELECT status, completed FROM todos WHERE id = ?',
                (ticket_id,)
            ).fetchone()
            self.assertEqual(status, 'in_progress')
            self.assertEqual(completed, 0)

        # 处理中 -> 已完成
        self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'completed'},
            follow_redirects=True
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            status, completed = conn.execute(
                'SELECT status, completed FROM todos WHERE id = ?',
                (ticket_id,)
            ).fetchone()
            self.assertEqual(status, 'completed')
            self.assertEqual(completed, 1)

        # 已完成 -> 待处理
        self.client.post(
            f'/status/{ticket_id}',
            data={'status': 'pending'},
            follow_redirects=True
        )

        with closing(sqlite3.connect('quickdesk.db')) as conn:
            status, completed = conn.execute(
                'SELECT status, completed FROM todos WHERE id = ?',
                (ticket_id,)
            ).fetchone()
            self.assertEqual(status, 'pending')
            self.assertEqual(completed, 0)

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
        self.assertIn('工单更新成功！'.encode('utf-8'), response.data)
        
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
        
        # Test toggle with children
        response = self.client.get(f'/toggle_with_children/{parent_id}', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify both tasks are completed
        with closing(sqlite3.connect('quickdesk.db')) as conn:
            cursor = conn.execute('SELECT completed FROM todos WHERE id IN (?, ?)', 
                                (parent_id, parent_id + 1))
            results = cursor.fetchall()
            for result in results:
                self.assertTrue(result[0])
    
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
            
            expected_columns = ['id', 'task', 'completed', 'parent_id', 'created_at', 'updated_at']
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