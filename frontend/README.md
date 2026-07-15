# SolarAgent Frontend

Vue 3 + Vite 独立前端。当前已接入登录、会话管理、SSE 流式对话、ask_user 回复以及实时结构化图表渲染。

## 启动

```powershell
npm install
npm run dev
```

开发服务器默认运行在 `http://localhost:5173`，`/api` 请求会代理到 FastAPI `http://127.0.0.1:8001`。

## 当前实现

- `/login` 独立登录页，登录态过期会自动回退登录页
- Axios 负责登录、会话列表、新建、历史消息、删除和 ask_user 回复等普通 REST 接口
- Fetch + ReadableStream 负责 `POST /api/chat/stream` SSE 流式事件
- 支持 `token`、`tool_start`、`tool_end`、`user_input_required`、`error`、`done` 事件\n- 支持范围图表的逐小时、多日按日汇总、预测/实际多序列渲染，并兼容多个单日图表事件合并
- 工作台进入时只加载会话列表，不自动打开最近会话，主区域保留欢迎页
- 点击会话后加载历史消息；消息模型预留 `chartData` 字段，后端未来返回 `chart_data` 后可恢复历史图表
- `VITE_API_BASE_URL` 可覆盖 API 前缀，默认建议保留 `/api` 代理路径

## 后续方向

- 将登录态和当前用户信息迁移到 Pinia，保留持久化层负责刷新后恢复
- 扩展历史消息接口，返回工具事件和结构化 `chart_data`
- 接入文件上传、结果导出和任务详情接口
### 图表渲染

图表消息由 `PowerChart.vue` 统一使用 ECharts 渲染，后端只返回标准化 `chart_data`。折线图、柱状图和饼图均使用同一组件入口，时间序列图启用坐标轴 tooltip，可查看具体日期/时刻的发电量。

首次接入 ECharts 后，在 `frontend` 目录执行 `npm install` 同步依赖与 lockfile，再执行 `npm run dev`。