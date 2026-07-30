# SolarAgent

SolarAgent 是一个面向光伏电站运营场景的智能分析 Agent。系统通过自然语言理解用户需求，调用站点、气象、实际发电量、预测、文件和图表工具，完成数据查询、发电预测、历史回测、可视化分析、文件导入与结果导出。

项目已经完成前后端分离：

- `backend/`：FastAPI、LangChain Agent、业务工具、预测模型与 MySQL 持久化。
- `frontend/`：Vue 3 + Vite 独立工作台。
- `shared/`：前后端共享配置。
- `docs/`：架构设计、功能复盘和性能优化记录。

> 本 README 按 2026-07-24 当前源码整理。旧版 `chart_tool.py` 仍保留在仓库中，但已经不在 Agent 工具注册表中；正式图表链路以 `get_chart_capabilities → get_power_dataset → create_chart_plan` 为准。

## 一、技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、Ant Design Vue、Pinia、Axios、Fetch、ECharts、MarkdownIt、DOMPurify |
| 后端 | FastAPI、SSE、LangChain、Pydantic |
| 数据库 | MySQL、SQLAlchemy、PyMySQL |
| 认证 | JWT Access Token、HttpOnly Refresh Cookie、Argon2id |
| 数据处理 | Pandas、NumPy、OpenPyXL、xlrd |
| 预测 | TensorFlow/Keras、LightGBM、XGBoost、Scikit-learn |

## 二、当前核心闭环

### 2.1 对话业务闭环

```mermaid
flowchart LR
    U["用户登录"] --> H["进入欢迎页"]
    H --> S["发送自然语言任务"]
    S --> A["Agent 拆分步骤并调用工具"]
    A --> Q{"是否需要用户确认"}
    Q -- "是" --> C["AskUser 卡片"]
    C --> A
    Q -- "否" --> R["流式返回文字、过程和结构化结果"]
    R --> P["图表或文件卡片"]
    P --> F["继续追问、导出或下载"]
```

### 2.2 统一数据制品闭环

实际发电量、预测结果、天气和对比结果不再只作为一段工具文本存在。相关工具会把结构化结果登记为 `DatasetArtifact`，保存到 MySQL，并在会话上下文中只保留轻量引用。

```mermaid
flowchart LR
    T["查询 / 预测 / 天气工具"] --> D["标准化 DataFrame"]
    D --> A["DatasetArtifact"]
    A --> M["dataset_artifact_store"]
    A --> C["当前任务 DatasetContext"]
    C --> SC["会话 active_dataset 引用"]
    A --> CH["ChartService"]
    A --> EX["export_table"]
```

这使“先查询或预测，下一轮再画图/导出刚才的数据”能够复用同一份数据事实，而不是依赖 Agent 从文字中重新拼接数组。

### 2.3 结构化图表闭环

```text
get_chart_capabilities
        → get_power_dataset
        → DatasetArtifact
        → create_chart_plan
        → ChartService 校验和编译
        → ChartSpec
        → SSE chart_spec
        → Vue + ECharts
```

Agent 负责选择已注册能力和提交受限 `ChartPlan`，后端负责验证字段角色、粒度、序列数、点数和数据归属，前端只渲染可信 `ChartSpec`。Agent 不能向前端注入 JavaScript、HTML、ECharts formatter 或任意图表配置。

### 2.4 文件上传与下载闭环

项目中有两类上传入口：

1. **数据导入弹窗**：上传 `.xlsx` / `.xls`，先预览清洗结果，再由用户确认写入 MySQL。
2. **对话附件**：在输入框通过加号上传 `.xlsx`、`.xls`、`.csv`、`.txt`、`.md`，得到不透明的 `attachment_id`，再由 Agent 选择现有读取、校验或导入工具。

Agent 生成文件后，后端登记 `file_id` 并通过 SSE 返回安全元数据。前端展示文件卡片，用户点击卡片后由 Axios 携带 Bearer Token 下载 Blob。生成文件会绑定到助手消息，重新打开历史会话时文件卡片可以恢复。

