# SolarAgent

SolarAgent 是一个面向光伏电站场景的智能分析 Agent。系统通过大语言模型理解用户意图，调用气象、发电量、预测、缓存、文件和知识库工具，完成光伏站点查询、发电预测、历史回测和数据分析。当前已形成前后端分离结构：后端提供 FastAPI 接口和 Agent 编排，前端使用 Vue 3 + Vite 构建独立工作台。

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
- 支持 1～5 天逐小时数据和超过 5 天日总发电量数据的可视化。
- 图表使用结构化 ECharts 数据描述，支持历史会话中的图表快照恢复。

### 2. Agent 能力

- 基于工具调用完成多步骤业务任务。
- 使用系统日期上下文，避免“5月1日”被错误解释为旧年份。
- 日期先经过 `parse_date` 统一转换为 `YYYY-MM-DD`。
- 站点名称模糊或匹配多个站点时，通过 `ask_user` 请求用户确认。
- 发电预测前要求用户核对站点和日期。
- Agent 只有在真正调用工具并获得结果后，才能向用户声明任务进度或结果。
- 支持连续多轮对话，并将会话记忆持久化到 MySQL。
- 使用滑动窗口和摘要机制控制长期对话的 Token 消耗。
- 支持 SSE 流式输出：前端可实时展示 Agent 的思考、工具调用、工具结果和增量回答。
- Agent 输出统一按 Markdown 处理，前端使用 MarkdownIt + DOMPurify 安全渲染。

### 3. 安全能力

- 使用 JWT Bearer Token 认证。
- 用户身份从服务端 Token 中解析，不信任前端传入的 `user_id`。
- 使用 Argon2id 保存密码哈希，不再保留旧哈希算法兼容逻辑。
- 会话、消息和长期记忆按照用户进行隔离。
- 每个 Agent 会话绑定独立的 `session_id`。
- AskUser 交互按照会话路由，避免跨用户或跨会话响应。
- 敏感配置通过 `.env` 管理，不应提交真实密钥到 Git。
- Markdown、链接、工具结果和图表提示框均进行安全渲染或转义，避免 Agent 输出引入 XSS。
- 前端登录页和工作台路由分离，未持有有效登录态时会被路由守卫拦截到 `/login`。

## 系统架构

```text
用户浏览器
        |
        v
Vue 3 + Vite 前端工作台
  ├── 登录页 / 路由守卫 / 登录态
  ├── Axios：登录、会话、历史消息等 REST 请求
  ├── Fetch：POST /api/chat/stream 的 SSE 流式读取
  ├── MarkdownIt + DOMPurify：安全渲染 Agent 输出
  └── ECharts：结构化图表渲染与历史快照恢复
        |
        | JWT + HTTP / SSE
        v
FastAPI API 层
  ├── auth       注册、登录、Token
  ├── chat       普通对话、SSE 流式对话、AskUser 回复
  └── sessions   会话创建、查询、删除和历史消息
        |
        v
Agent Manager
  ├── Agent 构建
  ├── 用户与会话隔离
  ├── AskUserBridge
  ├── 流式事件编排
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
  ├── 图表数据工具
  └── 用户确认工具
        |
        v
MySQL / SQLite
  ├── 用户与认证数据
  ├── 会话、消息和长期记忆
  ├── 图表结构化快照
  ├── 发电量数据
  └── 气象、预测和回测缓存
```

## 主要目录

```text
solar_agent/
├── backend/
│   ├── app/                  FastAPI 应用、路由、服务和 API 模型
│   │   └── routers/          auth、chat、sessions 路由
│   ├── Agent/                Agent 编排、提示词、记忆和 LLM 组装
│   ├── tools/                光伏预测、查询、气象、文件和图表工具
│   ├── sql/                  数据库结构和迁移脚本
│   └── temp/                 后端运行时文件和导出目录
├── frontend/                 独立 Vue 3 + Vite 前端
│   ├── src/api.js            Axios REST + Fetch SSE 业务 API 层
│   ├── src/router.js         登录态路由守卫
│   ├── src/components/       Markdown 消息和 ECharts 图表组件
│   └── src/views/LoginView.vue 登录页面
├── tests/                    阶段性契约测试
├── docs/                     交接、架构、日报和学习文档
├── .env                      本地环境变量，不提交真实密钥
└── requirements.txt          固定版本依赖
```

## 关键业务链路

### 普通对话

```text
登录获取 JWT
  -> 前端保存登录态并进入工作台
  -> 每次进入工作台默认展示欢迎页
  -> 用户创建或选择 session_id
  -> POST /api/chat/stream
  -> Agent 读取会话记忆
  -> LLM 判断意图
  -> SSE 增量返回 thinking / token / tool_start / tool_end / done
  -> 前端实时更新消息和执行轨迹
  -> 保存消息与记忆
```

前端的业务调用入口保持统一：普通 REST 请求（登录、会话、历史消息、删除等）使用 Axios；流式对话使用 Fetch 直接消费 `ReadableStream`。这样调用方只依赖 `login()`、`listSessions()`、`streamChat()` 等业务函数，不需要感知底层传输方式。

