# Token 认证与 AskUser 交互：从能运行到真正理解

> 适用项目：Solar Agent 阶段一。本文只解释当前已经落地的实现，并在最后说明怎样把它平滑演进为 Vue 前后端分离下更完整的生产方案。

## 先用一句话理解

- **Token 认证**：用户登录后，后端发给他一张带防伪印章的“临时通行证”（JWT）。以后每次请求都带通行证，后端自己确认“你是谁”，而不是相信前端说“我是 3 号用户”。
- **AskUser**：智能体需要用户补充信息时，不能真的在服务器上 `input()` 等键盘。它把问题递给网页；网页弹出输入框；用户回答后，网页再把答案送回服务器；原本暂停的智能体从这里继续执行。

这两个能力共同解决的是“**可信地知道是谁在操作**”和“**在一次长任务中可靠地向这个人追问**”。

---

## 一、系统全景：两条链路如何协作

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as 前端页面 / 未来 Vue
    participant A as FastAPI
    participant G as Agent 与工具
    participant DB as 数据库

    U->>F: 登录：用户名、密码
    F->>A: POST /api/auth/login
    A->>DB: 校验 Argon2id 密码哈希
    A-->>F: JWT access_token
    U->>F: 提问：查询“哲丰”发电量
    F->>A: POST /api/chat/stream + Authorization: Bearer Token
    A->>A: 从 Token 解析 user_id，校验会话归属
    A->>G: 执行 Agent（SSE 持续输出）
    G->>G: 发现“哲丰”对应多个站点，调用 ask_user
    G->>A: Bridge 把真实问题放进队列
    A-->>F: SSE 事件 user_input_required
    F->>U: 弹窗展示站点选项
    U->>F: 选择第 4 个站点
    F->>A: POST /api/chat/{session_id}/reply + Bearer Token
    A->>A: 校验该会话确属当前 Token 的用户
    A->>G: Bridge 唤醒等待中的工具
    G-->>A: 继续查询并生成答案
    A-->>F: SSE token / done
    F-->>U: 显示最终结果
