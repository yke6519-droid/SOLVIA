# Backend

SolarAgent 的 FastAPI、Agent 编排、业务工具和数据库脚本统一位于此目录。

```text
backend/
├─ app/       FastAPI 入口、路由、服务、Schema
├─ Agent/     AgentExecutor、Prompt、Memory、LLM
├─ tools/     光伏预测、查询、气象、文件、图表和导入工具
├─ sql/       数据库脚本和迁移
└─ temp/      后端运行时文件
```

从项目根目录启动：

```powershell
python -m backend.app.main
# 或
uvicorn backend.app.main:app --reload --port 8001
```