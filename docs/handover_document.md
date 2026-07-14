# SolarAgent 项目交接文档

> **交接日期**: 2026-07-14
> **项目路径**: `D:\AAA_myProjects\howso\myAgent\solar_agent`
> **虚拟环境**: `D:\AAA_myProjects\howso\myAgent\solar_agent\.venv`
> **文档作者**: AI 开发助手

---

## 一、项目概述

### 1.1 项目定位

SolarAgent 是一个**光伏发电分析 AI Agent 系统**，用户通过自然语言对话即可完成站点查询、发电量预测、数据导出、知识库检索等操作。

### 1.2 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI 0.128+ | 异步 ASGI 框架，类似 Spring WebFlux |
| ASGI 服务器 | uvicorn | 类似 Spring 内嵌 Tomcat |
| AI 框架 | LangChain | Agent 编排框架 |
| LLM | 阿里云百炼 qwen-plus | 通过 OpenAI 兼容接口调用 |
| 数据库 | MySQL 8.0 + SQLAlchemy | 数据持久化 |
| SSE | sse-starlette | 流式推送 LLM 输出 |
| 数据校验 | Pydantic v2 | 请求/响应模型校验 |
| 知识库 | 阿里云百炼 Retrieve API | 光伏领域知识检索 |
| 预测模型 | TensorFlow/Keras | Stacking 集成模型（晴天+多云） |

### 1.3 运行环境

```
操作系统: Windows
Python: 3.11 (项目自带 .venv)
数据库: MySQL 8.0 (localhost:3306)
```

### 1.4 启动方式

```bash
# 进入项目目录
cd D:\AAA_myProjects\howso\myAgent\solar_agent

# 用项目虚拟环境启动（不要用 conda 或系统 Python）
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# 访问地址
# 测试页面: http://localhost:8001
# API 文档: http://localhost:8001/docs
# 健康检查: http://localhost:8001/health
```

---

## 二、项目架构

### 2.1 目录结构

```
solar_agent/
├── app/                            # FastAPI Web 应用（新增层）
│   ├── main.py                     # 应用入口 + 内置测试页面 HTML
│   ├── config.py                   # 配置读取（从 .env）
│   ├── routers/                    # 路由层（≈ @RestController）
│   │   ├── auth.py                 #   注册/登录
│   │   ├── chat.py                 #   SSE 流式对话 + ask_user 回复
│   │   └── sessions.py             #   会话 CRUD + 安全校验
│   ├── schemas/                    # 数据模型（≈ DTO）
│   │   ├── chat.py                 #   ChatRequest / ReplyRequest
│   │   └── user.py                 #   RegisterRequest / LoginRequest
│   └── services/                   # 服务层（≈ @Service）
│       ├── agent_manager.py        #   Agent 实例缓存 + Bridge 管理
│       ├── ask_user_bridge.py      #   同步↔异步线程桥接
│       └── user_service.py         #   用户注册/登录/密码哈希
│
├── Agent/                          # Agent 核心逻辑（原有，几乎不变）
│   ├── agent.py                    # build_agent() 组装 AgentExecutor
│   ├── llm.py                      # ChatOpenAI 连接百炼 qwen-plus
│   ├── memory.py                   # 窗口+摘要+MySQL 持久化记忆
│   ├── prompt.py                   # 系统提示词 + 18 个工具索引
│   ├── tools.py                    # 工具注册
│   └── run.py                      # CLI 入口（改造前的主入口，保留）
│
├── predModels/Tools/               # 工具集（18 个工具，原有）
│   ├── ask_user_tool.py            # 用户交互
│   ├── power_query_tool.py         # 发电量查询
│   ├── table_io_tool.py            # 表格导入导出
│   ├── weather_fetcher_tool.py     # 气象数据
│   ├── pv_predictor.py             # 光伏预测核心
│   ├── knowledge_base_tool.py      # 知识库检索
│   ├── import_tool.py              # 数据导入入库
│   ├── chart_tool.py               # 可视化数据
│   ├── date_parser_tool.py         # 日期解析
│   ├── file_io_tool.py             # 文件读写
│   ├── cache_manager.py            # 缓存管理
│   └── ...
│
├── .env                            # 环境变量配置
├── sql/                            # 数据库建表脚本
├── scripts/                        # 数据导入脚本
├── docs/                           # 文档
│   └── backend_architecture_guide.md  # 后端架构详解
└── temp/file/                      # 生成的文件输出目录
```

