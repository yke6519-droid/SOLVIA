后端启动脚本
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8001

前端启动脚本（生产模式）
npm run build
npm run preview

前端启动脚本（开发模式）
npm run dev