```mermaid
flowchart LR
    U["Agent 生成文件"] --> R["登记 file_artifact"]
    R --> E["SSE 返回 file_id 和安全元数据"]
    E --> C["前端文件卡片"]
    C --> X["Axios + Bearer Token"]
    X --> API["GET /api/files/{file_id}/download"]
    API --> V["校验用户归属与文件状态"]
    V --> B["返回文件 Blob"]
```

Agent 输出文本中的服务器路径和 `/api/files/...` 下载链接会被后端及前端清洗，文件卡片是唯一下载入口。

## 三、已实现功能

### 3.1 前端工作台

#### 登录、路由与主题

- 独立 `/login` 页面和 `/workspace` 工作台。
- 未持有有效登录态时，路由守卫会回到登录页。
- Access Token 过期且无法续期时展示“重新登录”弹窗。
- 支持浅色、深色主题。

#### 会话管理

- 进入工作台默认展示欢迎页，不自动选中最近会话。
- “新建会话”实际行为为回到首页；只有第一次发送消息时才创建会话。
- 首页三张快捷任务卡片只把短语填入输入框，不自动发送。
- 会话列表支持游标分页、选择、重命名和删除。
- 会话重命名使用独立的 `RenameSessionDialog` 组件，统一处理输入校验、字符数限制和提交状态。
- 历史消息使用 `before_id` 游标向上分页，并在加载旧消息时保持滚动位置。
- 同一会话、同一游标的前端历史请求会共享同一个 Promise，避免重复请求。

#### 前端组件化与状态管理

- 页面级布局拆分为 `WorkspaceLayout`、`AppHeader`、`SessionSidebar` 和会话内容组件。
- 对话消息、输入框、确认卡片、图表、附件、导入弹窗和重命名弹窗均使用独立 Vue 组件。
- 认证状态和会话基础状态由 Pinia store 管理；流式对话、会话历史、附件和导入流程由 composables 承担。
- `App.vue` 负责页面级状态编排和跨组件事件协调，不再直接承载所有业务实现。

#### 流式交互

- `POST /api/chat/stream` 使用 SSE。
- 前端通过 Fetch + `ReadableStream` 消费 POST 流式响应。
- 支持 `token`、`usage`、`tool_start`、`tool_end`、`chart_spec`、`user_input_required`、`error`、`done` 等事件。
- Agent 文本按流式 token 增量展示，执行轨迹展示思考、工具调用、等待确认和完成状态。
- 用户可以停止当前流式任务。
- 每轮助手回答下方展示输入、输出和总 Token 用量；历史消息重新加载后仍可恢复。

#### 内容、图表与文件

- Markdown 使用 MarkdownIt 渲染，DOMPurify 清洗危险 HTML 和事件属性。
- ECharts 图表嵌入对话流，支持 Tooltip、浅/深色主题和历史图表恢复。
- 对话输入框支持上传附件，单条消息最多绑定 5 个附件。
- Agent 生成文件通过文件卡片下载，卡片可随历史消息恢复。
- 独立“导入数据”弹窗支持 Excel 预览和确认入库。

### 3.2 用户认证与访问控制

- 后端支持注册、登录、刷新和退出登录。
- 密码使用 Argon2id 哈希保存。
- Access Token 以 JWT Bearer Token 访问业务接口。
- Refresh Token 保存于 HttpOnly Cookie，服务端只保存哈希。
- Refresh Token 支持轮换、撤销和绝对过期。
- 普通 Axios 请求遇到 401 后会尝试刷新并重放一次。
- SSE 请求遇到 401 后会尝试刷新并重新建立连接一次。
- 前端只在用户近期有键盘、鼠标、触摸、重新聚焦等活动时主动续期。
- 用户、会话、消息、摘要、附件、数据制品、图表和生成文件均按 `user_id` 或会话归属校验。

### 3.3 Agent 编排与记忆

#### Agent 工具

当前真正注册给 Agent 的工具位于 `backend/Agent/tools.py`：

