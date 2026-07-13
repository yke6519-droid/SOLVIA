"""
user_service.py - 用户服务
==========================
用户注册、登录、密码校验。

对应 Spring 的 @Service 层。
当前阶段不做 JWT，用 user_id 直传，前后端分离时再加 Token。
"""
import os
import hashlib
import secrets
import logging
from typing import Optional, Tuple
from urllib.parse import urlparse

import pymysql

logger = logging.getLogger(__name__)


def _get_conn():
    """从环境变量获取 MySQL 连接。"""
    url = os.getenv("MYSQL_URL")
    if not url:
        raise RuntimeError("MYSQL_URL 未配置")
    # 使用url解析工具对MySQL_URL进行解析
    p = urlparse(url)
    return pymysql.connect(
        host=p.hostname,
        port=p.port or 3306,
        user=p.username,
        password=p.password,
        database=p.path.lstrip("/"),
        charset="utf8mb4",
    )

# 对密码加密(采用加盐的方法)
def _hash_password(password: str) -> str:
    """
    密码哈希: salt + sha256。
    简单实现，后续可替换为 bcrypt/passlib。
    格式: salt$hash
    """
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${hashed}"


def _verify_password(password: str, stored: str) -> bool:
    """校验密码。"""
    try:
        salt, hashed = stored.split("$", 1)
        return hashlib.sha256((salt + password).encode()).hexdigest() == hashed
    except (ValueError, AttributeError):
        return False


def register_user(username: str, password: str, display_name: str = "") -> dict:
    """
    注册新用户。

    返回:
        {"user_id": int, "username": str, "role": str, "display_name": str}

    异常:
        ValueError — 用户名已存在
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 检查用户名是否已存在
            cur.execute("SELECT id FROM user_info WHERE username=%s", (username,))
            if cur.fetchone():
                raise ValueError(f"用户名 '{username}' 已存在")

            # 插入新用户
            password_hash = _hash_password(password)
            cur.execute(
                "INSERT INTO user_info (username, password_hash, display_name) VALUES (%s, %s, %s)",
                (username, password_hash, display_name or username),
            )
            conn.commit()
            user_id = cur.lastrowid
            logger.info(f"用户注册成功: id={user_id}, username={username}")
            return {
                "user_id": user_id,
                "username": username,
                "role": "user",
                "display_name": display_name or username,
            }
    finally:
        conn.close()


def login_user(username: str, password: str) -> dict:
    """
    用户登录。

    返回:
        {"user_id": int, "username": str, "role": str, "display_name": str}

    异常:
        ValueError — 用户名不存在或密码错误
        RuntimeError — 账号已禁用
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash, role, display_name, status FROM user_info WHERE username=%s",
                (username,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"用户名 '{username}' 不存在")

            user_id, db_username, stored_hash, role, display_name, status = row
            if status == 0:
                raise RuntimeError("该账号已被禁用，请联系管理员")

            if not _verify_password(password, stored_hash):
                raise ValueError("密码错误")

            logger.info(f"用户登录成功: id={user_id}, username={username}")
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