```

请注意：**身份校验发生在两次请求上**：开始聊天时校验一次，提交 AskUser 回复时再校验一次。这样别人即使猜到你的 `session_id`，也不能替你回答问题。

---

## 二、Token 认证：为什么、怎么做、代码在哪里

### 2.1 先理解旧问题：为什么不能让前端传 `user_id`

可以把网页表单看成一张用户自己填写的纸条。

```json
{ "session_id": "abcd1234", "user_id": 3, "message": "查询数据" }
```

即使正常页面不会改 `user_id`，用户也可以用浏览器开发者工具、Postman 或脚本把它改为 `1`。后端若直接相信它，就等于允许任何人说“我是管理员”。这不是前端 bug，而是**信任边界放错了**：前端永远是用户可控制的环境。

正确设计是：请求体只放业务信息；身份信息放进后端签名的 Token。

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

前端可以保存和转发这串字符，但无法伪造其中的签名。

### 2.2 当前认证方案的组成

| 组件 | 当前做法 | 作用 |
| --- | --- | --- |
| 密码存储 | Argon2id | 数据库只存难以反推的密码哈希，不存明文密码 |
| 登录凭证 | JWT Access Token | 后端签名的短期身份证明 |
| 签名算法 | HS256 | 后端用 `JWT_SECRET_KEY` 签名与验签 |
| 身份传递 | HTTP `Authorization: Bearer <token>` | 对 Vue、移动端、内置测试页面都是标准协议 |
| 后端守卫 | FastAPI `Depends(get_current_user)` | 每个受保护接口统一提取当前用户 |
| 数据隔离 | `user_id + session_id` 校验 | 不让 A 用户读写 B 用户会话 |

### 2.3 登录时发生了什么

相关代码：[`backend/app/routers/auth.py`](../backend/app/routers/auth.py)、[`backend/app/services/user_service.py`](../backend/app/services/user_service.py)、[`backend/app/services/auth_service.py`](../backend/app/services/auth_service.py)。

1. 前端将用户名和密码发送到 `POST /api/auth/login`。
2. `login_user()` 从数据库取出该用户的 `password_hash`。
3. `PasswordHasher.verify()` 用 Argon2id 验证“这次输入的密码”是否对应“数据库的哈希”。
4. 验证通过后，`create_access_token(user)` 生成 Token。
5. 登录接口返回 `access_token`、`expires_in` 和用于页面显示的用户资料。

核心代码可以简化理解为：

```python
# backend/app/services/auth_service.py
payload = {
    "sub": str(user["user_id"]),  # 主体：谁
    "username": user["username"],
    "role": user.get("role", "user"),
    "iat": now,                    # 签发时间
    "exp": now + timedelta(seconds=expires_in),  # 过期时间
    "type": "access",             # 防止把别种 Token 当访问 Token
}
token = jwt.encode(payload, secret, algorithm="HS256")
```

把 JWT 想成一张装在透明塑封袋里的证件：内容可以被看到（因此**不能放密码、手机号、隐私数据**），但任何人改过一个字，后端验签都会失败。真正的防伪能力来自只有服务器知道的 `JWT_SECRET_KEY`。

### 2.4 后端怎样从 Token 取得“当前用户”

相关代码：[`backend/app/dependencies/auth.py`](../backend/app/dependencies/auth.py)。

`get_current_user()` 是一个 FastAPI 依赖。只要路由写上：

```python
async def create_session(
    current_user: dict = Depends(get_current_user),
):
```

FastAPI 就会在真正进入 `create_session()` **之前**完成以下动作：

1. `HTTPBearer` 从请求头找到 Bearer Token。
2. `decode_access_token()` 用同一个密钥和限定算法 `HS256` 验签。
3. 检查 `sub` 和 `type == "access"`。
4. 把可信的 `{user_id, username, role}` 交给路由。
5. 缺 Token、被篡改、过期、密钥不匹配，统一返回 HTTP 401。

这就是“门卫”模式：业务路由不需要自己到处写验 Token 的代码，统一由入口依赖处理。

### 2.5 会话隔离：认证之后还要授权

认证回答的是“你是谁”；授权回答的是“你能不能操作这条资源”。两者不是一回事。

例如，用户甲带着自己的有效 Token 请求：

```http
POST /api/chat/other-user-session/reply
```

他的身份是真的，但这个会话未必属于他。因此 [`backend/app/routers/sessions.py`](../backend/app/routers/sessions.py) 的 `_verify_session_ownership()` 会把 Token 解析出来的 `user_id` 与会话所属 `user_id` 比较：

- 相等：继续。
- 不相等：HTTP 403，无权操作。
- 会话不存在或没有可信归属：HTTP 404。

聊天开始、读取历史、删除会话、回复 AskUser 都调用这层校验。重点是校验依据来自 Token，而不是 `req.user_id`。

### 2.6 前端如何携带 Token，以及 Vue 要怎样接入 - 这里讲的不详细，需要补一下

当前内置验证页在 [`backend/app/main.py`](../backend/app/main.py) 中：登录后保存 `data.access_token`，`apiFetch()` 给每个请求加上：

```javascript
Authorization: 'Bearer ' + accessToken
```

未来 Vue 只需把同一规则放入 Axios 拦截器；SSE 在当前实现里使用 `fetch()` 读取流，所以在 `fetch` 的 headers 中手动带 Token。

```javascript
// api.js，示意代码
api.interceptors.request.use((config) => {
  config.headers.Authorization = `Bearer ${tokenStore.accessToken}`
  return config
})