| 类别 | 工具 |
| --- | --- |
| 站点 | `list_all_stations`、`get_station_info`、`get_station_location`、`get_stations_by_region` |
| 日期 | `get_current_datetime`、`parse_date` |
| 天气 | `get_weather_by_range`、`get_weather_records` |
| 发电预测 | `predict_power` |
| 发电量 | `get_actual_power`、`get_actual_power_by_range`、`get_predicted_power`、`get_power_comparison` |
| 文件 | `write_file`、`read_file`、`verify_file` |
| 表格 | `export_table`、`read_table` |
| 附件导入 | `import_power_data` |
| 图表 | `get_chart_capabilities`、`get_power_dataset`、`create_chart_plan` |
| 其他 | `search_knowledge_base`、`ask_user` |

Prompt 要求 Agent 先在内部拆分目标、站点/地区、日期、数据来源和输出形式，再逐步调用工具。对于“查询全部已接入站点”这种明确事实请求，流式与非流式接口还会检查是否真实调用了 `list_all_stations`，避免模型直接幻想站点清单。

#### 长期记忆

- 会话和 LangChain 消息持久化到 MySQL。
- Agent 每次只加载最近 `k` 轮原始消息和历史摘要。
- 摘要按消息游标增量生成，只处理已离开最近窗口且尚未摘要的消息。
- 摘要在回答完成后异步执行，不阻塞主响应。
- `summary_version` 和摘要游标用于并发更新保护。

#### 结构化上下文

一次 Agent 执行会绑定彼此隔离的临时上下文：

- `StationResolutionContext`：复用本轮已解析的结构化站点对象。
- `ChartExecutionContext`：记录图表能力预检和计划重试次数。
- `DatasetExecutionContext`：登记本轮产生的数据制品及当前 `active_dataset`。
- `AttachmentExecutionContext`：限定本轮可以访问的附件 ID。

跨轮次需要保留的信息写入 `chat_session.context_json`。当前会保存 `active_station`、`active_attachment`、`active_dataset` 和 `active_chart` 等轻量引用，原始大数据保存在各自事实表中。

### 3.4 站点、天气与发电业务

- 查询系统全部有效站点。
- 按简称、关键词或站点 ID 查询名称、位置、经纬度和装机容量。
- 按省、市、地区、地址或站点名称模糊查询区域站点。
- 地区查询最多返回 20 个站点，超过范围时要求缩小条件。
- 查询单日或日期范围实际发电量。
- 查询预测缓存。
- 拉取历史实况天气和未来预报天气。
- 执行单站点 24 小时光伏发电预测。
- 预测工具内部确认最终站点和标准日期。
- 历史日期使用历史气象进行回测，未来日期使用天气预报。
- 对比预测与实际结果并计算偏差、MAE、RMSE、MAPE 等指标。
- 预测结果、天气和实际数据均可登记为统一数据制品。

### 3.5 图表能力注册表

当前只开放三类受控能力：

| `capability_id` | 场景 | 图表 | 主要限制 |
| --- | --- | --- | --- |
| `time_series_trend` | 单序列逐小时时间趋势 | 折线图 | 1 条序列，最多 744 点 |
| `time_series_compare` | 单站预测/实际或多站点单一来源逐小时对比 | 折线图 | 2～4 条序列，每条最多 744 点，总计最多 2976 点 |
| `period_aggregate` | 单站或多站日总发电量 | 柱状图 | 1～4 条序列，每条最多 50 点，总计最多 200 点 |

多站点逐小时对比只允许一种数据来源；单站点可以比较预测与实际。未注册的饼图、雷达图、地图和任意混合图当前不会被执行。

### 3.6 数据导入、数据制品与文件产物

- 发电量 Excel 导入支持预览、清洗、站点解析和批量入库。
- 对话附件使用 `attachment_id`，Agent 不接触服务器真实路径。
- `read_table` 会遍历 Excel 的所有 Sheet 并生成工作簿摘要。
- `export_table` 优先从 `artifact_id` 或会话 `active_dataset` 导出，可支持单站、多站、单序列和多序列数据。
- 多序列长表在导出前会按数据制品元数据转为更适合用户阅读的宽表。
- 生成文件登记为 `file_artifact`，只向前端暴露 `file_id`、文件名、类型、大小和状态。
- 文件下载接口校验当前用户归属，不允许通过裸链接绕过认证。

### 3.7 性能与异常处理

