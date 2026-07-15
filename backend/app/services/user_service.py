"""用户服务：注册、登录和 Argon2id 密码校验。"""
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import pymysql
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

logger = logging.getLogger(__name__)
_PASSWORD_HASHER = PasswordHasher()


def _get_conn():
    """从环境变量获取 MySQL 连接。"""
    url = os.getenv("MYSQL_URL")
    if not url:
        raise RuntimeError("MYSQL_URL 未配置")
    parsed = urlparse(url)
    return pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/"),
        charset="utf8mb4",
    )


def _hash_password(password: str) -> str:
    """使用 Argon2id 哈希密码。"""
    return _PASSWORD_HASHER.hash(password)


def _verify_password(password: str, stored: str) -> bool:
    """只校验 Argon2id 密码哈希；旧 SHA256 哈希会被拒绝。"""
    if not stored or not stored.startswith("$argon2"):
        return False
    try:
        return _PASSWORD_HASHER.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def register_user(username: str, password: str, display_name: str = "") -> dict:
    """注册新用户。"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM user_info WHERE username=%s", (username,))
            if cur.fetchone():
                raise ValueError(f"用户名 '{username}' 已存在")
            cur.execute(
                "INSERT INTO user_info (username, password_hash, display_name) VALUES (%s, %s, %s)",
                (username, _hash_password(password), display_name or username),
            )
            conn.commit()
            user_id = cur.lastrowid
            return {
                "user_id": user_id,
                "username": username,
                "role": "user",
                "display_name": display_name or username,
            }
    finally:
        conn.close()


def login_user(username: str, password: str) -> dict:
    """登录用户"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash, role, display_name, status "
                "FROM user_info WHERE username=%s",
                (username,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("用户名或密码错误")
            user_id, db_username, stored_hash, role, display_name, status = row
            if status == 0:
                raise RuntimeError("该账号已被禁用，请联系管理员")
            if not _verify_password(password, stored_hash):
                raise ValueError("用户名或密码错误")
            logger.info("用户登录成功: id=%s, username=%s", user_id, db_username)
            return {
                "user_id": user_id,
                "username": db_username,
                "role": role,
                "display_name": display_name,
            }
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    """根据 user_id 查询用户信息。"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, role, display_name, status FROM user_info WHERE id=%s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "user_id": row[0],
                "username": row[1],
                "role": row[2],
                "display_name": row[3],
                "status": row[4],
            }
    finally:
        conn.close()