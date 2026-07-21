# SolarAgent

SolarAgent 是一个面向光伏电站运营场景的智能分析系统。用户可以通过自然语言查询站点、气象和发电量数据，执行光伏发电预测、历史回测与图表分析。

项目已经完成前后端分离：后端使用 FastAPI 提供认证、会话、Agent 和流式接口，前端使用 Vue 3 + Vite 构建独立工作台。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、Axios、Fetch、ECharts、MarkdownIt、DOMPurify |
| 后端 | FastAPI、SSE、LangChain、Pydantic |
| 数据库 | MySQL、SQLAlchemy、PyMySQL |
| 认证 | JWT、HttpOnly Refresh Cookie、Argon2id |
| 预测 | TensorFlow/Keras、LightGBM、XGBoost、Pandas、NumPy |

## 已实现功能

### 1. 前端工作台

#### 1.1 登录、路由与主题

- 独立登录页和工作台路由，未登录用户不能直接进入工作台。
- 支持浅色、深色两种主题。

#### 1.2 会话管理

- 进入工作台默认展示欢迎页，不自动进入最近会话。
- 点击首页快捷任务卡片后，只将任务短语填入输入框，由用户确认或修改后发送。
- 左侧会话列表支持分页加载、选择、重命名和删除。
- “回到首页”不会立即创建空会话，用户第一次发送消息时才创建新会话。
- 会话名称默认截取第一条用户消息的前 10 个字符，也支持手动重命名。
- 历史消息按页加载，向上加载旧消息时保持当前滚动位置。

#### 1.3 流式交互与 AskUser

- 支持 SSE 流式对话，展示文本增量、工具调用、工具结果、等待确认和任务完成状态。
- 支持 AskUser 确认卡片，用户提交回答后继续原来的 Agent 任务。

#### 1.4 内容与图表渲染

- Agent Markdown 输出通过 MarkdownIt 渲染，并使用 DOMPurify 清洗。
- ECharts 图表嵌入对话流，支持 Tooltip、浅色/深色主题和历史会话图表恢复。

### 2. 用户认证与访问控制

#### 2.1 用户认证

- 支持用户注册、登录、退出登录。
- 密码使用 Argon2id 哈希保存。
- Access Token 使用 JWT Bearer Token，业务接口从 Token 解析当前用户。

#### 2.2 Token 续期

- Refresh Token 通过 HttpOnly Cookie 保存，前端 JavaScript 无法直接读取。
- Refresh Token 在服务端保存哈希，并支持轮换、撤销和绝对过期时间。
- 普通 REST 请求收到 401 后，可刷新 Access Token 并重放原请求一次。
- SSE 流式请求收到 401 后，可刷新 Access Token 并重新建立流式连接一次。
- 只有检测到用户键盘、鼠标或触摸操作并处于活跃窗口时，才允许主动续期。
- 登录会话真正失效后，前端展示“重新登录”弹窗，清理本地状态并返回登录页。

#### 2.3 用户与会话隔离

- 用户、会话、消息、摘要和图表快照均按 `user_id` 隔离。

### 3. Agent 编排与长期记忆

#### 3.1 工具调用与执行约束

- Agent 通过 LangChain 工具调用完成日期、站点、气象、发电量、预测、文件和图表任务。
- Prompt 要求 Agent 先拆分任务，再按步骤逐个调用工具。
- 只有获得真实工具结果后，Agent 才能向用户声明查询、分析或生成结果。
- 日期先通过 `parse_date` 标准化，工具错误和无数据情况如实返回。

#### 3.2 会话记忆

- 会话、消息和摘要持久化到 MySQL。
- Agent 每次只读取最近消息窗口和历史摘要，不加载整段历史对话。
- 摘要使用增量游标，只处理尚未摘要且已离开最近窗口的消息。
- 摘要任务在对话完成后异步执行，不阻塞本次回答。
- 同一会话只允许一个摘要任务运行，并使用摘要版本进行并发更新保护。

#### 3.3 站点结构化解析与上下文复用

