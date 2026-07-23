# SolarAgent Backend

`backend/` 是 SolarAgent 的后端工程，包含 FastAPI 接口、LangChain Agent、光伏业务工具、统一数据制品、预测模型、数据库初始化脚本和回归测试。

> 当前后端只提供 API，不再内置 HTML 页面。请从项目根目录使用模块路径启动。

## 目录结构

```text
backend/
├── Agent/
│   ├── agent.py                组装 LLM、Prompt、Memory 和工具
│   ├── llm.py                  OpenAI 兼容模型客户端
│   ├── memory.py               MySQL 消息窗口和增量摘要
│   ├── prompt.py               Agent 系统约束
│   └── tools.py                当前真正注册给 Agent 的工具清单
├── app/
│   ├── charting/               ChartPlan、ChartSpec、注册表、校验器和 Builder
│   ├── dependencies/           FastAPI 认证依赖
│   ├── routers/                HTTP/SSE 路由
│   ├── schemas/                Pydantic 请求响应模型
│   ├── services/               业务服务与持久化边界
│   ├── config.py               应用与共享分页配置
│   ├── database.py             进程级 SQLAlchemy Engine/连接池
│   ├── errors.py               统一异常码和异常处理器
│   └── main.py                 FastAPI 应用工厂和入口
├── sql/
│   ├── init_schema.sql         站点与实际发电量基础表
│   ├── cache_schema.sql        气象与预测缓存表
│   ├── memory_schema.sql       当前会话相关表的完整建表脚本
│   └── migrations/             既有数据库增量迁移
├── tests/                      后端契约与回归测试
├── temp/                       文件读写测试及兼容运行目录
└── tools/                      Agent 可调用工具与预测底层逻辑
```

## 运行架构

```mermaid
flowchart LR
    FE["Vue 前端"] --> API["FastAPI Router"]
    API --> AUTH["认证与会话 Service"]
    API --> AG["AgentExecutor"]
    AG --> TOOL["LangChain Tools"]
    TOOL --> CTX["任务级 ContextVar"]
    TOOL --> DATA["DatasetArtifact / FileArtifact"]
    DATA --> DB["MySQL"]
    TOOL --> FS["FILE_DIR"]
    TOOL --> EXT["天气、知识库、模型服务"]
```

## FastAPI 路由

路由在 `backend/app/main.py` 中统一注册：

| 路由模块 | 前缀 | 主要接口 |
| --- | --- | --- |
| `auth.py` | `/api/auth` | 注册、登录、刷新、退出 |
| `sessions.py` | `/api` | 会话创建、列表、历史、重命名、删除 |
| `chat.py` | `/api` | 流式对话、非流式对话、AskUser 回复 |
| `attachments.py` | `/api/attachments` | 对话附件上传 |
| `files.py` | `/api/files` | Agent 生成文件鉴权下载 |
| `import_data.py` | `/api/import` | 发电量 Excel 预览与执行导入 |

应用还提供：

- `GET /`：服务信息。
- `GET /health`：健康检查。
- `GET /docs`：Swagger UI。

## Agent 与工具

Agent 使用 `create_tool_calling_agent` 和 `AgentExecutor`，最多执行 20 次迭代。当前工具注册表以 `backend/Agent/tools.py` 为准：

```text
站点：
  list_all_stations
  get_station_info
  get_station_location
  get_stations_by_region

日期和天气：
  get_current_datetime
  parse_date
  get_weather_by_range
  get_weather_records

发电量和预测：
  get_actual_power
  get_actual_power_by_range
  get_predicted_power
  get_power_comparison
  predict_power

图表：
  get_chart_capabilities
  get_power_dataset
  create_chart_plan

文件和表格：
  write_file
  read_file
  verify_file
  read_table
  export_table
  import_power_data

交互和检索：
  ask_user
  search_knowledge_base
```

`backend/tools/chart_tool.py` 是旧版直接生成图表 JSON 的实现，目前没有注册给 Agent。

## SSE 流式接口

`POST /api/chat/stream` 接收：

```json
{
  "session_id": "会话ID",
  "message": "用户原始任务",
  "attachments": [
    {"attachment_id": "att_xxx"}
  ]
}
```

单条消息最多携带 5 个附件。接口通过 `EventSourceResponse` 输出：

| 事件 | 作用 |
| --- | --- |
| `token` | Agent 文本增量 |
| `usage` | 本轮模型 Token 用量 |
| `tool_start` | 工具开始执行 |
| `tool_end` | 文本结果或文件卡片元数据 |
| `chart_spec` | 已校验的结构化图表协议 |
| `user_input_required` | AskUser 等待用户回复 |
| `error` | 结构化任务错误 |
| `done` | 任务完成、最终输出和聚合 Token 用量 |

同一个会话同时只允许一个 Agent 任务。执行过程中会分别绑定并在 `finally` 中释放：

- 图表上下文；
- 统一数据制品上下文；
- 站点解析上下文；
- 附件访问上下文；
- 原始用户消息上下文。

## 统一数据制品

`backend/app/services/dataset_artifact_service.py` 是查询、预测、天气、图表和导出之间的统一数据边界。

