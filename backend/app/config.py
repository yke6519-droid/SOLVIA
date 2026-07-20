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


def _load_history_page_size() -> int:
    """读取前后端共用的历史消息分页大小。"""
    config_path = Path(__file__).resolve().parents[2] / "shared" / "pagination.json"
    try:
        value = int(json.loads(config_path.read_text(encoding="utf-8"))["history_page_size"])
        return max(1, min(value, 200))
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 4


HISTORY_PAGE_SIZE = _load_history_page_size()


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
    jwt_access_token_expire_seconds: int = 3600


settings = Settings()