### 前端 Markdown 安全渲染

```text
Agent / 工具返回 Markdown
  -> MarkdownIt 解析（禁用原始 HTML）
  -> DOMPurify 清洗标签、属性和协议
  -> Vue 渲染安全 HTML
  -> 链接和 ECharts tooltip 继续做属性转义
```

流式输出期间，每次收到增量内容都会走同一套渲染链路，避免只在最终结果阶段清洗造成安全边界缺口。

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

### 结构化图表与历史恢复

```text
用户提出可视化需求
  -> chart_tool 解析数据范围和指标
  -> 返回 chart_type + title + xAxis + series 等结构化 JSON
  -> 后端保存 chart_snapshot
  -> SSE 返回图表事件
  -> 前端按白名单模板渲染 ECharts
  -> 历史会话读取快照并重新 init / setOption
```

图表类型由 Agent 负责选择和填充数据，前端只负责按模板渲染，不执行 Agent 返回的前端代码。当前已支持逐小时曲线和日总发电量趋势，并为后续柱状图、饼图等模板扩展预留接口。

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

### 安装前端依赖

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm install
```

如需指定后端地址，可在 `frontend/.env.local` 中配置：

```env
VITE_API_BASE_URL=http://127.0.0.1:8001/api
```

## 启动方式

### 启动 FastAPI 服务

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8001
```

服务启动后可以访问：

- API 文档：`http://127.0.0.1:8001/docs`
- API 服务信息：`http://127.0.0.1:8001/`
- 流式对话：`POST /api/chat/stream`
- 普通对话：`POST /api/chat`
- 注册：`POST /api/auth/register`
- 登录：`POST /api/auth/login`

### 启动 Vue 前端

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm run dev
```

前端默认访问：`http://localhost:5173`。开发环境下通过 `VITE_API_BASE_URL` 连接 FastAPI；未登录访问工作台会自动跳转到 `/login`。

## 数据库初始化顺序

首次部署时，根据数据库类型执行对应脚本：

1. `backend/sql/init_schema.sql`
2. `backend/sql/cache_schema.sql`
3. `backend/sql/memory_schema.sql`
4. `backend/sql/migrations/001_phase1_memory_schema.sql`
5. `backend/sql/migrations/002_prediction_weather_data_mode.sql`
6. `backend/sql/migrations/003_chart_snapshot_store.sql`

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

- 当前已完成 Vue 3 + Vite 独立前端的第一阶段闭环：登录、登录态保存、路由拦截、会话管理、欢迎页、历史消息和流式对话。登录态当前保存在浏览器 localStorage，后续计划迁移到 Pinia。
- 前端 API 层保持统一业务入口：Axios 负责普通 REST 请求，Fetch 负责 SSE 流式对话；两者共享 JWT、错误处理和登录态失效处理。
- 数据导入暂时采用简单约定：用户在表格首行填写完整站点名称。复杂的站点匹配预览、未匹配禁止自动建站和二次确认暂缓实现。
- 当前系统面向个人项目和小规模生产交付，暂未引入 Pinia 全局状态、消息队列、分布式任务调度和完整可观测平台。
- 阿里云模型接口偶发连接失败时，需要结合网络、TLS、代理和上游服务状态排查，不应仅通过增加 Agent 重试次数掩盖问题。

## 后续产品化方向

1. 将登录态、当前用户和会话状态迁移到 Pinia，保留统一 API 层。
2. 完善 Agent 执行状态协议，展示“思考 -> 工具调用 -> 工具结果 -> 继续思考”的可折叠时间线。
3. 扩展可视化模板，由 Agent 选择折线图、柱状图、饼图等类型，前端按白名单模板渲染。
4. 增加预测结果可信度、模型版本和回测报告。
5. 完善数据导入预览、站点匹配和重复数据处理（当前复杂校验仍按产品约定暂缓）。
6. 增加日志、指标、链路追踪、接口限流和内容安全策略（如 CSP）。
7. 增加 Docker 部署、定时预测和后台任务能力。
8. 建立工具调用评测集，持续评估日期解析、站点识别、预测准确率和任务完成率。
## 学习文档

- [项目架构与产品路线](docs/current_architecture_and_product_roadmap.md)
- [Token 认证与 AskUser 学习指南](docs/phase1/token_and_ask_user_learning_guide.md)
- [阶段一验收清单](docs/phase1/acceptance_checklist.md)
- [阶段一学习笔记](docs/phase1/phase1_learning_notes.md)
- [P1 阶段日报（2026-07-15）](docs/2026-07-15-P1阶段日报.md)
- [SSE 流式输出与会话锁设计亮点](docs/SSE-流式输出-与-会话锁-设计亮点.md)

### 图表历史快照迁移

为恢复历史会话中的 ECharts 图表，需要执行 `backend/sql/migrations/003_chart_snapshot_store.sql`，创建结构化图表快照表。该表与 LangChain 的 `message_store` 分离，保存的是可校验的图表数据描述，而不是前端代码或图片。

## 仓库地址

GitHub：<https://github.com/yke6519-droid/SolarAgent>