### 2.2 分层架构

```
浏览器前端（内置 HTML）
        │ HTTP / SSE
FastAPI 应用入口 (main.py)
        │
路由层 (routers/) ─── 数据模型 (schemas/)
        │
服务层 (services/)
        │
Agent 核心层 (Agent/) ─── 工具集 (predModels/Tools/)
        │
数据层 (MySQL + 阿里云百炼 LLM + 知识库)
```

---

## 三、数据库设计

### 3.1 数据库连接

```
MYSQL_URL=mysql+pymysql://root:755028@localhost:3306/solar_agent?charset=utf8mb4
```

### 3.2 核心表结构

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `user_info` | 用户信息 | id, username, password_hash, role, display_name, status |
| `message_store` | 对话消息持久化 | id, session_id, **user_id**, message, created_at |
| `agent_summary_store` | 对话摘要 | session_id, **user_id**, summary |
| `solar_station` | 站点信息 | station_id, station_name, lat, lon, capacity_kw |
| `power_generation` | 发电量数据 | station_id, record_time, power_kwh |
| `weather_forecast_cache` | 气象预报缓存 | station_id, record_time, ... |
| `prediction_cache` | 预测结果缓存 | station_id, predict_date, ... |

> **注意**: `message_store` 和 `agent_summary_store` 的 `user_id` 列是用户隔离开发时新增的，用于按用户查询会话。

### 3.3 测试用户

当前数据库中已有两个测试用户：

| user_id | username | password | role | display_name |
|---------|----------|----------|------|--------------|
| 1 | testuser1 | test123456 | user | 测试用户 |
| 2 | testuser2 | test654321 | user | User2 |

---

## 四、API 接口清单

### 4.1 认证接口

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| POST | `/api/auth/register` | 用户注册 | `{username, password, display_name?}` | `{user_id, username, role, display_name}` |
| POST | `/api/auth/login` | 用户登录 | `{username, password}` | `{user_id, username, role, display_name}` |

### 4.2 会话管理接口

| 方法 | 路径 | 说明 | 必传参数 | 安全校验 |
|------|------|------|----------|----------|
| POST | `/api/sessions` | 创建会话 | `?user_id=X` | 创建时绑定所有者 |
| GET | `/api/sessions` | 查询会话列表 | `?user_id=X` | 只返回该用户的会话 |
| GET | `/api/sessions/{id}/messages` | 获取历史消息 | `?user_id=X` | `_verify_session_ownership` |
| DELETE | `/api/sessions/{id}` | 删除会话 | `?user_id=X` | `_verify_session_ownership` |

### 4.3 对话接口

| 方法 | 路径 | 说明 | 请求体 | 安全校验 |
|------|------|------|--------|----------|
| POST | `/api/chat/stream` | SSE 流式对话 | `{session_id, message, user_id}` | `_verify_session_ownership` |
| POST | `/api/chat` | 非流式对话（兜底） | `{session_id, message, user_id}` | `_verify_session_ownership` |
| POST | `/api/chat/{id}/reply` | 回复 ask_user | `{answer}` + `?user_id=X` | `_verify_session_ownership` |

### 4.4 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/` | 内置测试页面（HTML） |
| GET | `/docs` | FastAPI 自动生成的 Swagger 文档 |

---

## 五、已完成功能清单

### 5.1 Web 服务层（新增）

- [x] FastAPI 应用搭建（main.py + config.py）
- [x] CORS 跨域配置
- [x] 内置测试页面（登录页 + 左侧会话导航 + 聊天界面）
- [x] 用户注册/登录接口（salt+sha256 密码哈希）
- [x] 会话 CRUD 接口（创建/查询/删除/历史消息）
- [x] 用户隔离与安全校验（`_verify_session_ownership`）

### 5.2 SSE 流式对话（新增）