// 流式聊天仍用 fetch，因为 EventSource 原生不方便自定义 Authorization 头
fetch('/api/chat/stream', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${tokenStore.accessToken}`,
  },
  body: JSON.stringify({ session_id, message }),
})
```

### 2.7 Token 方案的边界与下一步扩展

当前方案已经适合阶段一，但它是 **Access Token 单令牌方案**。生产化可以按优先级继续演进：

| 优先级 | 扩展 | 为什么需要 |
| --- | --- | --- |
| P0 | HTTPS、强随机 32+ 位 `JWT_SECRET_KEY`、密钥不提交 Git | 防止 Token 在传输或配置中泄露 |
| P0 | Access Token 设较短有效期 | 缩短 Token 被盗后的可用窗口 |
| P1 | Refresh Token + 轮换 | 用户不必频繁登录，同时维持短 Access Token |
| P1 | Refresh Token 放 `HttpOnly + Secure + SameSite` Cookie | JavaScript 读不到，降低 XSS 窃取风险 |
| P1 | 登录/刷新接口限流、失败审计 | 抵抗撞库与暴力尝试 |
| P2 | Token 黑名单或 `token_version` | 实现登出、改密、封禁后的即时失效 |
| P2 | RBAC 权限依赖 | `role` 不只写入 Token，还真正控制管理接口 |

**一个重要取舍**：JWT 是“无状态”的，后端通常不查库就能验签，因此很快；代价是已经签发的 Token 不会自动消失。Refresh Token、短过期时间和 `token_version` 是常见的补救组合。

---

## 三、AskUser：给不懂编程的人也能听懂的解释

### 3.1 先讲生活比喻：餐厅的传菜铃

想象四个角色：

- **顾客**：用户。
- **服务员**：网页前端。
- **前台**：FastAPI 服务器。
- **厨师**：正在做推理和调用工具的 Agent。

顾客说：“给我做哲丰站点的预测。”厨师发现“哲丰”可能有 11 家站点。厨师不能随便选一家，于是按铃问前台：“请顾客选择具体站点。”

前台把问题交给服务员；服务员走到顾客面前弹出菜单。顾客选择后，服务员把纸条交回前台；前台按一次铃，厨师继续做菜。

这里最关键的两件事：

1. 厨师可以**暂停等待**，但不应该把整个餐厅冻住。
2. 前台必须把回答送回**提出这个问题的那位厨师**，而不能送给隔壁桌。

这就是本项目 Bridge（桥接器）存在的原因。

### 3.2 为什么直接 `input()` 不行

命令行阶段，`ask_user` 可直接使用 Python 的 `input()`：

```python
answer = input("请选择站点：")
```

这要求运行程序的人就在服务器终端前敲字。网页部署后，用户在浏览器里，服务器在另一台机器上：服务器的 `input()` 不会在用户屏幕上弹窗，只会傻等服务器终端。

但 Agent 中很多工具已经按同步函数编写，改成“所有工具都异步”会牵动很多现有代码。因此阶段一采用桥接方案：**工具保持同步等待，网页和 SSE 保持异步通信**。

### 3.3 现有组件的职责

| 文件 / 组件 | 通俗职责 | 技术职责 |
| --- | --- | --- |
| [`backend/tools/ask_user_tool.py`](../backend/tools/ask_user_tool.py) | 厨师按铃提问 | LangChain Tool，调用可替换的 `_input_handler(question)` |
| [`backend/app/services/agent_manager.py`](../backend/app/services/agent_manager.py) | 前台按会话找对服务员 | 为每个 session 管理 Bridge，用 `ContextVar` 路由问题 |
| [`backend/app/services/ask_user_bridge.py`](../backend/app/services/ask_user_bridge.py) | 传菜铃 + 等待椅 | `asyncio.Queue` 送问题，`threading.Event` 等回复 |
| [`backend/app/routers/chat.py`](../backend/app/routers/chat.py) | 现场协调员 | SSE 输出事件，接收 `/reply`，校验会话归属 |
| [`backend/app/main.py`](../backend/app/main.py) | 服务员 | 接收 SSE 的 `user_input_required`，展示弹窗，提交回答 |

### 3.4 一次 AskUser 的完整旅程（逐步）

以“查询哲丰 5 月 1 日发电量”为例。多个站点匹配后，工具调用 `ask_user.invoke({"question": "找到 11 个匹配站点..."})`。

1. **工具发问**：`ask_user()` 收到完整的 `question`，执行 `_input_handler(question)`。
   
   - 这里修复过一个很重要的 bug：不能传固定文案“请回复”，否则前端不知道用户到底要回答什么。
2. **全局处理器转发**：应用启动时，`AgentManager.__init__()` 调用 `set_input_handler(self._web_input_handler)`，把 CLI 默认 `input` 替换为 Web 路由器。
3. **定位本会话 Bridge**：`_web_input_handler()` 优先从 `ContextVar` 获取当前 Agent 执行绑定的 Bridge；只有没有上下文且系统恰好只有一个活动 Bridge 时才兜底。
4. **问题入队**：`AskUserBridge.ask()` 将 `_waiting = True`，然后用 `loop.call_soon_threadsafe()` 安全地向 `asyncio.Queue` 放入：
   ```python
   ("question", {"question": question})
   ```
5. **工具线程暂停**：同一个 `ask()` 紧接着执行 `_reply_event.wait(timeout=120)`。这会阻塞执行 Agent 工具的线程，但不会阻塞浏览器，也不会阻塞 FastAPI 的异步事件循环。
6. **SSE 送到网页**：`chat_stream()` 内的 `event_generator()` 从 Queue 取到 `question`，把事件名转换成 `user_input_required`，通过 SSE 流送给前端。
7. **网页弹窗**：`handleEvent()` 收到 `user_input_required` 后调用 `showAskModal(data.question)`，把真实问题放进弹窗。
8. **用户答复**：网页调用 `POST /api/chat/{session_id}/reply`，请求体只有 `{ "answer": "4" }`，但请求头仍携带 Bearer Token。
9. **回复前再做安全检查**：`reply_to_question()` 先校验该 session 的归属，再确认 Bridge 存在、仍在活动、且真的处于等待状态。否则拒绝请求。
10. **按铃唤醒**：`bridge.reply(answer)` 保存答案并执行 `_reply_event.set()`。
11. **工具继续运行**：第 5 步的 `wait()` 被唤醒，返回 `用户回复: 4` 给工具和模型；模型据此继续下一次工具调用。
12. **收尾**：Agent 完成或异常后，`finally` 会 `bridge.detach()` 清掉 Queue、事件循环引用和临时回复，避免旧状态污染下一次请求。

### 3.5 为什么要同时用 Queue 和 Event

这是最容易混淆、却最值得掌握的地方。它们方向相反，分别负责不同事情：

| 机制 | 方向 | 传递什么 | 解决的问题 |
| --- | --- | --- | --- |
| `asyncio.Queue` | 工具线程 → SSE 协程 → 浏览器 | “请问用户这个问题” | 把问题安全送到网页 |
| `threading.Event` | 浏览器 → 回复接口 → 工具线程 | “用户已经回答了”这一信号 | 把暂停的同步工具唤醒 |
| `_reply` | 回复接口 → 工具线程 | 用户回答文本 | 保存真正答案 |

可以把它想成双向快递：

```text
问题快递：Agent 工具 --Queue--> SSE --网络--> 浏览器
回答铃声：浏览器 --HTTP--> /reply --Event.set()--> Agent 工具继续
回答纸条：浏览器 --HTTP--> bridge._reply
```

为什么不用一个东西全做？因为问题的接收者是异步的 SSE 协程，回答的接收者是正在同步 `wait()` 的工具线程。两边等待方式不同，使用各自合适的同步原语，逻辑最清楚。

### 3.6 `call_soon_threadsafe()` 为什么重要

`asyncio.Queue` 属于它创建时所在的事件循环，而 Agent 工具可能在另一条线程中执行。直接跨线程调用 `queue.put_nowait()` 有概率造成难以复现的问题。

```python
self._loop.call_soon_threadsafe(
    self._queue.put_nowait,
    ("question", {"question": question}),
)
```

这句话的意思是：“请把这个放入队列的动作，安排回 Queue 所属的异步事件循环自己执行。”这是跨线程操作 asyncio 对象的标准安全姿势。

### 3.7 `ContextVar` 为什么比“一个全局变量”更好

最早的简单写法常常是：

```python
current_bridge = bridge  # 全局只有一个
```

如果用户甲和用户乙同时提问：

1. 甲刚把 `current_bridge` 设为甲的 Bridge；
2. 乙马上把它覆盖成乙的 Bridge；
3. 甲的工具提问时，问题可能发给乙。

这既是并发 bug，也可能是隐私安全问题。

当前 [`backend/app/services/agent_manager.py`](../backend/app/services/agent_manager.py) 使用：

```python
_current_bridge_var = contextvars.ContextVar("current_bridge", default=None)
token = _current_bridge_var.set(bridge)
try:
    # 当前 Agent 执行任务
finally:
    _current_bridge_var.reset(token)
```

`ContextVar` 可以理解成“每条执行任务各自口袋里的纸条”，而不是大厅墙上唯一的一块白板。`set()` 后得到的 `token` 不是 JWT，而是 ContextVar 用来恢复现场的“书签”；`finally` 中 `reset()` 能保证异常时也不遗留错的上下文。

目前还保留了一个**谨慎的兜底规则**：若工具线程没有带到上下文，只有系统中恰好一个 Bridge 处于 active 状态，才把问题交给它；多个活动 Bridge 时宁可拒绝，也不跨会话猜测路由。这个策略把“偶发失败”限定为可见错误，而不是数据串会话。

### 3.8 生命周期与状态变化

```mermaid
stateDiagram-v2
    [*] --> Detached: 创建 Bridge
    Detached --> Active: attach(queue, loop)
    Active --> Waiting: ask(question)
    Waiting --> Active: reply(answer)
    Waiting --> Active: 超时
    Waiting --> Active: cancel()
    Active --> Detached: finally -> detach()
    Detached --> [*]
```

- `is_active`：是否已绑定 SSE Queue 和事件循环。
- `is_waiting`：是否已经提出问题、正在等用户回答。
- 超时默认值来自 [`backend/app/config.py`](../backend/app/config.py) 的 `ask_user_timeout = 120` 秒。

**区分两个时间问题**：AskUser 超时是“用户太久没回答”；大模型 `Connection error` 是“调用模型服务时网络/代理/TLS 连接失败”。它们是不同故障，调大 AskUser 的 120 秒不能修复模型网络连接错误。

### 3.9 为什么聊天接口要有 session 锁

[`backend/app/routers/chat.py`](../backend/app/routers/chat.py) 通过每会话一个 `asyncio.Lock` 保证：同一个会话尚在执行时，第二条消息直接返回 HTTP 409。

否则会出现两个 Agent 同时读写同一段记忆、同时用同一个 Bridge 问问题的竞态。锁的粒度是 session，不是整个系统，所以用户甲、乙仍可并行聊天。

---

## 四、当前 AskUser 方案的设计判断

### 4.1 适合使用 AskUser 的时机

根据工具注释与当前 System Prompt，优先级是：

1. 先从当前对话和持久化记忆找已确认的站点、日期、偏好。
2. 能用业务工具查到的信息，先查工具，不问用户。
3. 多个合理候选且上下文无法唯一判断时，AskUser 让用户选择。
4. 缺少完成任务的必要参数时，AskUser 补充。
5. 每次 `predict_power` 前，必须让用户确认“最终站点全名 + 标准日期”。
6. 写库、导入、产生明显成本或不可逆影响的操作前，AskUser 二次确认。

不适合的例子：

- 用户刚刚明确说“查哲丰一车间”，下一句说“查昨天的”，不要又问他是哪一个站点。
- 需要经纬度时，不要问用户地址，先调 `get_station_location`。
- 历史实际发电量查询本身不是预测，不必强制再确认，除非站点或日期仍不明确。

一个好问题应让用户不用猜系统想问什么：

> 找到 3 个“哲丰”相关站点：1. …；2. …；3. …。请回复序号。当前查询日期为 2026-05-01。

一个不好的问题是：

> 是否继续？

### 4.2 当前方案的优点

- 保留既有同步 Tool 的写法，改造面小。
- SSE 天然适合一边流式输出、一边发出“需要用户输入”的事件。
- 每个 session 独立 Bridge，加锁后同会话不会并发乱序。
- Token + session 所有权校验，避免别人替你提交 AskUser 回复。
- 超时、取消、`finally detach` 都有明确清理点。

### 4.3 已知边界：什么情况还需要升级

| 场景 | 当前方案表现 | 更成熟的演进 |
| --- | --- | --- |
| 单机、少量并发 | 很合适 | 保持当前方案 |
| 同一进程内多会话 | ContextVar + 兜底拒绝，能避免串会话 | 继续补并发压测 |
| 多个 Uvicorn worker / 多机器部署 | 内存里的 `_bridges` 不共享，回复可能落到另一台机器 | Redis / 数据库保存交互状态 + Pub/Sub 通知 |
| 用户刷新页面或断网 | SSE 断开，当前 Bridge 会 detach 并可能取消任务 | 把 pending question 持久化，支持重连恢复 |
| 人工等待数分钟或数小时 | 工作线程被 `Event.wait()` 占用 | 改为真正异步、可暂停/恢复的工作流 |
| 多步骤、高价值审批 | 简单文本回答难审计 | 增加确认卡片、审批记录、状态机和操作者日志 |

---

## 五、其他 AskUser 实现方案：该什么时候选它们

### 方案 A：当前方案——线程 Bridge（推荐作为你的阶段一答案）

**结构**：同步工具 + `threading.Event` + `asyncio.Queue` + SSE。

**适合**：已有很多同步 LangChain Tool；单体 FastAPI；需要尽快做出真实网页交互。

**优点**：改造最小、概念边界清楚、现在已经工作。

**缺点**：等待期间占用一个执行线程；内存状态不能天然跨进程。

### 方案 B：纯异步 Tool + `asyncio.Future`

**结构**：将 `ask_user` 及调用它的 backend/Agent/工作流改成 async；创建一个 `Future` 等待 `/reply` 来 `set_result()`。

**适合**：从一开始就采用全异步工作流，或准备重构工具体系。

**优点**：不占阻塞线程，异步模型更统一。

**缺点**：同步的 LangChain Tool、数据库操作、模型调用都可能需要重构；学习和改动成本高。

伪代码：

```python
pending[interaction_id] = loop.create_future()
await send_sse_question(interaction_id, question)
answer = await pending[interaction_id]  # 协程挂起，不占线程
```

### 方案 C：状态机 / Human-in-the-loop 工作流（生产推荐方向）

**结构**：Agent 执行到“需要审批”节点时保存 checkpoint（状态），返回 `waiting_for_user`；用户回复后创建新任务从 checkpoint 恢复。

**适合**：预测确认、数据导入、审批链、多轮人工介入、需要断线恢复的生产产品。

**优点**：不需要让一个线程长时间等待；可审计、可重试、可跨机器、可恢复；很适合写入简历的“Human-in-the-loop Agent 工作流”。

**缺点**：需要定义状态结构、持久化、幂等性和恢复逻辑，工程量更大。

可以采用 LangGraph 的 interrupt/checkpoint 思路，或自己实现一张 `agent_interactions` 表：

| interaction_id | session_id | user_id | question | status | answer | expires_at |
| --- | --- | --- | --- | --- | --- | --- |
| uuid | abc | 3 | 请选择站点 | waiting | NULL | … |

状态：`waiting → answered → resumed / expired / cancelled`。

### 方案 D：消息队列与事件总线

**结构**：Agent worker 把“问题”写入 Redis Streams/RabbitMQ；前端通过 WebSocket/SSE 收到事件；回复写回队列或 Redis。

**适合**：多 worker、多实例部署、任务队列、较高并发。

**优点**：跨机器可靠路由；可削峰、可观测。

**缺点**：运维复杂度上升；对当前个人项目过早引入会掩盖核心学习目标。

### 方案 E：前端预确认，而不是中途打断

**结构**：用户点击“预测”时，前端先展示站点和日期确认卡片；确认后再调用预测接口。

**适合**：参数字段固定、流程简单的业务，例如“预测某站点某日发电量”。

**优点**：用户体验直观，后端 Agent 少一次等待。

**缺点**：无法覆盖模型运行中才发现的歧义；仍需要 AskUser / 状态机处理例外情况。

**产品建议**：Vue 正式页面可以优先为“预测”做确认卡片，但保留 AskUser 作为 Agent 在运行中发现信息不足时的通用兜底能力。

---

## 六、把当前方案讲成面试答案

可以用下面这段话概括：

> 我在 FastAPI + LangChain 的光伏 Agent 中实现了 Human-in-the-loop 交互。由于 Agent 工具是同步调用、Web 层使用 asyncio 和 SSE，我通过每会话独立的 Bridge 连接两种执行模型：工具线程将问题通过线程安全方式投递到 `asyncio.Queue`，SSE 把它以 `user_input_required` 事件发送给前端；用户回复经过 JWT 身份认证与会话归属校验后，通过 `threading.Event` 唤醒等待中的工具线程。为避免多用户串会话，我使用 `ContextVar` 绑定当前执行上下文，并为同一 session 设置互斥锁。下一步会将内存态 Bridge 演进为基于 checkpoint 的可恢复工作流，支持多实例部署与断线恢复。

不要只背名词。面试官追问“为什么用 Queue 和 Event”时，回答：**Queue 用来把问题交给异步 SSE；Event 用来把同步等待的工具唤醒；它们分别对应两个方向与两种等待模型。**

---

## 七、动手复盘：建议你亲自验证的清单

### Token 认证

- [ ] 登录成功后，查看网络请求，确认响应包含 `access_token`、`expires_in`。
- [ ] 检查聊天请求 Headers，确认有 `Authorization: Bearer ...`。
- [ ] 去掉 Authorization 后请求 `GET /api/sessions`，应得到 401。
- [ ] 用 A 用户 Token 请求 B 用户的 `session_id`，应得到 403 或 404，绝不能读取消息。
- [ ] 等 Token 过期后再请求，应得到 401，并由前端回到登录页。
- [ ] 数据库 `user_info.password_hash` 应以 `$argon2` 开头，绝不出现明文密码。

### AskUser 正常流程

- [ ] 新建会话，输入含糊站点名（如“哲丰”），确认网页弹出真实的站点问题，而不是固定“请回复”。
- [ ] 选择一个序号，确认 Agent 从选择处继续，而不是重新开始对话。
- [ ] 发起预测，确认在调用 `predict_power` 前出现“站点全名 + YYYY-MM-DD”的确认问题。
- [ ] 不回复，等待 120 秒，确认模型收到“超时未回复”而非无限卡住。
- [ ] 在等待弹窗时，尝试向同一会话再发消息，确认返回 409 或前端阻止发送。
- [ ] 用另一个用户的 Token 调用 `/reply`，确认无法替别人回答。

### 代码阅读顺序（推荐）

1. `backend/tools/ask_user_tool.py`：先理解“提出问题”的最小接口。
2. `backend/app/services/ask_user_bridge.py`：理解 Queue、Event、等待和唤醒。
3. `backend/app/routers/chat.py`：理解问题如何变成 SSE、回复如何回到 Bridge。
4. `backend/app/services/agent_manager.py`：最后理解 session 路由、ContextVar 和并发保护。
5. `backend/app/main.py`：看前端如何监听事件与提交答案。

---

## 八、下一阶段的产品化路线（建议）

1. **Vue 3 前端**：Pinia 管理登录态；Axios 自动注入 Token；聊天流继续以 `fetch` 读取；预测使用确认卡片。
2. **认证完善**：Access Token 短期化 + Refresh Token 轮换；Refresh Token 使用 HttpOnly Cookie；增加登出和令牌失效策略。
3. **AskUser 生产化**：把 pending question 持久化为交互状态，支持刷新页面、超时处理、审批记录和重连恢复。
4. **Agent 工程化**：把高风险写操作、预测确认变成显式状态节点，建设可回放的任务日志与评估集。
5. **部署与可观测性**：统一请求 ID、session ID、interaction ID；监控模型调用耗时、错误率、AskUser 超时率和用户放弃率。

你现在的阶段一已经不是“调用了一个大模型接口”，而是具备了 Agent 产品的两条基本骨架：**可信身份**和**可控的人机协作**。
