# SolarAgent 后端架构详解 — 从单 Agent 到 FastAPI Web 服务

> **写给 Python 后端小白**：本文假设你懂一点 Python 语法，了解 Java Spring 的基本概念（Controller、Service、DTO），但不熟悉 FastAPI 和异步编程。每个概念都会用 Spring 类比 + 代码示例来讲。

---

## 目录

1. [项目全貌：从 CLI 到 Web 发生了什么](#1-项目全貌从-cli-到-web-发生了什么)
2. [目录结构与分层架构](#2-目录结构与分层架构)
3. [FastAPI 核心概念速查（对比 Spring）](#3-fastapi-核心概念速查对比-spring)
4. [入口文件 main.py 详解](#4-入口文件-mainpy-详解)
5. [路由层 routers 详解](#5-路由层-routers-详解)
6. [数据模型层 schemas 详解](#6-数据模型层-schemas-详解)
7. [服务层 services 详解](#7-服务层-services-详解)
8. [Agent 核心层详解](#8-agent-核心层详解)
9. [SSE 流式对话完整流程（带代码走读）](#9-sse-流式对话完整流程带代码走读)
10. [ask_user 交互桥接原理（最难的部分）](#10-ask_user-交互桥接原理最难的部分)
11. [用户隔离与安全校验](#11-用户隔离与安全校验)
12. [一个请求的完整生命周期（时序图）](#12-一个请求的完整生命周期时序图)
13. [改造前后对比：CLI vs Web](#13-改造前后对比cli-vs-web)
14. [常见问题 FAQ](#14-常见问题-faq)

---

## 1. 项目全貌：从 CLI 到 Web 发生了什么

### 1.1 改造前的样子（CLI 模式）

改造前，你的项目只有一个 `backend/Agent/run.py`，通过命令行跟 Agent 对话：

```
用户在终端打字 → input() → AgentExecutor.invoke() → print() → 终端显示
```

```python
# backend/Agent/run.py — 改造前的核心逻辑（简化版）
agent = build_agent(session_id="user_02", use_db=True)

while True:
    user_input = input("你: ")              # ① 阻塞等用户输入
    result = agent.invoke({"input": user_input})  # ② 调用 Agent
    print(f"助手: {result['output']}")      # ③ 打印结果
    agent.memory.maybe_summarize()          # ④ 异步摘要
```

**问题**：
- 只能一个人用，开一个终端就是全部
- `input()` 是阻塞的，Web 场景下不能用
- 没有用户系统，所有人共享一个 session
- 没有前端界面，用户体验差

### 1.2 改造后的样子（Web 模式）

改造后，加了一层 FastAPI Web 服务，变成：

```
浏览器 → HTTP 请求 → FastAPI 路由 → AgentManager → AgentExecutor → SSE 流式返回 → 浏览器逐字显示
```

```
用户在浏览器输入 → POST /api/chat/stream → FastAPI 路由函数
  → 校验用户权限
  → 获取 Agent 实例
  → 启动 Agent 异步执行
  → SSE 逐 token 推送到浏览器
  → 浏览器实时显示
```

**用 Spring 的话说**：就是给原来的业务逻辑套了一层 Spring MVC 外壳。原来 `main()` 里的 `while True` 循环，变成了 HTTP 接口；`input()` 变成了 HTTP Request Body；`print()` 变成了 SSE Event。

### 1.3 改造的核心思路

```
┌─────────────────────────────────────────────────────────┐
│                    改造前（CLI）                          │
│                                                         │
│  run.py                                                 │
│    ├── build_agent()  → AgentExecutor                   │
│    ├── input()         → 获取用户输入                    │
│    ├── agent.invoke()  → 执行 Agent                     │
│    └── print()         → 输出结果                        │
│                                                         │
├─────────────────────────────────────────────────────────┤
│                    改造后（Web）                          │
│                                                         │
│  backend/app/main.py           → FastAPI 应用入口                │
│  backend/app/routers/          → HTTP 路由（替代 input/print）    │
│  backend/app/schemas/          → 请求/响应数据模型（替代裸 dict）  │
│  backend/app/services/         → 业务逻辑层（管理 Agent 生命周期）│
│  backend/Agent/                → 原有 Agent 核心代码（几乎不变）  │
│  backend/tools/     → 原有工具集（完全不变）           │
└─────────────────────────────────────────────────────────┘
```

**关键点**：原有的 `backend/Agent/` 和 `backend/tools/` 代码几乎没改，只是在它们外面套了一层 Web 外壳。这就像 Spring 中，你的 `@Service` 业务逻辑不变，只是加了 `@RestController` 来暴露 HTTP 接口。

---

## 2. 目录结构与分层架构

### 2.1 目录树

```
solar_agent/
├── backend/app/                        # ★ 新增：FastAPI Web 应用
│   ├── main.py                 # 应用入口（≈ SpringBootApplication）
│   ├── config.py               # 配置（≈ application.properties）
│   ├── routers/                # 路由层（≈ @RestController）
│   │   ├── auth.py             #   认证路由（注册/登录）
│   │   ├── chat.py             #   对话路由（SSE 流式 + ask_user）
│   │   └── sessions.py         #   会话管理路由（增删查）
│   ├── schemas/                # 数据模型（≈ DTO/VO）
│   │   ├── chat.py             #   对话请求/响应模型
│   │   └── user.py             #   用户请求/响应模型
│   └── services/               # 服务层（≈ @Service）
│       ├── agent_manager.py    #   Agent 实例缓存 + Bridge 管理
│       ├── ask_user_bridge.py  #   同步↔异步桥接器
│       └── user_service.py     #   用户注册/登录
│
├── backend/Agent/                      # ★ 原有：Agent 核心逻辑
│   ├── agent.py                #   build_agent() 组装 AgentExecutor
│   ├── llm.py                  #   LLM 构建器（阿里云百炼 qwen-plus）
│   ├── memory.py               #   记忆管理（窗口+摘要+MySQL）
│   ├── prompt.py               #   系统提示词
│   ├── tools.py                #   工具注册
│   └── run.py                  #   CLI 入口（改造前的主入口）
│
├── backend/tools/           # ★ 原有：工具集（18 个工具）
│   ├── ask_user_tool.py        #   用户交互工具
│   ├── power_query_tool.py     #   发电量查询
│   ├── table_io_tool.py        #   表格导入导出
│   ├── weather_fetcher_tool.py #   气象数据获取
│   ├── ... (共 12 个工具文件)
│
├── .env                        # 环境变量配置
└── backend/sql/                        # 数据库建表脚本
```

### 2.2 分层架构图

```
┌──────────────────────────────────────────────────────────┐
│                      浏览器前端                            │
│            (内置在 main.py 的 TEST_HTML)                  │
└──────────────────────┬───────────────────────────────────┘
                       │ HTTP / SSE
┌──────────────────────▼───────────────────────────────────┐
│  backend/app/main.py        FastAPI 应用入口                       │
│                     (≈ @SpringBootApplication)             │
│                     - 创建 FastAPI 实例                     │
│                     - 注册 CORS 中间件                     │
│                     - 注册路由                              │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│  backend/app/routers/       路由层 (≈ @RestController)             │
│  ┌──────────────────────────────────────────────────┐    │
│  │ auth.py      POST /api/auth/login   登录          │    │
│  │              POST /api/auth/register 注册         │    │
│  │ sessions.py  POST /api/sessions     创建会话      │    │
│  │              GET  /api/sessions      查询会话列表  │    │
│  │              DELETE /api/sessions/{id} 删除会话    │    │
│  │              GET  /api/sessions/{id}/messages     │    │
│  │ chat.py      POST /api/chat/stream  SSE 流式对话   │    │
│  │              POST /api/chat/{id}/reply 回复问题   │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│  backend/app/schemas/       数据模型层 (≈ DTO / VO)                │
│  ┌──────────────────────────────────────────────────┐    │
│  │ chat.py  ChatRequest  {session_id, message, ...} │    │
│  │          ReplyRequest {answer}                    │    │
│  │ user.py  LoginRequest {username, password}        │    │
│  │          UserResponse {user_id, username, role}   │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│  backend/app/services/      服务层 (≈ @Service)                    │
│  ┌──────────────────────────────────────────────────┐    │
│  │ agent_manager.py  管理 Agent 实例缓存和 Bridge     │    │
│  │ ask_user_bridge.py 同步↔异步线程桥接              │    │
│  │ user_service.py    用户注册/登录/密码校验          │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│  backend/Agent/             Agent 核心层（原有代码，几乎不变）      │
│  ┌──────────────────────────────────────────────────┐    │
│  │ agent.py    build_agent() 组装 AgentExecutor       │    │
│  │ llm.py      ChatOpenAI (阿里云百炼 qwen-plus)     │    │
│  │ memory.py   PersistentWindowSummaryMemory         │    │
│  │ prompt.py   系统提示词 + 工具索引                  │    │
│  │ tools.py    注册 18 个工具                         │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────┬───────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────┐
│  数据层              MySQL + 阿里云百炼 LLM                │
│  ┌──────────────────────────────────────────────────┐    │
│  │ message_store       对话消息持久化                 │    │
│  │ agent_summary_store 对话摘要持久化                │    │
│  │ user_info           用户信息                       │    │
│  │ solar_station       站点信息                       │    │
│  │ power_generation    发电量数据                     │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

**Spring 类比**：这个分层和 Spring MVC 的经典四层架构完全对应：

| FastAPI 层 | Spring 对应 | 职责 |
|---|---|---|
| `routers/` | `@RestController` | 接收 HTTP 请求，返回响应 |
| `schemas/` | DTO / VO (`UserDTO`, `ChatRequest`) | 定义请求体和响应体结构 |
| `services/` | `@Service` | 业务逻辑 |
| `backend/Agent/` + `Tools/` | `@Repository` + 业务逻辑 | 数据访问 + 核心业务 |
| `config.py` | `application.properties` + `@Configuration` | 配置管理 |
| `main.py` | `@SpringBootApplication` | 应用入口 |

---

## 3. FastAPI 核心概念速查（对比 Spring）

### 3.1 应用实例

```python
# FastAPI
app = FastAPI(title="SolarAgent API")
```

```java
// Spring
@SpringBootApplication
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
```

FastAPI 直接创建一个 `FastAPI()` 对象就是应用实例，比 Spring 简洁很多。

### 3.2 路由注册

```python
# FastAPI — 方式一：直接在 app 上定义
@app.get("/health")
async def health():
    return {"status": "ok"}

# FastAPI — 方式二：用 APIRouter（推荐，便于模块化）
router = APIRouter(prefix="/api", tags=["chat"])

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    ...

# 在 main.py 中注册
app.include_router(router)
```

```java
// Spring
@RestController
@RequestMapping("/api")
public class ChatController {

    @PostMapping("/chat/stream")
    public ResponseEntity<?> chatStream(@RequestBody ChatRequest req) {
        ...
    }
}
```

**区别**：Spring 用注解 `@RestController` + `@PostMapping`，FastAPI 用装饰器 `@router.post()`。Spring 通过组件扫描自动发现 Controller，FastAPI 需要手动 `include_router()` 注册。

### 3.3 请求体解析

```python
# FastAPI — 用 Pydantic BaseModel
class ChatRequest(BaseModel):
    session_id: str = Field(..., description="会话ID")
    message: str = Field(..., description="用户消息")
    user_id: int = Field(..., description="用户ID")

@router.post("/chat")
async def chat(req: ChatRequest):  # FastAPI 自动把 JSON body 解析为 ChatRequest
    print(req.session_id)           # 直接用属性访问
```

```java
// Spring — 用 DTO
@Data
public class ChatRequest {
    private String sessionId;
    private String message;
    private Integer userId;
}

@PostMapping("/chat")
public ResponseEntity<?> chat(@RequestBody ChatRequest req) {
    System.out.println(req.getSessionId());
}
```

**区别**：Spring 用 `@RequestBody` 注解 + Lombok `@Data`，FastAPI 用 Pydantic `BaseModel` + 类型注解。FastAPI 自动做类型校验（比如 `user_id` 必须是 int），校验失败自动返回 422 错误。

### 3.4 依赖注入

```python
# FastAPI — 用 Depends()
from backend.app.services.agent_manager import agent_manager  # 直接导入全局单例

@router.post("/chat")
async def chat(req: ChatRequest):
    executor = agent_manager.get_agent(req.session_id)  # 直接用
```

```java
// Spring — 用 @Autowired
@Service
public class ChatService {
    @Autowired
    private AgentManager agentManager;
}
```

**区别**：Spring 有 IoC 容器，自动注入依赖。FastAPI 没有容器，最简单的方式是直接导入全局单例（如 `agent_manager = AgentManager()`）。FastAPI 也支持 `Depends()` 做更复杂的依赖注入，但本项目用全局单例就够了。

### 3.5 异步处理

```python
# FastAPI — async/await
@router.post("/chat")
async def chat(req: ChatRequest):
    # await 表示等待异步操作完成，期间不阻塞线程
    result = await asyncio.to_thread(executor.invoke, {"input": req.message})
    return {"output": result["output"]}
```

```java
// Spring — CompletableFuture / @Async
@Async
public CompletableFuture<String> chat(String message) {
    return CompletableFuture.supplyAsync(() -> {
        return executor.invoke(message);
    });
}
```

**关键理解**：`async def` 定义的是协程函数，`await` 等待异步操作。FastAPI 在处理一个请求时，如果遇到 `await`，会释放线程去处理其他请求，等异步操作完成再回来继续。这和 Spring 的 `@Async` + `CompletableFuture` 概念类似。

### 3.6 查询参数

```python
# FastAPI — Query 参数
@router.get("/sessions")
async def list_sessions(user_id: int = Query(..., description="用户ID")):
    # ...?user_id=1
    return {"sessions": [...]}
```

```java
// Spring
@GetMapping("/sessions")
public ResponseEntity<?> listSessions(@RequestParam Integer userId) {
    // ...?userId=1
}
```

---

## 4. 入口文件 main.py 详解

`backend/app/main.py` 是整个 Web 服务的入口，相当于 Spring 的 `@SpringBootApplication` 启动类。

```python
# backend/app/main.py（简化版）

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from backend.app.config import settings
from backend.app.routers import chat, sessions, auth

def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    app = FastAPI(
        title="SolarAgent API",
        description="光伏发电分析助手 - Agent 对话接口",
        version="1.0.0",
    )

    # ① CORS 中间件（≈ Spring 的 CorsFilter / @CrossOrigin）
    origins = [o.strip() for o in settings.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,       # 允许哪些前端域名访问
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ② 注册路由（≈ Spring 的 @ComponentScan 自动发现 Controller）
    app.include_router(auth.router)       # /api/auth/register, /api/auth/login
    app.include_router(chat.router)       # /api/chat/stream, /api/chat/{id}/reply
    app.include_router(sessions.router)   # /api/sessions, /api/sessions/{id}/messages

    # ③ 健康检查接口
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "SolarAgent"}

    # ④ 内置测试页面（方便快速验证）
    @app.get("/", response_class=HTMLResponse)
    async def test_page():
        return TEST_HTML

    return app

# 全局应用实例
app = create_app()
```

### 逐段讲解

**① CORS 中间件**：浏览器的安全策略，默认不允许跨域请求（前端在 `localhost:5173`，后端在 `localhost:8000` 就是跨域）。CORS 中间件就是告诉浏览器"这些域名可以访问我"。

Spring 中对应 `@CrossOrigin` 注解或 `WebMvcConfigurer` 中注册 `CorsRegistry`。

**② 注册路由**：把三个路由模块（auth、chat、sessions）挂载到 app 上。Spring 中这一步是自动的——`@ComponentScan` 会扫描所有 `@RestController` 注解的类。

**③ 健康检查**：一个简单的 GET 接口，返回 `{"status": "ok"}`，用于运维监控。

**④ 内置测试页面**：访问 `http://localhost:8001` 就能看到前端测试页面，不用单独部署前端。Spring 中可以用 Thymeleaf 或直接返回静态 HTML。

### 启动命令

```bash
# 用项目自带的 .venv 虚拟环境启动
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload
```

- `backend.app.main:app` — 意思是 `backend/app/main.py` 文件中的 `app` 变量
- `--reload` — 代码修改后自动重启（开发模式用）
- `uvicorn` — ASGI 服务器，相当于 Spring 内嵌的 Tomcat

---

## 5. 路由层 routers 详解

### 5.1 sessions.py — 会话管理路由

这个文件实现了会话的增删查，以及安全校验。

```python
# backend/app/routers/sessions.py（核心代码简化版）

router = APIRouter(prefix="/api", tags=["sessions"])

# ========== 安全校验函数 ==========
def _verify_session_ownership(session_id: str, user_id: int) -> None:
    """
    校验会话所有权：确保该 session_id 属于该 user_id。

    查找顺序：
      1. agent_manager 内存缓存（刚创建还没对话的会话）
      2. message_store 表（已有对话记录）
      3. agent_summary_store 表（有摘要但消息已清理）

    不匹配抛 403，找不到抛 404。
    """
    # 1. 先查内存缓存
    if session_id in agent_manager._agents:
        executor = agent_manager._agents[session_id]
        owner_id = getattr(executor.memory, 'user_id', None)
        if owner_id is not None and owner_id != user_id:
            raise HTTPException(403, detail="无权操作此会话")
        return

    # 2. 再查数据库
    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT user_id FROM message_store WHERE session_id = :sid LIMIT 1"
        ), {"sid": session_id}).fetchone()

        if row is None:
            # message_store 没有，查 agent_summary_store
            row = conn.execute(text(
                "SELECT user_id FROM agent_summary_store WHERE session_id = :sid"
            ), {"sid": session_id}).fetchone()

        if row is None:
            raise HTTPException(404, detail=f"会话 {session_id} 不存在")

        owner_id = row[0]
        if owner_id is not None and owner_id != user_id:
            raise HTTPException(403, detail="无权操作此会话")
```

**Spring 类比**：这就像在 Controller 方法里加一个 `@PreAuthorize` 注解或写一个拦截器，在处理请求前先校验权限。FastAPI 没有拦截器机制，所以我们直接在路由函数开头调用校验函数。

```python
# 创建会话
@router.post("/sessions", response_model=SessionResponse)
async def create_session(user_id: int = Query(..., description="用户ID")):
    session_id = agent_manager.create_session(user_id=user_id)
    return SessionResponse(session_id=session_id)

# 查询会话列表
@router.get("/sessions")
async def list_sessions(user_id: int = Query(..., description="用户ID")):
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT session_id, MAX(created_at) as last_msg "
            "FROM message_store WHERE user_id = :uid "
            "GROUP BY session_id ORDER BY last_msg DESC"
        ), {"uid": user_id}).fetchall()
    return {"sessions": [{"session_id": r[0], "last_message_at": str(r[1])} for r in rows]}

# 获取会话历史消息
@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, user_id: int = Query(...)):
    _verify_session_ownership(session_id, user_id)  # 安全校验
    # ... 查询 message_store 返回消息列表

# 删除会话
@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user_id: int = Query(...)):
    _verify_session_ownership(session_id, user_id)  # 安全校验
    # ... 清理内存缓存 + 数据库记录
```

### 5.2 auth.py — 认证路由

```python
# backend/app/routers/auth.py
router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/register", response_model=UserResponse)
async def register(req: RegisterRequest):
    """注册新用户。"""
    try:
        user = register_user(req.username, req.password, req.display_name or "")
        return UserResponse(user_id=user["user_id"], username=user["username"],
                           role=user["role"], display_name=user["display_name"])
    except ValueError as e:
        raise HTTPException(400, detail=str(e))  门店已存在

@router.post("/login", response_model=UserResponse)
async def login(req: LoginRequest):
    """用户登录。"""
    try:
        user = login_user(req.username, req.password)
        return UserResponse(user_id=user["user_id"], ...)
    except ValueError as e:
        raise HTTPException(401, detail=str(e))  # 密码错误
    except RuntimeError as e:
        raise HTTPException(403, detail=str(e))  # 账号禁用
```

**Spring 类比**：`HTTPException(400, detail="...")` 相当于 Spring 中 `throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "...")`。

### 5.3 chat.py — 对话路由（最复杂）

这个文件实现了 SSE 流式对话和 ask_user 回复，是整个后端最核心的路由。详见 [第 9 节](#9-sse-流式对话完整流程带代码走读)。

---

## 6. 数据模型层 schemas 详解

`schemas/` 目录定义了 API 的请求体和响应体结构，相当于 Spring 的 DTO（Data Transfer Object）。

### 6.1 chat.py — 对话数据模型

```python
# backend/app/schemas/chat.py
from pydantic import BaseModel, Field
from typing import Optional

class ChatRequest(BaseModel):
    """对话请求 — 对应 Spring 的 ChatRequestDTO"""
    session_id: str = Field(..., description="会话ID")
    message: str = Field(..., description="用户消息")
    user_id: int = Field(..., description="用户ID，必传，校验会话所有权")

class ReplyRequest(BaseModel):
    """回复请求 — ask_user 的用户回复"""
    answer: str = Field(..., description="用户的回复内容")

class SessionResponse(BaseModel):
    """会话响应"""
    session_id: str = Field(..., description="会话ID")
```

**讲解**：
- `Field(...)` 中的 `...` 表示必传（不能省略），如果写成 `Field(None)` 则可选
- `str`、`int` 是类型注解，FastAPI 自动校验类型，类型不对返回 422 错误
- Spring 中你用 `@NotNull`、`@NotBlank` 等注解做校验，FastAPI 用类型注解 + Field 参数

### 6.2 user.py — 用户数据模型

```python
# backend/app/schemas/user.py
class RegisterRequest(BaseModel):
    """注册请求"""
    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")
    display_name: Optional[str] = Field(None, max_length=128, description="显示名称")

class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")

class UserResponse(BaseModel):
    """用户信息响应"""
    user_id: int = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    role: str = Field(..., description="角色: super_admin/admin/user")
    display_name: Optional[str] = Field(None, description="显示名称")
    message: str = Field("操作成功", description="提示消息")
```

**讲解**：`min_length=2, max_length=64` 就是 Spring 中的 `@Size(min=2, max=64)`。FastAPI 自动校验，如果用户传了 1 个字符的用户名，直接返回 422 错误，不需要你写 if 判断。

---

## 7. 服务层 services 详解

### 7.1 agent_manager.py — Agent 实例管理器

这是连接 Web 层和 Agent 层的桥梁。

```python
# backend/app/services/agent_manager.py（核心逻辑）

class AgentManager:
    """Agent 实例缓存 + AskUserBridge 管理。"""

    def __init__(self):
        self._agents: Dict[str, object] = {}         # session_id -> AgentExecutor
        self._bridges: Dict[str, AskUserBridge] = {} # session_id -> Bridge
        self._current_bridge: Optional[AskUserBridge] = None

        # 一次性替换 ask_user 的全局 handler
        # 把 CLI 的 input() 换成 Web 回调
        set_input_handler(self._web_input_handler)

    def _web_input_handler(self, question: str) -> str:
        """替代 CLI 的 input()，路由到当前活跃的 bridge。"""
        if self._current_bridge and self._current_bridge.is_active:
            return self._current_bridge.ask(question)
        return ""

    def create_session(self, user_id: Optional[int] = None) -> str:
        """创建新会话，返回 session_id。"""
        session_id = str(uuid.uuid4())[:8]  # 生成 8 位随机 ID
        executor = build_agent(session_id=session_id, use_db=True, user_id=user_id)
        self._agents[session_id] = executor  # 缓存到内存
        return session_id

    def get_agent(self, session_id: str, user_id: Optional[int] = None):
        """获取或创建 AgentExecutor。"""
        if session_id not in self._agents:
            executor = build_agent(session_id=session_id, use_db=True, user_id=user_id)
            self._agents[session_id] = executor
        return self._agents[session_id]

# 全局单例 — 整个应用共享一个 AgentManager
agent_manager = AgentManager()
```

**Spring 类比**：

```java
@Service
public class AgentManager {
    private Map<String, AgentExecutor> agents = new ConcurrentHashMap<>();
    private Map<String, AskUserBridge> bridges = new ConcurrentHashMap<>();

    public String createSession(Integer userId) {
        String sessionId = UUID.randomUUID().toString().substring(0, 8);
        AgentExecutor executor = AgentFactory.buildAgent(sessionId, true, userId);
        agents.put(sessionId, executor);
        return sessionId;
    }

    public AgentExecutor getAgent(String sessionId) {
        return agents.computeIfAbsent(sessionId,
            sid -> AgentFactory.buildAgent(sid, true, null));
    }
}
```

**关键设计**：
- **为什么缓存 AgentExecutor？** 因为 `build_agent()` 要初始化 LLM 连接、加载工具、构建 Memory，比较耗时。如果每条消息都重建，会很慢。所以按 session_id 缓存，同一个会话复用同一个 Agent 实例。
- **为什么用全局单例？** 因为所有请求共享同一份 Agent 缓存。Spring 中 `@Service` 默认就是单例，FastAPI 中我们手动创建全局变量 `agent_manager = AgentManager()` 实现同样效果。

### 7.2 user_service.py — 用户服务

```python
# backend/app/services/user_service.py（核心逻辑简化版）

def _hash_password(password: str) -> str:
    """密码哈希：salt + sha256。格式: salt$hash"""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${hashed}"

def register_user(username: str, password: str, display_name: str = "") -> dict:
    """注册新用户。"""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM user_info WHERE username=%s", (username,))
        if cur.fetchone():
            raise ValueError(f"用户名 '{username}' 已存在")

        password_hash = _hash_password(password)
        cur.execute(
            "INSERT INTO user_info (username, password_hash, display_name) VALUES (%s, %s, %s)",
            (username, password_hash, display_name or username)
        )
        conn.commit()
        return {"user_id": cur.lastrowid, "username": username, "role": "user", ...}

def login_user(username: str, password: str) -> dict:
    """用户登录。"""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, password_hash, role, status FROM user_info WHERE username=%s", (username,))
        row = cur.fetchone()
        if not row:
            raise ValueError("用户名不存在")
        if not _verify_password(password, row[1]):
            raise ValueError("密码错误")
        return {"user_id": row[0], "username": username, "role": row[2], ...}
```

**Spring 类比**：这就是一个标准的 `@Service` 类，用 `JdbcTemplate` 或 `MyBatis` 操作数据库。密码哈希用了 `salt + sha256`，生产环境建议换 `BCryptPasswordEncoder`。

### 7.3 ask_user_bridge.py — 同步异步桥接器

这是整个项目最精巧的设计，详见 [第 10 节](#10-ask_user-交互桥接原理最难的部分)。

---

## 8. Agent 核心层详解

这一层是改造前就有的代码，Web 化时几乎没改。

### 8.1 agent.py — Agent 组装

```python
# backend/Agent/agent.py
def build_agent(session_id="test", use_db=False, memory=None, user_id=None) -> AgentExecutor:
    """组装 AgentExecutor，像工厂方法。"""
    llm = build_llm()              # 1. 创建 LLM（阿里云百炼 qwen-plus）
    tools = get_all_tools()        # 2. 获取所有工具（18 个）
    prompt = build_prompt()        # 3. 构建系统提示词

    if memory is None:
        memory = build_memory(session_id=session_id, use_db=use_db, llm=llm, user_id=user_id)

    # 4. 创建 Agent（把 LLM + Tools + Prompt 组装成 Agent）
    agent = create_tool_calling_agent(llm, tools, prompt)

    # 5. 包装成 AgentExecutor（加上 Memory + 迭代控制）
    return AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        max_iterations=10,
        verbose=True,
        handle_parsing_errors=True,
    )
```

**Spring 类比**：`build_agent()` 就像 `@Bean` 工厂方法，组装一个复杂的 Bean。`AgentExecutor` 就是核心业务引擎，类似 Spring 中一个封装了多种策略的 Service。

### 8.2 llm.py — LLM 构建

```python
# backend/Agent/llm.py
def build_llm() -> ChatOpenAI:
    """创建 LLM 客户端，连接阿里云百炼。"""
    return ChatOpenAI(
        api_key=os.getenv("API_KEY"),     # 从 .env 读取
        base_url=os.getenv("BASE_URL"),   # 百炼 API 地址
        model=os.getenv("MODEL_NAME"),    # qwen-plus
        streaming=True,                    # 启用流式输出
        temperature=0.5,                   # 创造性适中
    )
```

**讲解**：`ChatOpenAI` 是 LangChain 提供的 LLM 封装类。虽然名字叫 OpenAI，但它兼容所有 OpenAI API 格式的 LLM（阿里云百炼用的就是兼容格式）。`streaming=True` 让 LLM 逐 token 返回，这是 SSE 流式对话的基础。

### 8.3 memory.py — 记忆管理

```python
# backend/Agent/memory.py（核心设计）

class PersistentWindowSummaryMemory(BaseChatMemory, SummarizerMixin):
    """
    窗口 + 摘要 + 持久化记忆。

    - 最近 k 轮对话保留完整原文（窗口）
    - 更早的对话用 LLM 总结成摘要
    - 消息和摘要都持久化到 MySQL
    - 每条消息携带 user_id，实现用户隔离
    """

    k: int = 3                    # 窗口大小：保留最近 3 轮（6 条消息）
    session_id: str = "default"   # 会话标识
    user_id: Optional[int] = None # 消息所有者
    db_url: Optional[str] = None  # MySQL 连接串
```

**记忆读取流程**（每轮对话前触发）：
```
load_memory_variables()
  1. 从 message_store 读取该 session 全部消息
  2. 从 agent_summary_store 读取摘要
  3. 截取最近 2*k 条作为窗口
  4. 拼装：摘要 + 窗口消息 → 返回给 LLM
```

**记忆写入流程**（每轮对话后触发）：
```
save_context()  ← 继承父类默认实现
  → chat_memory.add_messages([HumanMessage, AIMessage])
  → INSERT 到 message_store（携带 user_id）
  → 只 INSERT，不 DELETE，不触发 LLM 调用
```

**异步摘要**（对话结束后触发）：
```
amaybe_summarize()
  → 检查消息数是否超过阈值
  → 超过则把旧消息交给 LLM 总结
  → 保存摘要到 agent_summary_store
  → 失败不影响主流程
```

### 8.4 tools.py — 工具注册

```python
# backend/Agent/tools.py
ALL_TOOLS = [
    get_station_location,      # 查站点经纬度
    get_station_info,          # 查站点基本信息
    get_current_datetime,      # 查当前时间
    parse_date,                # 自然语言日期解析
    get_weather_by_range,      # 查气象数据
    predict_power,             # 发电量预测
    get_actual_power,          # 查实际发电量（单日）
    get_actual_power_by_range, # 查实际发电量（范围）
    export_table,              # 导出 Excel/CSV
    write_file,                # 写文件
    read_file,                 # 读文件
    verify_file,               # 验证文件
    search_knowledge_base,     # 知识库检索
    ask_user,                  # 向用户提问
    # ... 共 18 个工具
]

def get_all_tools():
    return ALL_TOOLS
```

**讲解**：每个工具都是用 `@tool` 装饰器修饰的函数。LangChain 的 Agent 会根据用户的输入，自动决定调用哪个工具。这就像 Spring 中你定义了多个 `@Service` 方法，由一个 Facade 统一调度。

---

## 9. SSE 流式对话完整流程（带代码走读）

这是整个后端最核心、最复杂的部分。我们用"用户发送一条消息"为例，走一遍完整流程。

### 9.1 什么是 SSE

SSE（Server-Sent Events）是 HTTP 协议的一种长连接机制。服务器可以持续推送数据到浏览器，浏览器只需发起一次 HTTP 请求。

| 维度 | SSE | WebSocket |
|---|---|---|
| 通信方向 | 服务器→客户端（单向） | 双向 |
| Spring 对应 | `SseEmitter` | `@ServerEndpoint` |
| 浏览器 API | `EventSource` | `WebSocket` |
| 适用场景 | LLM 流式输出（单向推送） | 实时聊天（双向） |

我们选 SSE 的原因：LLM 的输出是单向流（服务器生成 → 客户端展示），不需要双向通信。

### 9.2 SSE 数据格式

SSE 响应的 `Content-Type` 是 `text/event-stream`，每条消息格式：

```
event: token
data: {"content": "你好"}

event: tool_start
data: {"name": "get_station_info"}

event: done
data: {"output": "完整回复..."}
```

### 9.3 代码走读

```python
# backend/app/routers/chat.py

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    # ① 安全校验
    _verify_session_ownership(req.session_id, req.user_id)

    # ② 获取 Agent 和 Bridge
    executor = agent_manager.get_agent(req.session_id, user_id=req.user_id)
    bridge = agent_manager.get_or_create_bridge(req.session_id)

    # ③ 定义 SSE 事件生成器
    async def event_generator():
        # 创建统一事件队列
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        # 绑定 bridge 到当前事件循环
        bridge.attach(queue, loop)
        agent_manager.set_current_bridge(bridge)

        full_output = ""

        # ④ 后台任务：消费 Agent 事件流
        async def consume_agent():
            nonlocal full_output
            try:
                # astream_events 是 LangChain 的异步事件流
                # 它会产出各种事件：LLM 输出 token、工具开始/结束等
                async for ev in executor.astream_events(
                    {"input": req.message}, version="v2"
                ):
                    # 过滤事件，提取前端需要的信息
                    processed = _process_agent_event(ev)
                    if processed:
                        kind, data = processed
                        if kind == "token":
                            full_output += data["content"]
                        await queue.put(processed)  # 推入队列
            except Exception as e:
                await queue.put(("error", {"message": str(e)}))
            finally:
                await queue.put(("done", {"output": full_output}))

        # 启动后台任务
        task = asyncio.create_task(consume_agent())

        # ⑤ 主循环：从队列取事件，yield 给前端
        try:
            while True:
                kind, data = await queue.get()  # 阻塞等待队列中的事件

                if kind == "done":
                    yield {"event": "done", "data": json.dumps(data)}
                    break
                elif kind == "token":
                    yield {"event": "token", "data": json.dumps(data)}
                elif kind == "tool_start":
                    yield {"event": "tool_start", "data": json.dumps(data)}
                elif kind == "tool_end":
                    yield {"event": "tool_end", "data": json.dumps(data)}
                elif kind == "question":
                    yield {"event": "user_input_required", "data": json.dumps(data)}
        finally:
            bridge.detach()
            agent_manager.set_current_bridge(None)
            if not task.done():
                task.cancel()
            # 异步触发摘要
            await executor.memory.amaybe_summarize()

    # ⑥ 返回 SSE 响应
    return EventSourceResponse(event_generator(), ping=15)
```

### 9.4 流程图解

```
浏览器                    FastAPI                    LangChain Agent
  │                          │                            │
  │  POST /api/chat/stream   │                            │
  │  {session_id, message}   │                            │
  │─────────────────────────>│                            │
  │                          │                            │
  │                    ①校验权限 ②获取Agent                 │
  │                          │                            │
  │                  ③创建Queue + 启动后台任务               │
  │                          │  executor.astream_events() │
  │                          │───────────────────────────>│
  │                          │                            │
  │              ┌───────────│  ④Agent 开始执行             │
  │              │           │  LLM 输出 token "你"        │
  │              │           │<───────────────────────────│
  │              │           │  → queue.put(("token",...))│
  │              │           │                            │
  │  event: token            │                            │
  │  data: {"content":"你"}  │  ⑤从Queue取出 → yield SSE   │
  │<─────────────────────────│                            │
  │              │           │                            │
  │              │           │  LLM 输出 token "好"        │
  │              │           │<───────────────────────────│
  │  event: token            │                            │
  │  data: {"content":"好"}  │                            │
  │<─────────────────────────│                            │
  │              │           │                            │
  │              │           │  LLM 决定调用工具            │
  │              │           │  事件: on_tool_start        │
  │              │           │<───────────────────────────│
  │  event: tool_start       │                            │
  │  data: {"name":"..."}    │                            │
  │<─────────────────────────│                            │
  │              │           │                            │
  │              │           │  工具执行完毕                │
  │              │           │  事件: on_tool_end          │
  │              │           │<───────────────────────────│
  │  event: tool_end         │                            │
  │<─────────────────────────│                            │
  │              │           │                            │
  │              │           │  Agent 执行结束              │
  │              │           │  → queue.put(("done",...))  │
  │  event: done             │                            │
  │  data: {"output":"..."}  │                            │
  │<─────────────────────────│                            │
  │                          │                            │
  │ 连接关闭                   │  ⑥触发异步摘要               │
```

### 9.5 为什么需要 asyncio.Queue

`consume_agent` 是生产者，`event_generator` 的 while 循环是消费者。它们是两个并行的协程。Queue 是它们之间的缓冲通道：

```
┌──────────────┐     asyncio.Queue     ┌──────────────┐
│ consume_agent │  ────(kind, data)──→  │ event_gen    │
│  (生产者协程)  │                       │ (消费者)     │
│              │                       │              │
│ astream_events│                      │ queue.get()  │
│ ↓ 处理每个事件 │                      │ ↓ yield SSE  │
│ ↓ put到queue  │                      │ ↓ 推给浏览器  │
└──────────────┘                       └──────────────┘
```

**Spring 类比**：相当于一个 `@Async` 方法往 `BlockingQueue` 里放数据，另一个方法从 Queue 取数据通过 `SseEmitter.send()` 推给客户端。区别是 FastAPI 用 `asyncio.Queue`（协程级），Spring 用 `BlockingQueue`（线程级）。

---

## 10. ask_user 交互桥接原理（最难的部分）

### 10.1 问题是什么

`ask_user` 工具是在 Agent 执行过程中，让 Agent 向用户提问并等待回复。

**CLI 模式下**很简单：
```python
answer = input("请选择: ")  # 阻塞等待用户输入
```

**Web 模式下**不能这样做，因为：
1. `input()` 会阻塞线程，导致 SSE 流卡死
2. Web 模式下用户在浏览器输入，不在终端
3. Agent 在子线程中运行，SSE 在事件循环中运行，两者需要跨线程通信

### 10.2 解决方案

用 `threading.Event`（线程同步）+ `asyncio.Queue`（跨线程通信）实现桥接。

### 10.3 线程模型

```
┌─ asyncio 事件循环 (主线程) ──────────────────────────────┐
│                                                         │
│  event_generator()  ←→  asyncio.Queue  ←→  consume_agent()│
│  (SSE推流)                    ↑                    ↓     │
│                               │              astream_events│
│                               │                    ↓     │
│                               │            ┌─────────────┐│
│                               │            │ LangChain    ││
│                               │            │ 线程池(子线程)││
│                               │            │              ││
│                               │            │  ask_user()  ││
│                               │            │  ↓ 阻塞等待   ││
│                               │            │  Event.wait()││
│                               │            └─────────────┘│
│                               │                    ↑      │
│  POST /reply ──→ bridge.reply(answer) ──→ Event.set()    │
│                                                         │
└──────────────────────────────────────────────────────────┘
```

### 10.4 三步交互流程

以"查询哲丰电站"匹配到 6 个站点为例：

**第一步：ask_user 被触发（子线程）**

Agent 决定调用 `ask_user` 工具，运行在 LangChain 的线程池子线程中：

```python
# ask_user_bridge.py — ask() 方法
def ask(self, question: str) -> str:
    self._reply = None
    self._reply_event.clear()  # 重置信号

    # 关键1：把问题推入 asyncio.Queue（跨线程安全操作）
    self._loop.call_soon_threadsafe(
        self._queue.put_nowait,
        ("question", {"question": question})
    )

    # 关键2：阻塞当前子线程，等待用户回复
    # 这不会阻塞事件循环，因为我们在子线程中
    if self._reply_event.wait(timeout=self.timeout):
        return f"用户回复: {self._reply}"
    else:
        return "用户回复: (超时未回复)"
```

**Spring 类比**：`threading.Event.wait()` 相当于 `CountDownLatch.await()`，阻塞当前线程等待另一个线程的 `countDown()`。

**第二步：前端收到问题并回复（HTTP 请求）**

问题通过 Queue → SSE 事件 `user_input_required` → 浏览器收到 → 弹出输入框 → 用户选择 → 浏览器发 `POST /api/chat/{session_id}/reply`：

```python
# backend/app/routers/chat.py
@router.post("/chat/{session_id}/reply")
async def reply_to_question(session_id: str, req: ReplyRequest, user_id: int = Query(...)):
    _verify_session_ownership(session_id, user_id)
    bridge = agent_manager.get_bridge(session_id)
    bridge.reply(req.answer)  # 唤醒等待中的 ask()
    return {"status": "ok"}
```

**第三步：bridge.reply 唤醒阻塞的线程**

```python
# ask_user_bridge.py — reply() 方法
def reply(self, answer: str):
    self._reply = answer        # 存储回复内容
    self._reply_event.set()     # 唤醒 ask() 中的 Event.wait()
```

`Event.set()` 唤醒子线程 → `ask()` 返回用户回复 → Agent 继续执行 → 后续 token 继续推送到前端。

### 10.5 为什么用 threading.Event 而不是 asyncio.Event

因为 `ask_user` 工具运行在**子线程**中（LangChain 的线程池），不是协程。`asyncio.Event` 只能在事件循环中使用，在子线程中调用 `await event.wait()` 会报错。`threading.Event` 是线程级别的同步原语，可以在任意线程中 `wait()` 和 `set()`。

### 10.6 call_soon_threadsafe 是什么

这是 Python asyncio 模块提供的**唯一安全跨线程操作事件循环**的方法。在子线程中你不能直接 `await queue.put(...)`，必须通过 `call_soon_threadsafe` 让事件循环自己在合适的时机执行 `put_nowait`。

**Spring 类比**：相当于你在一个普通线程中需要往 Spring 的 `SseEmitter` 写数据，不能直接写，需要通过线程安全的方式提交到主线程执行。

---

## 11. 用户隔离与安全校验

### 11.1 数据库设计

```
user_info 表
├── id (PK)
├── username (UNIQUE)
├── password_hash
├── role (super_admin/admin/user)
├── display_name
└── status (1=active, 0=disabled)

message_store 表
├── id (PK)
├── session_id
├── user_id  ← 新增列，消息所有者
├── message
└── created_at

agent_summary_store 表
├── session_id (PK)
├── user_id  ← 新增列
└── summary
```

### 11.2 消息写入时携带 user_id

```python
# backend/Agent/memory.py — 自定义 ORM Model
class UserAwareMessage(ConverterBase):
    """message_store 的 ORM 映射，额外携带 user_id 列。"""
    __tablename__ = "message_store"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(255))
    user_id = Column(BigInteger)    # ← 比标准 Model 多一个 user_id
    message = Column(Text)
    created_at = Column(DateTime)

# 自定义 Converter，在写入时自动填入 user_id
class ChineseFriendlyConverter(DefaultMessageConverter):
    def __init__(self, table_name: str, user_id: Optional[int] = None):
        self._user_id = user_id
        self.model_class = UserAwareMessage

    def to_sql_model(self, message: BaseMessage, session_id: str):
        return self.model_class(
            session_id=session_id,
            user_id=self._user_id,      # ← 写入 user_id
            message=json.dumps(message_to_dict(message), ensure_ascii=False)
        )
```

### 11.3 安全校验流程

所有涉及会话操作的接口都调用 `_verify_session_ownership`：

```
用户请求 GET /api/sessions/abc123/messages?user_id=1
  │
  ├── 1. 查内存缓存（agent_manager._agents）
  │     找到 session_id=abc123，其 memory.user_id=1
  │     user_id 匹配 → 校验通过
  │
  ├── 2. 如果内存缓存没有，查 message_store
  │     SELECT user_id FROM message_store WHERE session_id='abc123' LIMIT 1
  │     找到 user_id=1 → 匹配 → 通过
  │
  ├── 3. 如果 message_store 也没有，查 agent_summary_store
  │     找到 → 匹配 → 通过
  │
  ├── 4. 都没找到 → 返回 404 "会话不存在"
  │
  └── 5. user_id 不匹配 → 返回 403 "无权操作此会话"
```

---

## 12. 一个请求的完整生命周期（时序图）

以用户在浏览器发送"你好"为例：

```
浏览器                    FastAPI                    AgentManager              backend/Agent/LLM               MySQL
  │                          │                          │                       │                       │
  │ POST /api/chat/stream    │                          │                       │                       │
  │ {session_id, message,   │                          │                       │                       │
  │  user_id}               │                          │                       │                       │
  │─────────────────────────>│                          │                       │                       │
  │                          │                          │                       │                       │
  │              ┌───────────│ verify_ownership()       │                       │                       │
  │              │           │──────────────────────────────────────────────────────────────────────────>│
  │              │           │<─────────────────────────────────────────────────────────────────────────│
  │              │           │                          │                       │                       │
  │              ├───────────│ get_agent(session_id)    │                       │                       │
  │              │           │─────────────────────────>│                       │                       │
  │              │           │  return executor         │                       │                       │
  │              │           │<─────────────────────────│                       │                       │
  │              │           │                          │                       │                       │
  │              ├───────────│ get_bridge(session_id)   │                       │                       │
  │              │           │─────────────────────────>│                       │                       │
  │              │           │  return bridge           │                       │                       │
  │              │           │<─────────────────────────│                       │                       │
  │              │           │                          │                       │                       │
  │              │     创建Queue，启动后台任务             │                       │                       │
  │              │           │                          │  astream_events()     │                       │
  │              │           │──────────────────────────────────────────────────>│                       │
  │              │           │                          │                       │                       │
  │              │           │      event: token "你"    │                       │                       │
  │              │           │<─────────────────────────────────────────────────│                       │
  │  SSE: token "你"         │                          │                       │                       │
  │<─────────────────────────│                          │                       │                       │
  │              │           │                          │                       │                       │
  │              │           │      event: token "好"    │                       │                       │
  │              │           │<─────────────────────────────────────────────────│                       │
  │  SSE: token "好"         │                          │                       │                       │
  │<─────────────────────────│                          │                       │                       │
  │              │           │                          │                       │                       │
  │              │           │      event: done         │                       │                       │
  │              │           │<─────────────────────────────────────────────────│                       │
  │  SSE: done               │                          │                       │                       │
  │<─────────────────────────│                          │                       │                       │
  │                          │                          │                       │                       │
  │              │      异步触发摘要                       │                       │                       │
  │              │           │──────────────────────────────────────────────────────────────────────────>│
  │              │           │                          │  INSERT message_store │                       │
  │              │           │                          │  (携带 user_id)        │                       │
  │              │           │                          │──────────────────────────────────────────────>│
  │ 连接关闭                   │                          │                       │                       │
```

---

## 13. 改造前后对比：CLI vs Web

| 维度 | CLI 模式 (run.py) | Web 模式 (FastAPI) |
|---|---|---|
| **入口** | `python -m backend.Agent.run` | `uvicorn backend.app.main:app` |
| **用户输入** | `input("你: ")` 阻塞终端 | `POST /api/chat/stream` HTTP 请求 |
| **结果输出** | `print(result['output'])` 一次性打印 | SSE 流式逐 token 推送 |
| **多用户** | 不支持，单终端 | 支持，每个用户独立会话 |
| **用户隔离** | 无，所有人共享 session | user_id 绑定，安全校验 |
| **ask_user** | `input()` 直接阻塞 | threading.Event + asyncio.Queue 桥接 |
| **记忆持久化** | MySQL (use_db=True) | MySQL (相同) |
| **会话管理** | 写死 session_id | REST API 创建/查询/删除 |
| **Agent 核心** | `build_agent()` | `build_agent()` （不变） |
| **工具集** | 18 个工具 | 18 个工具（不变） |
| **LLM** | ChatOpenAI (qwen-plus) | ChatOpenAI (不变) |

### 改造的关键代码变化

**获取用户输入**：
```python
# 改造前（CLI）
user_input = input("你: ")

# 改造后（Web）
# 用户输入通过 HTTP 请求体传入
@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    message = req.message  # ← 从请求体获取
```

**输出结果**：
```python
# 改造前（CLI）
result = agent.invoke({"input": user_input})
print(result['output'])

# 改造后（Web）— SSE 流式
async for ev in executor.astream_events({"input": message}, version="v2"):
    # 逐 token 推送给浏览器
    yield {"event": "token", "data": json.dumps({"content": token})}
```

**ask_user 交互**：
```python
# 改造前（CLI）
answer = input("请选择: ")  # 直接阻塞等输入

# 改造后（Web）
# 1. 把问题推到 SSE 队列
# 2. threading.Event.wait() 阻塞子线程
# 3. 前端发 POST /reply → Event.set() 解除阻塞
```

---

## 14. 常见问题 FAQ

### Q1: FastAPI 的 async def 和普通 def 有什么区别？

`async def` 定义的是协程函数，可以用 `await`。FastAPI 对两种函数的处理方式不同：
- `async def`：在事件循环中直接执行
- 普通 `def`：放到线程池中执行（避免阻塞事件循环）

如果函数中有 `await`（如 `await executor.astream_events()`），必须用 `async def`。如果函数是纯 CPU 密集或阻塞 IO（如 `time.sleep()`），用普通 `def` 让 FastAPI 自动放线程池。

### Q2: 为什么不用 Flask 而用 FastAPI？

Flask 是同步框架，一个请求占一个线程，SSE 流式对话会卡住线程。FastAPI 是异步框架（ASGI），一个线程可以处理多个并发连接，天然适合 SSE 和 WebSocket。

**Spring 类比**：Flask ≈ Spring MVC（同步，一个请求一个 Servlet 线程），FastAPI ≈ Spring WebFlux（异步，响应式）。

### Q3: agent_manager 是全局单例，会不会有线程安全问题？

当前设计中，`_agents` 字典的读写是原子的（Python GIL 保证），多线程并发读写不会崩溃。但 `_current_bridge` 是单值，同一时刻只支持一个 ask_user 交互。多并发场景需要改用 `contextvars` 方案。

### Q4: 为什么用 SSE 而不是 WebSocket？

LLM 的输出是单向流（服务器 → 客户端），不需要双向通信。SSE 更简单：浏览器自带 `EventSource` API，断线自动重连，用普通 HTTP 即可。WebSocket 需要额外的握手协议和心跳维护。

### Q5: message_store 里的 user_id 是怎么写入的？

通过自定义 SQLAlchemy Model（`UserAwareMessage`）+ 自定义 Converter（`ChineseFriendlyConverter`）。在 `to_sql_model()` 方法中，把 `user_id` 写入 ORM 对象。LangChain 的 `SQLChatMessageHistory` 调用这个 Converter 写入消息，`user_id` 就自动带上了。

### Q6: 为什么 _verify_session_ownership 不用 await？

它是同步函数（普通 `def`），不是协程函数（`async def`）。同步函数直接调用即可，不需要 `await`。`await` 只能用于协程函数返回的协程对象。如果对同步函数用 `await`，会报 `TypeError: object NoneType can't be used in 'await' expression`。

### Q7: uvicorn 和 Tomcat 是什么关系？

都是 Web 服务器，负责接收 HTTP 请求转给应用处理：
- **Tomcat**：Java 的 Servlet 容器，Spring Boot 内嵌使用
- **uvicorn**：Python 的 ASGI 服务器，FastAPI 运行在它上面

### Q8: .env 文件是做什么的？

存放环境变量配置（数据库密码、API Key 等），不提交到 Git。相当于 Spring 的 `application.properties` 或 `application.yml`。用 `python-dotenv` 库加载：

```env
# .env 示例
MYSQL_URL=mysql+pymysql://root:password@localhost:3306/solar_agent
API_KEY=sk-xxxxx
BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen-plus
FILE_DIR=./backend/temp/file
```

---

> **文档结束**。如有疑问，可以对照代码逐行阅读，每个函数都有注释说明。核心理解顺序建议：main.py → sessions.py → chat.py → agent_manager.py → ask_user_bridge.py → memory.py → agent.py。