- [x] SSE 流式输出（`EventSourceResponse` + `asyncio.Queue`）
- [x] 5 种事件类型：`token` / `tool_start` / `tool_end` / `user_input_required` / `done`
- [x] `ask_user` 交互桥接（`threading.Event` + `asyncio.Queue` 跨线程通信）
- [x] 非流式对话兜底接口

### 5.3 Agent 核心层（原有 + 微调）

- [x] AgentExecutor 组装（LLM + 18 个工具 + Memory + Prompt）
- [x] 窗口+摘要+MySQL 持久化记忆
- [x] 自定义 `ChineseFriendlyConverter`（中文原文存储 + 携带 user_id）
- [x] 异步摘要生成（对话结束后不阻塞主链路）

### 5.4 工具集（原有 18 个工具）

- [x] 站点查询：`get_station_location`, `get_station_info`
- [x] 日期时间：`get_current_datetime`, `parse_date`
- [x] 气象数据：`get_weather_by_range`, `get_weather_records`
- [x] 发电预测：`predict_power`（Stacking 集成模型）
- [x] 数据查询：`get_actual_power`, `get_actual_power_by_range`, `get_predicted_power`, `get_power_comparison`
- [x] 文件 I/O：`write_file`, `read_file`, `verify_file`
- [x] 表格导出：`export_table`（支持日期范围导出原始逐小时数据）, `read_table`
- [x] 知识库：`search_knowledge_base`（百炼 Retrieve API）
- [x] 用户交互：`ask_user`
- [x] 可视化：`get_power_chart_data`（返回结构化 JSON）
- [x] 数据导入：`import_power_data`（Excel 入库，内置 ask_user 确认）

### 5.5 Bug 修复记录

| 日期 | 问题 | 修复方案 |
|------|------|----------|
| 07-13 | `export_table` 日期范围导出只返回单日数据 | 新增 `end_date` 参数 + `_query_actual_power_range` 函数，返回原始逐小时数据 |
| 07-13 | `_format_time_column` 丢弃日期只保留 HH:MM | 新增 `fmt` 参数，单日导出用 `%Y-%m-%d %H:%M` |
| 07-13 | `_verify_session_ownership` 被 `await` 调用报 500 | 去掉 `await`（同步函数不需要 await） |
| 07-13 | 新建会话在 DB 中无消息记录导致 404 | `_verify_session_ownership` 增加内存缓存检查 |

---

## 六、已知问题与待优化项

### 6.1 确认存在的 Bug

| 编号 | 问题 | 根因 | 修复方案 | 优先级 |
|------|------|------|----------|--------|
| BUG-001 | `ask_user` 前端只显示"请回复"不显示实际问题 | `ask_user_tool.py` 第 55 行 `_input_handler("👉 请回复: ")` 传的是固定字符串而非 `question` | 将 `question` 传入 `_input_handler`，使 `AskUserBridge.ask()` 收到完整问题文本 | 高 |

### 6.2 架构限制

| 编号 | 限制 | 影响 | 优化方向 | 优先级 |
|------|------|------|----------|--------|
| LIM-001 | `_current_bridge` 是单值，同一时刻只支持一个 ask_user 交互 | 多用户同时触发 ask_user 会互相干扰 | 改用 `contextvars` 方案 | 中 |
| LIM-002 | 无 JWT Token 认证，user_id 通过请求参数直传 | 安全性不足，user_id 可被伪造 | 加 JWT Token + 中间件拦截 | 高 |
| LIM-003 | Agent 实例缓存在内存中，服务重启后丢失 | 重启后需要重新创建 Agent（自动恢复，但会丢失内存中的对话上下文窗口） | 可接受，DB 中的消息持久化不受影响 | 低 |
| LIM-004 | 无文件下载接口 | `export_table` 生成的文件只能通过路径访问，前端无法直接下载 | 已预留 `export_table_to_bytes()` 函数，需加 FastAPI 下载接口 | 中 |

### 6.3 代码质量

| 编号 | 问题 | 建议 |
|------|------|------|
| CQ-001 | `main.py` 中的 `TEST_HTML` 有约 300 行内联 HTML | 前后端分离后移除，改为返回静态文件 |
| CQ-002 | `sessions.py` 中直接写 SQL（`text()`） | 可抽取为 Repository 层，或用 SQLAlchemy ORM |
| CQ-003 | 密码哈希用 salt+sha256 | 生产环境建议换 bcrypt 或 passlib |
| CQ-004 | `LangChainDeprecationWarning: connection_string was deprecated` | 后续将 `connection_string` 改为 `connection` |