- 支持按站点简称查询站点名称、ID、位置、经纬度和装机容量。
- 单次 Agent 任务中，已解析的结构化站点对象会被复用，避免不同工具重复询问同一个模糊站点。
- 模糊名称匹配到多个站点时，通过 AskUser 让用户选择一次。
- 用户明确选择的站点会写入当前会话的 `active_station`。
- 下一轮使用“它”“这个站点”“刚才那个站点”等指代时，可以复用上次确认的站点。
- 用户明确提供新站点、站点列表或地区时，新范围优先，不会被旧的 `active_station` 覆盖。
- 支持单站点、明确多站点和地区站点三种范围。
- 地区查询根据数据库中的省、市、地址和站点名称进行模糊匹配，不维护写死的地区白名单。
- 地区查询默认最多返回 20 个站点，超过限制时要求用户缩小范围或分批执行。

### 4. 光伏业务能力

#### 4.1 站点与发电数据

- 查询站点基本信息、位置和装机容量。
- 查询单日或日期范围内的实际发电量。
- 查询已经缓存的预测发电量。

#### 4.2 气象与发电预测

- 获取历史气象实况和未来天气预报。
- 执行单站点 24 小时光伏发电量预测。
- 预测前在工具内部确认最终站点和标准日期。
- 历史日期使用实况气象进行回测，未来日期使用天气预报进行预测。
- 对比预测值与实际值，计算偏差、MAE、RMSE、MAPE 等指标。

#### 4.3 缓存与文件工具

- 缓存气象数据、模型实例和预测结果，减少重复请求和重复计算。
- 支持知识库检索、文件读写、文件校验、表格导入导出和发电量数据导入。

### 5. 图表可视化

#### 5.1 图表生成链路

新图表任务采用以下固定链路：

```text
get_chart_capabilities
        -> get_power_dataset
        -> DatasetArtifact
        -> create_chart_plan
        -> ChartSpec
        -> SSE chart_spec
        -> Vue + ECharts
```

- Agent 先从图表能力注册表选择已开放能力。
- `get_power_dataset` 调用真实业务查询或预测缓存，生成标准化数据制品。
- Agent 只提交受限的 ChartPlan，不直接生成 JavaScript、HTML 或 ECharts formatter。
- 后端校验数据归属、字段角色、数据粒度、序列数量和点数限制。
- ChartService 将合法 ChartPlan 编译成前后端统一的 ChartSpec。
- 图表快照独立保存到 MySQL，重新打开历史会话时可以再次渲染。

#### 5.2 图表能力注册表

当前开放三类图表能力：

| capability_id | 功能 | 图表 | 主要限制 |
| --- | --- | --- | --- |
| `time_series_trend` | 单序列逐小时时间趋势 | 折线图 | 1 条序列，每条最多 744 点 |
| `time_series_compare` | 预测/实际或多站点逐小时对比 | 折线图 | 2～4 条序列，总计最多 2976 点 |
| `period_aggregate` | 单站点或多站点日总发电量 | 柱状图 | 1～4 条序列，每条最多 50 点 |

多站点对比只允许使用一种数据来源，即只比较实际数据或只比较预测数据；单站点可以进行预测与实际对比。

### 6. 数据库、性能与异常处理

#### 6.1 数据库与查询性能

- 数据库使用进程级 SQLAlchemy Engine 和连接池，查询通过短连接获取和归还连接。
- 会话列表按 `last_message_at` 倒序排列，使用游标分页代替大偏移量分页。
- 历史消息使用 `before_id` 游标分页，只查询当前需要的一页。
- 前后端分页大小统一读取 `shared/pagination.json`。
- 会话列表使用 `(user_id, last_message_at, session_id)` 联合索引。

#### 6.2 流式协议与异常处理

- `POST /api/chat/stream` 使用 SSE 返回 Agent 执行过程。
- 前端通过 Fetch + `ReadableStream` 消费 POST 流式响应。
- 普通 REST 接口使用 Axios，并与流式请求共享登录状态和错误协议。
- 同一个会话同时只执行一个 Agent 任务，重复提交返回 `SESSION_BUSY`。
- AskUser 按用户和会话路由，避免其他会话提交错误回答。
- 后端统一返回结构化异常：`code`、`message`、`status`、`retryable`、`details` 和 `request_id`。
- 前端根据异常码区分登录失效、参数错误、会话冲突、数据不存在、数据库异常和上游服务异常。
- 未处理异常只向前端返回安全提示，详细堆栈保留在后端日志中。