- 后端使用进程级 SQLAlchemy Engine 和连接池。
- 会话列表按 `(user_id, last_message_at, session_id)` 联合索引进行 Keyset/游标分页。
- 历史消息使用 `before_id`，避免一次读取整个会话。
- 前后端分页大小统一读取 `shared/pagination.json`，当前为每页 5 个会话、6 条消息。
- 同一会话同时只运行一个 Agent 任务，冲突返回 `SESSION_BUSY`。
- 统一异常响应包含 `code`、`message`、`status`、`retryable`、`details` 和 `request_id`。
- 前端按异常码区分认证失效、参数错误、会话冲突、数据不存在、图表限制、数据库故障和上游错误。

## 四、系统架构

```mermaid
flowchart TB
    FE["Vue 3 工作台"]
    REST["Axios REST<br/>认证、会话、上传、下载"]
    SSE["Fetch + ReadableStream<br/>SSE 对话"]
    API["FastAPI 路由层"]
    AG["LangChain AgentExecutor"]
    TOOL["光伏业务工具"]
    CTX["执行上下文<br/>站点 / 图表 / 数据制品 / 附件"]
    DB["MySQL"]
    FS["FILE_DIR 本地文件存储"]
    MODEL["预测模型与外部天气/知识库服务"]

    FE --> REST
    FE --> SSE
    REST --> API
    SSE --> API
    API --> AG
    AG --> TOOL
    API --> CTX
    TOOL --> CTX
    TOOL --> DB
    TOOL --> FS
    TOOL --> MODEL
    CTX --> DB
```

## 五、主要目录

```text
solar_agent/
├── backend/
│   ├── Agent/                         Agent、Prompt、LLM、Memory、工具注册表
│   ├── app/
│   │   ├── charting/                  图表协议、注册表、校验器、Builder、ChartService
│   │   ├── dependencies/              FastAPI 认证依赖
│   │   ├── routers/                   auth、chat、sessions、attachments、files、import
│   │   ├── schemas/                   请求与响应模型
│   │   └── services/                  认证、上下文、附件、数据制品、文件产物等服务
│   ├── sql/                           初始化脚本和增量迁移
│   ├── tests/                         后端契约与回归测试
│   ├── temp/                          文件读写测试及兼容运行目录
│   └── tools/                         站点、天气、发电、预测、图表、文件与导入工具
├── frontend/
│   ├── src/app/AppProvider.vue        Ant Design Vue 配置与全局主题
│   ├── src/components/                对话、布局、图表、Markdown、附件、导入组件
│   │   ├── conversation/              对话头部、消息流、输入框、重命名弹窗
│   │   └── layout/                    顶部栏、会话侧栏
│   ├── src/composables/               流式对话、历史、认证、附件、导入流程
│   ├── src/layouts/WorkspaceLayout.vue 工作台页面布局
│   ├── src/stores/                    Pinia 认证与会话状态
│   ├── src/utils/                     消息解析与展示辅助函数
│   ├── src/views/                     登录页与欢迎页
│   ├── src/api.js                     REST、Blob 下载与 SSE 客户端
│   ├── src/App.vue                    页面级状态编排与主入口
│   └── src/router.js                  轻量路由与登录守卫
├── shared/pagination.json             前后端共享分页大小
├── docs/                              设计、复盘与学习文档
├── requirements.txt
└── README.md
```

## 六、环境配置

建议使用 Python 3.11。项目根目录创建 `.env`，至少配置：

```env
API_KEY=your_model_api_key
BASE_URL=https://your-openai-compatible-endpoint/v1
MODEL_NAME=your_model_name

MYSQL_URL=mysql+pymysql://user:password@127.0.0.1:3306/solar_agent?charset=utf8mb4

JWT_SECRET_KEY=replace_with_at_least_32_characters
JWT_ACCESS_TOKEN_EXPIRE_SECONDS=900
JWT_REFRESH_TOKEN_EXPIRE_SECONDS=604800
JWT_REFRESH_COOKIE_SECURE=false

ASK_USER_TIMEOUT=120
FILE_DIR=D:/your/runtime/files
ATTACHMENT_MAX_UPLOAD_BYTES=20971520
IMPORT_MAX_UPLOAD_BYTES=20971520

MODEL_SUNNY=D:/your/models/sunny
MODEL_CLOUDY=D:/your/models/cloudy

ALIBABA_CLOUD_ACCESS_KEY_ID=your_access_key_id
ALIBABA_CLOUD_ACCESS_KEY_SECRET=your_access_key_secret
WORKSPACE_ID=your_workspace_id
BAILIAN_INDEX_ID=your_index_id
BAILIAN_ENDPOINT=bailian.cn-beijing.aliyuncs.com
```

