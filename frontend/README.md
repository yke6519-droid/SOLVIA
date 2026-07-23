# SolarAgent Frontend

SolarAgent 前端是一个独立的 Vue 3 + Vite 单页工作台，当前已接入认证、会话管理、SSE 流式 Agent、AskUser、结构化 ECharts、对话附件、发电量导入、生成文件下载和历史结果恢复。

## 技术栈

| 依赖 | 用途 |
| --- | --- |
| Vue 3 | 响应式状态和组件 |
| Vite 6 | 开发服务器和生产构建 |
| Axios | 普通 REST、刷新重放、附件上传、Blob 下载 |
| Fetch + ReadableStream | `POST /api/chat/stream` SSE |
| ECharts | 结构化图表渲染 |
| MarkdownIt | Agent Markdown 渲染 |
| DOMPurify | HTML 安全清洗 |

当前未引入 Vue Router 和 Pinia。`src/router.js` 是项目自己的轻量路由守卫；主要页面和业务状态仍集中在 `App.vue`。

## 目录结构

```text
frontend/
├── src/
│   ├── components/
│   │   ├── AskUserCard.vue
│   │   ├── ConversationAttachmentPicker.vue
│   │   ├── ImportDataDialog.vue
│   │   ├── MarkdownMessage.vue
│   │   └── PowerChart.vue
│   ├── views/
│   │   └── LoginView.vue
│   ├── api.js
│   ├── App.vue
│   ├── config.js
│   ├── main.js
│   ├── router.js
│   └── styles.css
├── index.html
├── package.json
└── vite.config.js
```

## 页面与交互

### 登录与主题

- `/login`：独立登录页。
- `/workspace`：主工作台。
- 无有效 Access Token 时路由守卫自动返回登录页。
- 登录失效时展示只能选择“重新登录”的弹窗。
- 支持浅色、深色主题。

### 欢迎页与会话

- 进入工作台只加载会话列表，不自动打开最近会话。
- 点击“回到首页/新建会话”不会立即创建空会话。
- 首次发送消息时才调用后端创建会话。
- 首页快捷卡片只填充短语，不自动发送。
- 会话列表支持加载更多、选择、重命名和删除。
- 历史消息支持向上分页加载。
- 同一会话、同一分页游标的历史请求会去重。

前后端分页大小来自 `../shared/pagination.json`，当前为：

```json
{
  "history_page_size": 6,
  "session_page_size": 5
}
```

### 流式 Agent

普通接口使用 Axios；流式接口必须使用 Fetch：

```text
POST /api/chat/stream
  → Fetch
  → response.body.getReader()
  → 解析 SSE event/data
  → 增量更新 Vue 消息
```

当前处理的主要事件：

| 事件 | 前端行为 |
| --- | --- |
| `token` | 追加 Agent 文本，形成打字机效果 |
| `usage` | 更新本轮 Token 用量 |
| `tool_start` | 添加“正在调用工具”步骤 |
| `tool_end` | 记录工具结果，恢复图表或文件卡片 |
| `chart_spec` | 交给 `PowerChart.vue` 渲染 |
| `user_input_required` | 展示 `AskUserCard.vue` |
| `error` | 按结构化异常码展示错误 |
| `done` | 完成消息并保存最终输出 |

用户可以点击“停止”中断当前 Fetch 流。

### Markdown 安全渲染

`MarkdownMessage.vue`：

- 禁止 Markdown 内嵌原始 HTML。
- 使用 DOMPurify 清洗脚本、iframe、表单、事件属性和危险协议。
- 清除旧消息中的服务器本地路径和 `/api/files/...` 下载链接。
- 文件只能通过文件卡片下载。

### ECharts

`PowerChart.vue` 接收后端 `ChartSpec`，负责：

- 折线图和柱状图渲染；
- 浅色/深色主题；
- 坐标轴名称和单位；
- Tooltip 精确查看某时刻/日期数值；
- 多序列图例和前端固定配色；
- Resize 响应；
- 历史 ChartSpec 恢复。

前端不执行 Agent 生成的 JavaScript、HTML 或 formatter。

### 对话附件

输入框加号打开 `ConversationAttachmentPicker.vue`：

- 支持 `.xlsx`、`.xls`、`.csv`、`.txt`、`.md`。
- 文件先上传到 `/api/attachments`，返回 `attachment_id`。
- 发送对话时只携带附件 ID。
- 切换会话会清除尚未发送的附件。
- 当前组件一次选择一个文件，后端单条消息最多接受 5 个附件。

### 发电量数据导入

顶部“导入数据”打开 `ImportDataDialog.vue`：