## 系统结构

```text
Vue 3 工作台
  ├── Axios：认证、会话、历史消息
  ├── Fetch：SSE 流式对话
  ├── MarkdownIt + DOMPurify
  └── ECharts
          |
          v
FastAPI
  ├── auth：注册、登录、刷新、退出
  ├── sessions：会话与历史消息
  └── chat：Agent、SSE、AskUser
          |
          v
LangChain Agent + 业务工具
  ├── 站点、地区、日期、气象
  ├── 实际发电量与预测
  ├── 图表、文件、表格、知识库
  └── 结构化站点上下文
          |
          v
MySQL
  ├── 用户和刷新会话
  ├── 会话、消息、摘要和上下文
  ├── 实际发电量、气象和预测缓存
  └── 图表快照
```

## 主要目录

```text
solar_agent/
├── backend/
│   ├── Agent/                 Agent、Prompt、LLM 和长期记忆
│   ├── app/
│   │   ├── charting/          图表注册表、校验器和 ChartService
│   │   ├── routers/           auth、chat、sessions 路由
│   │   └── services/          认证、摘要、站点解析和会话服务
│   ├── sql/                   初始化和数据库迁移脚本
│   ├── temp/                  文件读写测试与运行目录
│   └── tools/                 预测、查询、气象、图表和文件工具
├── frontend/
│   ├── src/api.js             REST 与 SSE 业务 API
│   ├── src/App.vue            当前工作台主页面
│   ├── src/router.js          登录路由守卫
│   ├── src/components/        AskUser、Markdown、ECharts 组件
│   └── src/views/LoginView.vue
├── shared/pagination.json     前后端共用分页大小
├── docs/                      架构、复盘和学习文档
└── requirements.txt
```

## 环境配置

项目根目录创建 `.env`，至少配置：

```env
API_KEY=your_api_key
BASE_URL=https://your-openai-compatible-endpoint/v1
MODEL_NAME=your_model_name

MYSQL_URL=mysql+pymysql://user:password@127.0.0.1:3306/solar_agent?charset=utf8mb4

JWT_SECRET_KEY=replace_with_at_least_32_characters
JWT_ACCESS_TOKEN_EXPIRE_SECONDS=900
JWT_REFRESH_TOKEN_EXPIRE_SECONDS=604800
JWT_REFRESH_COOKIE_SECURE=false

ASK_USER_TIMEOUT=120
FILE_DIR=D:/your/runtime/files
MODEL_SUNNY=D:/your/models/sunny
MODEL_CLOUDY=D:/your/models/cloudy
```

生产环境必须使用足够强度的 `JWT_SECRET_KEY`，并将 `JWT_REFRESH_COOKIE_SECURE` 设置为 `true`。不要提交真实密钥、数据库密码和模型服务凭证。

前端需要指定后端地址时，在 `frontend/.env.local` 中配置：

```env
VITE_API_BASE_URL=http://127.0.0.1:8001/api
```

## 安装与启动

### 后端

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --reload-dir backend --host 0.0.0.0 --port 8001
```

使用 `--reload-dir backend` 可以避免 Uvicorn 扫描前端依赖缓存目录。

后端启动后可访问：

- API 文档：`http://127.0.0.1:8001/docs`
- 服务地址：`http://127.0.0.1:8001/`

### 前端

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm install
npm run dev
```

Vite 默认访问地址为 `http://localhost:5173`。如果端口被占用，Vite 会自动尝试下一个可用端口。

