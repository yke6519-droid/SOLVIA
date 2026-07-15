# SolarAgent Frontend

Vue 3 + Vite 独立前端原型。当前阶段使用本地静态数据模拟登录后的会话、AskUser 确认、流式执行状态和预测结果卡片。

## 启动

```powershell
pnpm install
pnpm dev
```

开发服务器默认运行在 `http://localhost:5173`，`/api` 请求会代理到 FastAPI `http://127.0.0.1:8001`。

## 当前原型状态

- 左侧会话列表、新建会话和最近结果
- 空会话快捷任务
- 对话流、执行中状态和停止按钮
- 站点 / 日期 / 动作确认卡片
- 预测指标、SVG 曲线和执行详情
- 真实 API 尚未接入，后续将接入 JWT、会话、SSE 和图表事件