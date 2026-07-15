# SolarAgent

SolarAgent 是一个面向光伏电站场景的智能分析 Agent。系统通过大语言模型理解用户意图，调用气象、发电量、预测、缓存、文件和知识库工具，完成光伏站点查询、发电预测、历史回测和数据分析。

项目定位不是单纯的聊天机器人，而是一个具备生产产品雏形的 Agent 系统：有用户认证、会话隔离、持久化记忆、工具调用、人工确认、数据缓存和结果验证等完整链路。

## 当前版本能力

### 1. 光伏业务能力

- 查询站点基本信息、站点位置和装机容量。
- 查询历史实际发电量和发电量区间。
- 获取历史气象实况和未来天气预报。
- 执行光伏发电量预测。
- 对比预测发电量与实际发电量，并输出偏差、MAE、RMSE、MAPE 等指标。
- 支持历史日期的实况回测模式。
- 支持未来日期的天气预报模式。
- 预测结果、气象数据和发电数据使用数据库缓存，减少重复请求和重复计算。
- 支持表格读写、文件生成、文件验证和发电数据导入。
- 支持知识库检索和图表数据生成。

### 2. Agent 能力

- 基于工具调用完成多步骤业务任务。
- 使用系统日期上下文，避免“5月1日”被错误解释为旧年份。
- 日期先经过 `parse_date` 统一转换为 `YYYY-MM-DD`。
- 站点名称模糊或匹配多个站点时，通过 `ask_user` 请求用户确认。
- 发电预测前要求用户核对站点和日期。
- Agent 只有在真正调用工具并获得结果后，才能向用户声明任务进度或结果。
- 支持连续多轮对话，并将会话记忆持久化到 MySQL。
- 使用滑动窗口和摘要机制控制长期对话的 Token 消耗。

### 3. 安全能力

- 使用 JWT Bearer Token 认证。
- 用户身份从服务端 Token 中解析，不信任前端传入的 `user_id`。
- 使用 Argon2id 保存密码哈希，不再保留旧哈希算法兼容逻辑。
- 会话、消息和长期记忆按照用户进行隔离。
- 每个 Agent 会话绑定独立的 `session_id`。
- AskUser 交互按照会话路由，避免跨用户或跨会话响应。
- 敏感配置通过 `.env` 管理，不应提交真实密钥到 Git。

## 系统架构

```text
用户 / 未来 Vue 前端
        |
        | JWT + HTTP / SSE
        v
FastAPI API 层
  ├── auth       注册、登录、Token
  ├── chat       普通对话、SSE 流式对话
  └── sessions   会话创建、查询和历史消息
        |
        v
Agent Manager
  ├── Agent 构建
  ├── 用户与会话隔离
  ├── AskUserBridge
  └── 长期记忆加载与保存
        |
        v
LLM + 工具调用
  ├── 日期与站点工具
  ├── 气象数据工具
  ├── 光伏预测工具
  ├── 发电量查询工具
  ├── 文件与表格工具
  ├── 知识库工具
  └── 用户确认工具
        |
        v
MySQL / SQLite
  ├── 用户与认证数据
  ├── 会话和消息
  ├── 长期记忆与摘要
  ├── 发电量数据
  └── 气象、预测和回测缓存
```

## 主要目录

```text
solar_agent/
├── backend/
│   ├── app/                   FastAPI 应用、路由、服务和 API 模型
│   ├── Agent/                Agent 编排、提示词、记忆和 LLM 组装
│   ├── tools/                光伏预测、查询、气象、文件和图表工具
│   ├── sql/                  数据库结构和迁移脚本
│   └── temp/                 后端运行时文件和导出目录
├── frontend/                 独立 Vue 3 + Vite 前端
├── tests/                    阶段性契约测试
├── docs/                     交接、架构和学习文档
├── .env                      本地环境变量，不提交真实密钥
└── requirements.txt          固定版本依赖
```
## 关键业务链路

### 普通对话

```text
登录获取 JWT
  -> 创建或选择 session_id
  -> POST /api/chat/stream
  -> Agent 读取会话记忆
  -> LLM 判断意图
  -> 调用业务工具
  -> SSE 返回过程事件和最终结果
  -> 保存消息与记忆
```

### AskUser 用户确认

```text
Agent 发现站点或日期不明确
  -> 调用 ask_user
  -> AskUserBridge 暂停当前 Agent 线程
  -> SSE 通道通知前端弹出问题
  -> 用户提交回答
  -> 后端按 session_id 唤醒对应 Bridge
  -> Agent 继续执行后续工具
```

### 历史回测

```text
用户请求过去日期预测
  -> 判断目标日期早于当前日期
  -> 使用历史气象实况接口
  -> 标记 weather_data_mode=historical_actual
  -> 生成预测结果
  -> 查询实际发电量
  -> 输出预测与实际对比
```

