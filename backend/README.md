# Backend

SolarAgent 的 FastAPI、Agent 编排、业务工具和数据库脚本统一位于此目录。

```text
backend/
├─ app/       FastAPI 入口、路由、服务、Schema
├─ Agent/     AgentExecutor、Prompt、Memory、LLM
├─ tools/     光伏预测、查询、气象、文件、图表和导入工具
├─ sql/       数据库脚本和迁移
└─ temp/      文件读写功能的独立测试环境
```

从项目根目录启动：

```powershell
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8001
```