生产环境必须：

- 使用足够强度的 `JWT_SECRET_KEY`。
- 将 `JWT_REFRESH_COOKIE_SECURE=true`。
- 通过 HTTPS 和反向代理暴露前后端。
- 不提交真实密钥、数据库密码、模型路径和云服务凭证。

前端开发模式默认通过 Vite 代理 `/api` 到 `http://127.0.0.1:8001`。如果前后端不在同一源，可在构建前配置：

```env
VITE_API_BASE_URL=http://127.0.0.1:8001/api
```

## 七、安装与启动

### 7.1 后端

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --reload-dir backend --host 0.0.0.0 --port 8001
```

必须从项目根目录使用模块路径启动。不要在 `backend/app/` 中直接执行 `python main.py`。

`--reload-dir backend` 可以避免 Uvicorn 扫描 `frontend/node_modules`、`.pnpm-store` 等前端目录。

启动后：

- API：`http://127.0.0.1:8001/`
- 健康检查：`http://127.0.0.1:8001/health`
- Swagger：`http://127.0.0.1:8001/docs`

### 7.2 前端开发模式

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm install
npm run dev
```

开发地址固定为 `http://localhost:5173`。如果 5173 已被占用，Vite 会选择其他可用端口；需要固定端口时应先释放占用进程。

### 7.3 前端生产构建

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm run build
npm run preview
```

`vite preview` 默认使用 4173，但它不是生产部署服务器。正式部署时应让 Nginx 等静态服务器托管 `frontend/dist`，并将 `/api` 反向代理到 FastAPI；或者在构建时提供完整的 `VITE_API_BASE_URL`。

## 八、主要接口

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/` | API 基本信息 |
| GET | `/health` | 健康检查 |
| POST | `/api/auth/register` | 注册用户 |
| POST | `/api/auth/login` | 登录并创建 Refresh 会话 |
| POST | `/api/auth/refresh` | 轮换 Refresh Token 并签发新 Access Token |
| POST | `/api/auth/logout` | 撤销 Refresh 会话并清除 Cookie |
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions` | 游标分页查询会话列表 |
| GET | `/api/sessions/{session_id}/messages` | `before_id` 分页查询历史消息、图表、文件和 Token 用量 |
| PATCH | `/api/sessions/{session_id}` | 重命名会话 |
| DELETE | `/api/sessions/{session_id}` | 删除会话、消息、摘要和图表快照 |
| POST | `/api/chat/stream` | SSE 流式 Agent 对话 |
| POST | `/api/chat/{session_id}/reply` | 回复 AskUser 问题 |
| POST | `/api/attachments` | 上传并绑定对话附件 |
| GET | `/api/files/{file_id}/download` | 鉴权下载 Agent 生成文件 |
| POST | `/api/import/power/preview` | 预览和清洗发电量 Excel |
| POST | `/api/import/power/execute` | 确认后将发电量写入 MySQL |

## 九、数据库初始化与迁移

空数据库按顺序执行：

1. `backend/sql/init_schema.sql`
2. `backend/sql/cache_schema.sql`
3. `backend/sql/memory_schema.sql`

已有数据库只执行缺失的增量迁移，执行前先备份：

| 文件 | 作用 |
| --- | --- |
| `001_phase1_memory_schema.sql` | 用户隔离的消息与摘要基础结构 |
| `002_incremental_summary.sql` | 摘要游标、版本和消息计数 |
| `002_prediction_weather_data_mode.sql` | 预测缓存的气象数据模式与逻辑状态 |
| `003_chart_snapshot_store.sql` | 历史 ChartSpec 快照 |
| `004_chat_session.sql` | 会话标题与最后消息时间 |
| `005_session_cursor_pagination.sql` | 会话 Keyset 分页索引 |
| `006_refresh_token_sessions.sql` | 可撤销、可轮换的 Refresh 会话 |
| `006_session_context.sql` | 会话级结构化 `context_json` |
| `007_weather_analysis_fields.sql` | 天气分析所需字段 |
| `008_remove_weather_code.sql` | 移除不再使用的天气编码字段 |
| `009_file_attachment_store.sql` | 对话附件元数据 |
| `010_file_artifact_store.sql` | Agent 生成文件元数据 |
| `011_file_artifact_message_link.sql` | 生成文件与助手消息关联 |
| `012_dataset_artifact_store.sql` | 统一结构化数据制品持久化 |

迁移脚本以可重复执行为目标，但初始化脚本和真实生产表结构仍应根据实际数据库状态审查后执行。

## 十、测试与检查

后端：

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
& .\.venv\Scripts\python.exe -m pytest -q backend\tests
& .\.venv\Scripts\python.exe -m compileall -q backend
```

