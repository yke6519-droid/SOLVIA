"""
config.py - FastAPI 配置
========================
使用 pydantic-settings 统一读取 .env，替代散落的 os.getenv。
对应 Spring 的 application.properties + @ConfigurationProperties。
"""
import os
import json
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# 确保 solar_agent/.env 被加载
load_dotenv()


def _load_pagination_config() -> dict:
    """读取前后端共用的分页配置。"""
    config_path = Path(__file__).resolve().parents[2] / "shared" / "pagination.json"
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {}


_PAGINATION_CONFIG = _load_pagination_config()
HISTORY_PAGE_SIZE = max(1, min(int(_PAGINATION_CONFIG.get("history_page_size", 4)), 200))
SESSION_PAGE_SIZE = max(1, min(int(_PAGINATION_CONFIG.get("session_page_size", 20)), 100))


class Settings(BaseSettings):
    """应用配置。只声明 FastAPI 需要的字段，其余 .env 变量自动忽略。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略 .env 中未声明的变量（API_KEY 等）
    )

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"

    # ask_user 超时（秒）
    ask_user_timeout: int = 120

    jwt_secret_key: str = ""
    # Authentication session policy. Values can be overridden through .env.
    jwt_access_token_expire_seconds: int = 900
    jwt_refresh_token_expire_seconds: int = 7 * 24 * 60 * 60
    jwt_refresh_cookie_secure: bool = False


settings = Settings()