---

## 七、核心机制说明

### 7.1 SSE 流式对话流程

```
浏览器 POST /api/chat/stream
  → 校验会话所有权
  → 获取 AgentExecutor + AskUserBridge
  → 创建 asyncio.Queue
  → 启动后台任务 consume_agent()
      → executor.astream_events()
      → 过滤事件 → queue.put()
  → 主循环从 Queue 取事件
      → yield SSE 事件给前端
  → 结束后异步触发摘要
```

### 7.2 ask_user 交互桥接

```
Agent 子线程: bridge.ask(question)
  → call_soon_threadsafe 把问题推入 asyncio.Queue
  → threading.Event.wait() 阻塞子线程

前端收到 user_input_required 事件
  → 弹出输入框
  → 用户输入
  → POST /api/chat/{id}/reply?user_id=X

主线程: bridge.reply(answer)
  → threading.Event.set() 唤醒子线程
  → Agent 继续执行
```

### 7.3 用户隔离机制

```
所有会话操作接口都调用 _verify_session_ownership(session_id, user_id):

1. 查 agent_manager._agents 内存缓存（刚创建还没对话的会话）
2. 查 message_store 表（已有对话记录）
3. 查 agent_summary_store 表（有摘要但消息已清理）
4. 都没找到 → 404
5. user_id 不匹配 → 403
```

### 7.4 记忆系统设计

```
PersistentWindowSummaryMemory:
  - 最近 3 轮对话保留完整原文（窗口）
  - 更早的对话用 LLM 总结成摘要
  - 消息和摘要都持久化到 MySQL
  - 每条消息携带 user_id
  - 摘要异步生成，不阻塞对话
```

---

## 八、工具集清单（18 个）

| 分类 | 工具名 | 文件 | 说明 |
|------|--------|------|------|
| 站点·时间 | `get_station_location` | power_query_tool.py | 查站点经纬度 |
| | `get_station_info` | power_query_tool.py | 查站点基本信息 |
| | `get_current_datetime` | date_parser_tool.py | 查当前时间 |
| | `parse_date` | date_parser_tool.py | 自然语言日期转 YYYY-MM-DD |
| 气象数据 | `get_weather_by_range` | weather_fetcher_tool.py | 查气象数据 |
| | `get_weather_records` | weather_fetcher_tool.py | 查气象记录 |
| 发电预测 | `predict_power` | pv_predictor.py | Stacking 集成模型预测 |
| 数据查询 | `get_actual_power` | power_query_tool.py | 查实际发电量（单日） |
| | `get_actual_power_by_range` | power_query_tool.py | 查实际发电量（范围） |
| | `get_predicted_power` | power_query_tool.py | 查预测发电量 |
| | `get_power_comparison` | power_query_tool.py | 预测 vs 实际对比 |
| 文件 I/O | `write_file` | file_io_tool.py | 写 .txt/.md 文件 |
| | `read_file` | file_io_tool.py | 读文件 |
| | `verify_file` | file_io_tool.py | 验证文件存在和内容 |
| 表格导出 | `export_table` | table_io_tool.py | 导出 Excel/CSV（支持日期范围） |
| | `read_table` | table_io_tool.py | 读取表格 |
| 知识库 | `search_knowledge_base` | knowledge_base_tool.py | 百炼 Retrieve API |
| 用户交互 | `ask_user` | ask_user_tool.py | 向用户提问并等待回复 |
| 可视化 | `get_power_chart_data` | chart_tool.py | 返回图表 JSON |
| 数据导入 | `import_power_data` | import_tool.py | Excel 发电量数据入库 |

---

## 九、环境配置

### 9.1 .env 文件

