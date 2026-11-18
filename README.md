# QuickDesk 企业工单与 SLA 协作管理系统

QuickDesk 是一个基于 Python Flask 和 SQLite 开发的小型企业工单管理系统。系统面向企业内部技术支持、行政支持和设备维护等场景，用于记录问题、分派处理人、跟踪处理状态，并通过 SLA 截止时间提示工单是否超时。

本项目由开源项目 FlaskTasks 改造而来，在原任务管理功能基础上增加了企业工单字段、优先级、SLA、状态流转、搜索筛选和完整编辑等功能。

## 主要功能

- 创建、编辑和删除工单
- 记录工单标题与问题描述
- 记录提交人和处理人
- 支持低、中、高、紧急四种优先级
- 根据优先级自动计算 SLA 截止时间
- 自动标记“SLA正常”或“SLA已超时”
- 支持待处理、处理中、已完成三阶段状态流转
- 支持已完成工单重新打开
- 支持关键词搜索
- 支持按照工单状态筛选
- 支持父工单和子任务
- 支持响应式网页布局
- 提供自动化测试

## SLA 规则

| 优先级 | 处理时限 |
|---|---:|
| 紧急 | 4 小时 |
| 高 | 8 小时 |
| 中 | 24 小时 |
| 低 | 72 小时 |

创建工单时，系统根据当前时间和优先级自动生成 SLA 截止时间。未完成工单超过截止时间后，页面会显示红色“SLA已超时”标记。

## 技术栈

- Python 3.11
- Flask 3.0
- SQLite
- Jinja2
- Bootstrap 5
- Bootstrap Icons
- Pytest
- Git 与 GitHub

## 项目结构

```text
flasktasks-main/
├── app.py
├── requirements.txt
├── README.md
├── test_simple.py
├── templates/
│   ├── index.html
│   ├── edit.html
│   └── about.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js
└── docs/
    ├── 01-planning/
    └── baseline/
```

## 本地运行

### 1. 进入项目目录

```powershell
cd "D:\study\软件工程\大作业-基于 Flask 的小型企业工单与 SLA 协作管理系统\flasktasks-main"
```

### 2. 创建虚拟环境

首次运行时执行：

```powershell
py -m venv .venv
```

### 3. 安装依赖

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4. 启动系统

```powershell
.\.venv\Scripts\python.exe app.py
```

### 5. 访问系统

在浏览器中打开：

```text
http://127.0.0.1:5000
```

系统首次启动时会自动创建 `quickdesk.db` 数据库。

## 自动化测试

运行 QuickDesk 核心测试：

```powershell
.\.venv\Scripts\python.exe -m pytest test_simple.py -q --tb=line
```

当前核心测试共 17 项，覆盖：

- 工单创建
- 空标题校验
- 工单编辑
- 工单删除
- 工单状态流转
- 父子任务操作
- 工单元数据展示
- SLA 正常与超时判断
- 关键词搜索
- 状态筛选
- 页面基本元素

## 数据库主要字段

| 字段 | 说明 |
|---|---|
| id | 工单编号 |
| task | 工单标题 |
| description | 问题描述 |
| priority | 优先级 |
| status | 工单状态 |
| requester | 提交人 |
| assignee | 处理人 |
| sla_deadline | SLA 截止时间 |
| completed | 是否完成 |
| parent_id | 父任务编号 |
| created_at | 创建时间 |
| updated_at | 更新时间 |

## 主要路由

| 请求方式 | 地址 | 功能 |
|---|---|---|
| GET | `/` | 工单列表、搜索与筛选 |
| POST | `/add` | 创建工单 |
| GET/POST | `/edit/<id>` | 查看并编辑工单 |
| POST | `/status/<id>` | 更新工单状态 |
| GET | `/toggle/<id>` | 切换完成状态 |
| GET | `/delete/<id>` | 删除工单 |
| GET | `/about` | 关于系统 |

## 项目改造说明

原 FlaskTasks 项目主要用于普通待办任务管理。本项目将其改造成企业工单系统，主要改进包括：

1. 将普通任务模型扩展为企业工单模型。
2. 增加问题描述、提交人、处理人和优先级字段。
3. 增加三阶段工单状态流转。
4. 增加 SLA 自动计算及超时判断。
5. 增加关键词搜索和状态筛选。
6. 增加完整工单编辑页面。
7. 将页面和提示信息中文化。
8. 增加针对新增业务功能的自动化测试。
9. 使用 Git 分阶段记录项目改造过程。

## 开源项目来源

本项目基于以下开源项目进行二次开发：

- FlaskTasks
- 原项目地址：https://github.com/triangletodd/flasktasks

QuickDesk 的企业工单业务、SLA 功能、状态流转、搜索筛选、中文界面和相关测试均为本课程项目的改造内容。