```text
业务工具
  → 标准化 DataFrame
  → create_dataset_artifact / register_power_frame
  → 保存进程内热缓存
  → 保存 dataset_artifact_store
  → 更新本轮 active_dataset
  → 对话成功后写入 chat_session.context_json
```

每个制品包含：

- `artifact_id`
- `artifact_type`
- `owner_user_id`
- `session_id`
- 字段 Schema 与语义角色
- 标准化行数据
- 业务元数据

`ChartService` 和 `export_table` 都会通过当前用户、会话和 `artifact_id` 读取制品，防止跨用户或跨会话复用。

## 图表管线

正式图表管线：

```text
get_chart_capabilities
  → get_power_dataset
  → DatasetArtifact
  → create_chart_plan
  → ChartService.resolve_chart_plan
  → Validator
  → Builder
  → ChartSpec
```

当前注册能力：

| 能力 | 模板 | 粒度 | 序列 |
| --- | --- | --- | --- |
| `time_series_trend` | `single_line` | `hourly` | 1 |
| `time_series_compare` | `multi_line` | `hourly` | 2～4 |
| `period_aggregate` | `aggregate_bar` | `daily_total` | 1～4 |

图表计划每轮最多提交 2 次。ChartSpec 最终保存到 `chart_snapshot_store`，用于历史消息恢复；图表快照不是表格导出的数据源。

## 附件与生成文件

### 对话附件

- 上传接口：`POST /api/attachments`。
- 支持：`.xlsx`、`.xls`、`.csv`、`.txt`、`.md`。
- 默认大小上限：20 MB，可用 `ATTACHMENT_MAX_UPLOAD_BYTES` 调整。
- 文件本体保存到 `FILE_DIR/uploads/{user_id}/{session_id}/`。
- MySQL 保存 `attachment_id`、归属、哈希、类型、大小、状态和真实存储 URI。
- Agent 只接触 `attachment_id`，真实路径由服务端完成归属校验后解析。

### Agent 生成文件

- `write_file` 与 `export_table` 成功落盘后调用文件产物服务登记。
- `file_artifact` 保存 `file_id`、用户、会话、消息、哈希、状态和真实路径。
- SSE 只发送文件卡片需要的安全字段。
- `GET /api/files/{file_id}/download` 校验用户归属后返回 `FileResponse`。
- 文件产物绑定助手消息，历史消息接口可以恢复下载卡片。

## 会话记忆

`backend/Agent/memory.py` 使用 MySQL 作为在线事实源：

1. 每轮只查询最近 `2 * k` 条消息。
2. 读取 `agent_summary_store` 中的历史摘要。
3. 将摘要放在前面、最近原始消息放在后面交给 Agent。
4. 回答完成后由 `SummaryTaskManager` 在线程中异步执行增量摘要。
5. 摘要只处理游标后、最近窗口前的消息。
6. 使用 `summary_version` 和游标进行乐观并发控制。

`chat_session.context_json` 保存跨轮次的轻量结构化状态，包括 `active_station`、`active_attachment`、`active_dataset` 和 `active_chart`。

## 数据库连接

`backend/app/database.py` 懒加载一个进程级 SQLAlchemy Engine：

```python
with get_engine().connect() as conn:
    ...

with get_engine().begin() as conn:
    ...
```

查询或事务结束后连接归还连接池，不会为每次业务调用重复创建 Engine。配置启用了 `pool_pre_ping=True` 和 `pool_recycle=1800`。

## 环境变量

项目根目录 `.env`：

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

## 安装与启动

从项目根目录执行：

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --reload-dir backend --host 0.0.0.0 --port 8001
```

不要进入 `backend/app` 后执行 `python main.py`，否则 Python 无法从项目根包解析 `backend.app.*`。

## 数据库脚本

空库初始化：

```text
backend/sql/init_schema.sql
backend/sql/cache_schema.sql
backend/sql/memory_schema.sql
```

已有数据库增量迁移位于 `backend/sql/migrations/`，当前最新迁移为：

```text
009_file_attachment_store.sql
010_file_artifact_store.sql
011_file_artifact_message_link.sql
012_dataset_artifact_store.sql
```

执行真实数据库迁移前必须先备份，并按当前线上 DDL 判断需要执行的脚本。

## 测试

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent
& .\.venv\Scripts\python.exe -m pytest -q backend\tests
& .\.venv\Scripts\python.exe -m compileall -q backend
```

测试目录当前覆盖：

- 图表注册、Builder、校验和范围限制；
- 统一异常协议；
- Refresh Token；
- 站点结构化解析和地区范围；
- 数据制品上下文；
- 文件产物 SSE 与历史安全清洗；
- 数据制品表格导出。

## 当前后端限制

- 文件本体仍使用本地 `FILE_DIR`，尚未迁移 OSS。
- Redis 尚未接入，热点站点目录、会话和历史仍直接读取 MySQL。
- 缺少完整 RBAC 和站点级数据权限。
- 图表只开放三种注册能力。
- 尚未实现由多个已有 `artifact_id` 直接组合派生新数据制品。
- 旧 `chart_tool.py` 和独立定时预测脚本仍在仓库，但不属于当前 Agent 主链路。