```env
# 阿里云百炼 LLM
API_KEY=sk-xxxxx
BASE_URL=https://llm-xxxxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen-plus

# 百炼知识库
ALIBABA_CLOUD_ACCESS_KEY_ID=LTAIxxxxx
ALIBABA_CLOUD_ACCESS_KEY_SECRET=xxxxx
BAILIAN_INDEX_ID=vcrvmwq5st

# 预测模型路径
MODEL_SUNNY=D:\AAA光伏项目\TRAIN\...\晴天_5-8
MODEL_CLOUDY=D:\AAA光伏项目\TRAIN\...\新融合_多云_ssrd增强版

# 数据库
MYSQL_URL=mysql+pymysql://root:755028@localhost:3306/solar_agent?charset=utf8mb4

# FastAPI
CORS_ORIGINS=http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000
ASK_USER_TIMEOUT=120

# 文件输出
FILE_DIR=D:\AAA_myProjects\howso\myAgent\solar_agent\temp\file
```

### 9.2 依赖安装

```bash
# 使用项目自带的 .venv 虚拟环境
# 不要用 conda 或系统 Python

# 如需重新安装依赖
.venv\Scripts\pip install -r requirements.txt
```

### 9.3 关键依赖

| 包名 | 用途 |
|------|------|
| fastapi | Web 框架 |
| uvicorn | ASGI 服务器 |
| sse-starlette | SSE 流式响应 |
| langchain | Agent 框架 |
| langchain-openai | OpenAI 兼容 LLM |
| langchain-community | SQLChatMessageHistory |
| sqlalchemy | ORM |
| pymysql | MySQL 驱动 |
| pydantic-settings | 配置管理 |
| openpyxl | Excel 读写 |
| tensorflow | 预测模型 |

---

## 十、未来开发计划

### 10.1 P0 优先级（近期）

| 编号 | 任务 | 说明 |
|------|------|------|
| F-001 | 修复 ask_user question 不传前端的 Bug | BUG-001，改 `ask_user_tool.py` 第 55 行 |
| F-002 | 前后端分离 | 用 Vue3 或 React 重写前端，后端纯 API |
| F-003 | JWT Token 认证 | 登录返回 Token，后续请求携带 Token 而非 user_id |
| F-004 | 文件下载接口 | 用 `export_table_to_bytes()` 实现 `/api/download` 接口 |

### 10.2 P1 优先级（中期）

| 编号 | 任务 | 说明 |
|------|------|------|
| F-005 | RBAC 权限拦截中间件 | 基于 role 字段（super_admin/admin/user）做接口级权限控制 |
| F-006 | 用户管理界面 | 管理员可增删改查用户、重置密码 |
| F-007 | 多并发 ask_user 支持 | 用 `contextvars` 替换 `_current_bridge` 单值 |
| F-008 | 会话标题自动生成 | 创建会话后根据首轮对话内容用 LLM 生成标题 |
| F-009 | 图表前端渲染 | 前端接收 `get_power_chart_data` 的 JSON 用 ECharts 渲染 |

### 10.3 P2 优先级（远期）

| 编号 | 任务 | 说明 |
|------|------|------|
| F-010 | 预测模型在线更新 | 支持上传新数据后自动重训模型 |
| F-011 | 多租户支持 | 不同组织/公司数据隔离 |
| F-012 | 监控告警 | 对话日志、工具调用统计、异常告警 |
| F-013 | 移动端适配 | 响应式布局或独立移动端 |

---

## 十一、开发约定

### 11.1 代码规范

- 所有 Python 文件使用 UTF-8 编码
- 函数/类必须有 docstring（中文）
- 新增工具必须在 `Agent/prompt.py` 的 SYSTEM_PROMPT 中添加工具索引和调用规则
- 文件生成必须遵循：`create tool` → `verify_file` → `reply to user`
- 严禁在调用创建工具之前就声称文件已生成
- 每次只调一个工具，等返回后再决定下一步

### 11.2 数据库规范

- MySQL 连接串必须从环境变量 `MYSQL_URL` 读取
- 所有写库操作必须通过 `ask_user` 工具获取用户确认
- `message_store` 只 INSERT 不 DELETE（窗口在读取时截取）
- 用户只能操作自己 user_id 下的会话

### 11.3 安全规范

- 所有会话操作接口必须携带 `user_id` 并通过 `_verify_session_ownership` 校验
- 密码必须哈希存储（当前 salt+sha256，建议升级 bcrypt）
- 路径遍历攻击必须拦截（如 `../`）
- 知识库检索结果需标注信息来源