前端：

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm run build
```

当前测试覆盖图表注册和限制、统一异常、Refresh Token、站点解析/地区范围、数据制品上下文、文件产物事件、表格导出和历史消息安全清洗。

## 十一、当前限制与待完成

1. **用户与站点权限**：已有基础角色字段，但系统管理员、站点运维人员和可访问站点范围尚未形成完整 RBAC。
2. **站点仪表盘**：当前核心入口仍是自然语言工作台，尚未提供传统筛选条件式原始数据仪表盘。
3. **前端状态管理**：Pinia 已用于认证和会话基础状态；流式任务状态仍由页面级 composable 与 `App.vue` 协同管理，后续可继续收敛为更明确的任务状态模型。
4. **前端模块拆分**：核心页面、对话组件和业务 composables 已完成拆分；`App.vue` 仍承担页面级编排，后续可继续拆为 workspace controller 或更细粒度 composables。
5. **附件存储**：当前文件本体保存在本地 `FILE_DIR`；尚未迁移到阿里云 OSS 等对象存储。
6. **Redis 缓存**：站点目录、会话列表和热点历史尚未接入 Redis；MySQL 是当前事实源。
7. **历史会话性能**：已经完成游标分页、请求去重和有限消息加载，后续仍可做会话缓存、图表按需加载和虚拟列表。
8. **图表能力范围**：当前只开放三类折线/柱状能力，未开放饼图、雷达图、地图、组合图和任意数据变换。
9. **图表数据组合**：预测/实际对比可以通过 `get_power_dataset` 重新读取两种数据源生成统一长表；尚未实现直接把两个已有 `artifact_id` 组合成派生数据制品。
10. **附件预览体验**：后端和工具可读取多 Sheet 工作簿，但前端尚未提供通用的多 Sheet 表格浏览器。
11. **部署与可观测性**：尚未补齐 Docker、任务队列、指标监控、链路追踪、接口限流和集中日志。
12. **旧代码收口**：`chart_tool.py` 与独立旧定时预测脚本仍保留在仓库中，但不属于当前 Agent 主链路。

## 十二、相关文档

- [统一数据制品链路](docs/数据制品统一链路与跨模块复用.md)
- [图表能力架构](docs/图表能力.md)
- [文件上传功能与泛化设计](docs/文件上传功能实现与泛化设计复盘.md)
- [文件下载与 Blob 机制](docs/文件下载功能与Blob机制.md)
- [流式对话与上下文记忆](docs/流式对话接口与上下文记忆机制.md)
- [站点结构化解析与会话记忆](docs/站点结构化解析与会话级记忆复盘.md)
- [性能优化记录](docs/性能优化记录.md)
- [JWT 刷新令牌与活跃续期](docs/JWT刷新令牌与活跃续期.md)
- [SSE 流式输出与会话锁](docs/SSE-流式输出-与-会话锁-设计亮点.md)
- [Vite 开发与生产模式差异](docs/Vite开发模式与生产模式性能差异.md)
- [项目全景评估与优化路线](docs/2026-07-16-项目全景评估与优化路线.md)

## 十三、仓库地址

GitHub：<https://github.com/yke6519-droid/SolarAgent>
