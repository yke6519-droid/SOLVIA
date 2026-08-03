"""SolarAgent FastAPI 应用入口。"""
import logging
import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.routers import auth, attachments, chat, files, import_data, sessions
from backend.app.services.summary_task_manager import summary_task_manager
from backend.app.database import dispose_engine
from backend.app.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    internal_error_handler,
    validation_error_handler,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

def _configure_proxy_environment() -> None:
    """避免本地服务和指定云服务错误经过系统代理。"""
    # ChatOpenAI/httpx 会从 HTTP(S)_PROXY 读取代理配置。
    # 这里不能再全局删除代理变量，否则代理开启时外部模型请求会退回直连。
    # 同时合并已有 NO_PROXY，避免覆盖用户或运行环境已经配置的域名。
    no_proxy_items: list[str] = []
    for key in ("NO_PROXY", "no_proxy"):
        for value in os.environ.get(key, "").split(","):
            value = value.strip()
            if value and value not in no_proxy_items:
                no_proxy_items.append(value)

    # 本地前后端、数据库和回环地址不应经过 Clash/Mihomo。
    for local_host in ("127.0.0.1", "localhost", "::1"):
        if local_host not in no_proxy_items:
            no_proxy_items.append(local_host)

    no_proxy = ",".join(no_proxy_items)
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
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)

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
    app.include_router(attachments.router)
    app.include_router(files.router)
    app.include_router(import_data.router)
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
        dispose_engine()

    return app


app = create_app()
