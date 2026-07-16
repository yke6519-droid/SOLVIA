"""SolarAgent FastAPI 应用入口。"""
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.routers import auth, chat, sessions
from backend.app.services.summary_task_manager import summary_task_manager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def _configure_proxy_environment() -> None:
    """避免本地服务和指定云服务错误经过系统代理。"""
    proxy_env_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]
    for key in proxy_env_keys:
        os.environ.pop(key, None)

    no_proxy = (
        "dashscope.aliyuncs.com,"
        "bailian.cn-beijing.aliyuncs.com,"
        "127.0.0.1,localhost"
    )
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""
    _configure_proxy_environment()

    app = FastAPI(
        title="SolarAgent API",
        description="面向光伏运营场景的智能分析与对话 API",
        version="1.0.0",
    )

    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)

    @app.get("/")
    async def api_info():
        return {
            "service": "SolarAgent API",
            "status": "ok",
            "docs": "/docs",
        }

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "SolarAgent"}

    @app.on_event("shutdown")
    async def shutdown_background_tasks():
        await summary_task_manager.shutdown()

    return app


app = create_app()