1. 调用 `/api/import/power/preview`。
2. 展示文件清洗和站点解析预览。
3. 用户确认后调用 `/api/import/power/execute`。
4. 后端将发电量数据写入 MySQL。

该入口与对话附件不同：导入弹窗是明确的两阶段数据入库流程，对话附件则交由 Agent 根据用户意图选择读取、校验或导入工具。

### Agent 生成文件下载

Agent 生成文件时，SSE 只返回：

```json
{
  "file_id": "file_xxx",
  "filename": "结果.xlsx",
  "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "size_bytes": 12345,
  "status": "ready"
}
```

前端展示文件卡片。点击下载后：

1. `ensureFreshAccessToken()` 检查或刷新 Access Token。
2. Axios 请求 `/api/files/{file_id}/download`。
3. 请求携带 Bearer Token，响应类型为 `blob`。
4. 前端创建临时 Object URL。
5. 触发隐藏 `<a download>` 保存文件。
6. 释放 Object URL。

不能用普通 `<a href>` 直接跳转，因为浏览器不会自动把 localStorage 中的 Bearer Token 加到导航请求。

文件卡片随助手消息保存，历史消息接口返回 `files` 后可以重新渲染并下载。

### Token 用量

流式接口通过 `usage` 或 `done.usage` 返回本轮模型消耗。前端显示：

```text
本轮消耗 Token：输入 x · 输出 y · 合计 z
```

后端会把用量写入助手消息 `response_metadata.token_usage`，因此历史会话也能恢复。

## API 层

`src/api.js` 维护两个 Axios 实例：

- `publicClient`：登录、刷新等公开认证接口。
- `authClient`：自动附加 Bearer Token 的业务接口。

`authClient` 遇到 401 时：

1. 只允许原请求重试一次。
2. 调用 Refresh 接口。
3. 保存新的 Access Token。
4. 重放原请求。
5. Refresh 失败则清理本地状态并触发登录失效弹窗。

`streamChat()` 因为需要读取流体，使用 Fetch 实现，并单独处理一次刷新后重连。

## 本地认证状态

Access Token、用户信息、过期时间和 Token 生命周期当前保存在 localStorage。Refresh Token 由浏览器通过 HttpOnly Cookie 管理，JavaScript 无法读取。

主动续期只在用户近期有真实活动时发生。工作台记录键盘、鼠标、触摸、窗口重新聚焦和页面重新可见等活动；超过 30 分钟未活动会结束前端登录状态。

## 环境配置

开发模式默认使用相对 `/api`，由 Vite 代理到 FastAPI：

```js
server: {
  port: 5173,
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:8001'
    }
  }
}
```

如需直接访问其他后端，在 `frontend/.env.local` 中配置：

```env
VITE_API_BASE_URL=http://127.0.0.1:8001/api
```

修改构建时环境变量后必须重新执行 `npm run build`，旧的 `dist` 不会自动更新。

## 安装与启动

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm install
npm run dev
```

开发地址为 `http://localhost:5173`。如果该端口被占用，Vite 会自动尝试下一个端口。

## 生产构建

```powershell
cd D:\AAA_myProjects\howso\myAgent\solar_agent\frontend
npm run build
npm run preview
```

- 构建结果：`frontend/dist/`。
- `vite preview` 默认端口：4173。
- `vite preview` 只用于本地预览，不是正式生产服务器。
- 正式部署应使用 Nginx 等静态服务器，并把 `/api` 反向代理到 FastAPI；否则构建前应设置完整的 `VITE_API_BASE_URL`。

## 构建检查

```powershell
npm run build
```

如果出现 `Cannot find module ... vite.js`，说明 `node_modules` 不完整，应在 `frontend/` 重新执行：

```powershell
npm install
```

## 当前前端限制

- `App.vue` 仍承担大部分工作台、认证、会话和流式状态，尚未完成模块化拆分。
- 未引入 Pinia，登录态和全局业务状态仍由 `App.vue` 与 localStorage 管理。
- 当前路由是自研轻量实现，只有登录页和工作台。
- 尚未提供传统筛选式站点数据仪表盘。
- 尚未提供通用的多 Sheet 工作簿浏览器。
- 图表导出按钮仍是占位提示；结构化数据导出应通过 Agent 生成文件卡片完成。
- 历史消息虽已分页和请求去重，仍可继续做虚拟列表、缓存和图表懒加载。
- 当前主 JavaScript 产物超过 500 kB，Vite 构建会提示分包警告，后续需要通过动态导入或 `manualChunks` 拆分 ECharts 等大依赖。