未来日期则使用天气预报接口，并标记为 `forecast`。两种模式使用独立缓存，避免历史实况和未来预报相互污染。

## 预测缓存迁移

历史回测模式使用了 `prediction_cache.weather_data_mode` 字段。已有数据库需要执行一次迁移：

```text
backend/sql/migrations/002_prediction_weather_data_mode.sql
```

迁移后，预测缓存按照以下模式区分：

- `historical_actual`：历史实况回测。
- `forecast`：未来天气预报预测。

如果已经清理过旧缓存，仍然需要执行数据库结构迁移，因为代码会查询新的模式字段。

## 环境准备

项目使用现有虚拟环境，不需要重建：

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
$python = "D:\AAA_myProjects\howso\myAgent\solar_agent\.venv\Scripts\python.exe"
& $python -m pip install -r requirements.txt
```

`.env` 至少需要根据本地环境配置以下内容：

```env
API_KEY=your_api_key
BASE_URL=https://your-openai-compatible-endpoint/v1
MODEL_NAME=your_model_name
MYSQL_URL=your_mysql_connection_string
JWT_SECRET_KEY=your_long_random_secret
JWT_ACCESS_TOKEN_EXPIRE_SECONDS=3600
ASK_USER_TIMEOUT=120
```

不要把真实 API Key、数据库密码或 JWT 密钥提交到 Git 仓库。

## 启动方式

### 启动 FastAPI 服务

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
$python = "D:\AAA_myProjects\howso\myAgent\solar_agent\.venv\Scripts\python.exe"
& $python -m backend.app.main
```

服务启动后可以访问：

- API 文档：`http://127.0.0.1:8001/docs`
- 内置测试页面：`http://127.0.0.1:8001/`
- 流式对话：`POST /api/chat/stream`
- 普通对话：`POST /api/chat`
- 注册：`POST /api/auth/register`
- 登录：`POST /api/auth/login`

### 命令行运行 Agent

```powershell
$python = "D:\AAA_myProjects\howso\myAgent\solar_agent\.venv\Scripts\python.exe"
& $python -m backend.Agent.run
```

## 数据库初始化顺序

首次部署时，根据数据库类型执行对应脚本：

1. `backend/sql/init_schema.sql`
2. `backend/sql/cache_schema.sql`
3. `backend/sql/memory_schema.sql`
4. `backend/sql/migrations/001_phase1_memory_schema.sql`
5. `backend/sql/migrations/002_prediction_weather_data_mode.sql`

生产环境建议使用迁移脚本管理数据库结构，不要直接删除业务表。

## 测试与验证

使用项目虚拟环境执行阶段性契约测试：

```powershell
$python = "D:\AAA_myProjects\howso\myAgent\solar_agent\.venv\Scripts\python.exe"
& $python -m pytest -q tests\test_phase1_contract.py
& $python -m compileall -q backend tests
```

重点验证内容包括：

- Token 认证和用户身份隔离。
- Argon2id 密码哈希。
- AskUser 实际传递用户问题并按会话恢复。
- 日期解析和当前日期注入。
- Agent 工具执行真实性。
- 历史实况回测与未来预报缓存隔离。

## 当前产品约定与限制

- 当前前端仍以内置测试页面为主，后续计划使用 Vue 重建独立前端。
- Token 接口已经为前后端分离预留，Vue 前端通过 `Authorization: Bearer <token>` 调用后端。
- 数据导入暂时采用简单约定：用户在表格首行填写完整站点名称。复杂的站点匹配预览、未匹配禁止自动建站和二次确认暂缓实现。
- 当前系统面向个人项目和小规模生产交付，暂未引入消息队列、分布式任务调度和完整可观测平台。
- 阿里云模型接口偶发连接失败时，需要结合网络、TLS、代理和上游服务状态排查，不应仅通过增加 Agent 重试次数掩盖问题。

## 后续产品化方向

1. 使用 Vue 构建正式前端，完善登录、会话列表、流式消息和 AskUser 弹窗。
2. 增加统一的 Agent 执行状态、错误码和任务追踪。
3. 增加预测结果可信度、模型版本和回测报告。
4. 完善数据导入预览、站点匹配和重复数据处理。
5. 增加日志、指标、链路追踪和接口限流。
6. 增加 Docker 部署、定时预测和后台任务能力。
7. 建立工具调用评测集，持续评估日期解析、站点识别、预测准确率和任务完成率。

## 学习文档

- [项目架构与产品路线](docs/current_architecture_and_product_roadmap.md)
- [Token 认证与 AskUser 学习指南](docs/phase1/token_and_ask_user_learning_guide.md)
- [阶段一验收清单](docs/phase1/acceptance_checklist.md)
- [阶段一学习笔记](docs/phase1/phase1_learning_notes.md)

## 仓库地址

GitHub：<https://github.com/yke6519-droid/SolarAgent>