### 11.4 Git 规范

- `.env` 文件不提交到 Git
- `temp/file/` 目录不提交
- `.venv/` 目录不提交

---

## 十二、关键文件索引

| 文件 | 作用 | 重要程度 |
|------|------|----------|
| `app/main.py` | FastAPI 入口 + 测试页面 HTML | 核心 |
| `app/routers/chat.py` | SSE 流式对话 + ask_user 回复 | 核心 |
| `app/routers/sessions.py` | 会话 CRUD + 安全校验 | 核心 |
| `app/routers/auth.py` | 注册/登录 | 核心 |
| `app/services/agent_manager.py` | Agent 实例缓存 + Bridge 管理 | 核心 |
| `app/services/ask_user_bridge.py` | 同步↔异步桥接 | 核心 |
| `app/services/user_service.py` | 用户注册/登录 | 重要 |
| `Agent/agent.py` | build_agent() 组装 | 核心 |
| `Agent/memory.py` | 记忆管理（窗口+摘要+持久化） | 核心 |
| `Agent/prompt.py` | 系统提示词 + 工具索引 | 重要 |
| `Agent/llm.py` | LLM 连接配置 | 重要 |
| `predModels/Tools/ask_user_tool.py` | ask_user 工具（有已知 Bug） | 重要 |
| `predModels/Tools/table_io_tool.py` | 表格导出（含日期范围） | 重要 |
| `predModels/Tools/power_query_tool.py` | 发电量查询 | 重要 |
| `.env` | 环境变量配置 | 核心 |
| `docs/backend_architecture_guide.md` | 后端架构详解文档 | 参考 |

---

## 十三、测试验证清单

### 13.1 服务启动验证

```bash
# 1. 启动服务
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# 2. 检查健康
curl http://localhost:8001/health
# 期望: {"status":"ok","service":"SolarAgent"}

# 3. 打开测试页面
# 浏览器访问 http://localhost:8001
```

### 13.2 功能验证

| 测试项 | 操作 | 期望结果 |
|--------|------|----------|
| 用户登录 | 用 testuser1/test123456 登录 | 进入主界面，左侧显示会话列表 |
| 创建会话 | 点击"+ 新建会话" | 左侧新增会话项，右侧显示聊天界面 |
| 流式对话 | 输入"你好" | SSE 逐字显示回复 |
| 工具调用 | 输入"查一下英杰电站的装机容量" | 显示工具执行卡片 + 回复 |
| ask_user | 输入"查询哲丰电站发电量" | 弹出输入框（但问题文本有 Bug，只显示"请回复"） |
| 会话切换 | 点击左侧不同会话 | 加载对应历史消息 |
| 会话删除 | 点击会话项的 × | 会话从列表移除 |
| 用户隔离 | 用 testuser2 登录 | 看不到 testuser1 的会话 |
| 越权访问 | testuser1 访问 testuser2 的会话 | 返回 403 |

### 13.3 安全验证

```python
# 可用以下脚本验证安全性
# 跨用户访问 → 403
# 缺少 user_id → 422
# 不存在的会话 → 404
# 所有者正常操作 → 200
```

---

## 十四、交接确认

### 14.1 接手人需确认事项

- [ ] 能用 `.venv` 虚拟环境成功启动服务
- [ ] 能用测试账号登录并完成一轮对话
- [ ] 了解 SSE 流式对话的基本原理
- [ ] 了解 ask_user 交互桥接机制
- [ ] 了解用户隔离和安全校验机制
- [ ] 已阅读 `docs/backend_architecture_guide.md` 后端架构文档
- [ ] 已知 BUG-001（ask_user 问题不传前端）待修复
- [ ] 已知未来计划（JWT、前后端分离、RBAC 等）

### 14.2 联系方式

如有疑问，可参考以下文档：
- `docs/backend_architecture_guide.md` — 后端架构详解（小白友好，含 Spring 对比）
- `project_memory.md` — 项目记忆文件（记录了所有开发决策和经验教训）

---

> **文档版本**: v1.0
> **最后更新**: 2026-07-14