## 主要接口

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| POST | `/api/auth/register` | 注册用户 |
| POST | `/api/auth/login` | 登录并创建刷新会话 |
| POST | `/api/auth/refresh` | 轮换 Refresh Token 并签发新 Access Token |
| POST | `/api/auth/logout` | 撤销刷新会话并清除 Cookie |
| POST | `/api/sessions` | 创建新会话 |
| GET | `/api/sessions` | 游标分页查询会话列表 |
| GET | `/api/sessions/{session_id}/messages` | 分页查询历史消息和图表快照 |
| PATCH | `/api/sessions/{session_id}` | 重命名会话 |
| DELETE | `/api/sessions/{session_id}` | 删除会话及关联记录 |
| POST | `/api/chat/stream` | SSE 流式 Agent 对话 |
| POST | `/api/chat` | 非流式兼容接口 |
| POST | `/api/chat/{session_id}/reply` | 回复 AskUser 问题 |

## 数据库初始化与迁移

首次部署空数据库时执行：

1. `backend/sql/init_schema.sql`
2. `backend/sql/cache_schema.sql`
3. `backend/sql/memory_schema.sql`

已有数据库按实际缺失结构执行对应迁移，不要重新执行包含 `DROP TABLE` 的初始化脚本：

| 文件 | 作用 |
| --- | --- |
| `001_phase1_memory_schema.sql` | 消息、摘要和用户隔离基础升级 |
| `002_incremental_summary.sql` | 增量摘要游标、版本和消息计数 |
| `002_prediction_weather_data_mode.sql` | 区分历史实况回测与未来预报缓存 |
| `003_chart_snapshot_store.sql` | 保存历史 ChartSpec 图表快照 |
| `004_chat_session.sql` | 会话标题和最后消息时间 |
| `005_session_cursor_pagination.sql` | 会话游标分页联合索引 |
| `006_refresh_token_sessions.sql` | 可撤销、可轮换的刷新会话表 |
| `006_session_context.sql` | 会话级结构化上下文 `context_json` |

迁移脚本设计为可重复执行，但操作真实数据库前仍应先备份。

## 测试

后端针对当前新增能力提供了回归测试：

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
& .\.venv\Scripts\python.exe -m pytest -q backend\tests
& .\.venv\Scripts\python.exe -m compileall -q backend
```

前端构建检查：

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm run build
```

测试覆盖图表注册能力、日期范围限制、结构化异常协议、Refresh Token、站点解析复用和地区站点范围。

## 当前限制与待完成

1. **前端文件上传**：后端已经可以从指定路径导入发电量文件，前端尚未实现文件选择、上传、预览和导入进度。
2. **生成文件下载**：后端能够生成文件，尚未提供完整的前端文件列表和安全下载接口。
3. **用户与站点权限**：目前只有基础用户角色字段，尚未完成系统管理员、运维人员和可访问站点范围的权限模型。
4. **站点仪表盘**：当前以自然语言工作台为主，尚未实现传统筛选条件式的数据查询页面。
5. **前端状态管理**：Access Token、用户和主要工作台状态尚未迁移到 Pinia。
6. **前端模块拆分**：`App.vue` 仍承担较多会话、认证和流式状态逻辑，需要继续拆分为页面、组件和组合式函数。
7. **Redis 缓存**：站点目录、会话列表和热点历史记录尚未接入 Redis；MySQL 仍是当前持久化数据源。
8. **历史会话性能**：已经完成游标分页和按页加载，后续还需增加请求缓存、图表按需加载和前端渲染优化。
9. **文件工具产品化**：当前文件导入、导出和验证能力主要供 Agent 与后端调用，尚未形成完整前端闭环。
10. **部署与可观测性**：尚未补齐 Docker、后台任务队列、指标监控、链路追踪和接口限流。

## 相关文档

- [图表能力架构](docs/图表能力.md)
- [站点结构化解析与会话级记忆复盘](docs/站点结构化解析与会话级记忆复盘.md)
- [性能优化记录](docs/性能优化记录.md)
- [JWT 刷新令牌与活跃续期](docs/JWT刷新令牌与活跃续期.md)
- [SSE 流式输出与会话锁](docs/SSE-流式输出-与-会话锁-设计亮点.md)
- [Vite 开发模式与生产模式性能差异](docs/Vite开发模式与生产模式性能差异.md)
- [项目全景评估与优化路线](docs/2026-07-16-项目全景评估与优化路线.md)

## 仓库地址

GitHub：<https://github.com/yke6519-droid/SolarAgent>
