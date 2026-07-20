"""Shared SQLAlchemy engine for the FastAPI process.

The application uses one process-level Engine so SQLAlchemy can reuse its
connection pool. Individual queries still acquire short-lived connections via
``with get_engine().connect()`` or ``with get_engine().begin()``.
"""

from __future__ import annotations

import os
from threading import Lock

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine


load_dotenv()

_engine: Engine | None = None
_engine_lock = Lock()


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy Engine, creating it lazily once."""

    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                mysql_url = os.getenv("MYSQL_URL")
                if not mysql_url:
                    raise RuntimeError("MYSQL_URL 未配置")
                _engine = create_engine(
                    mysql_url,
                    pool_pre_ping=True,
                    pool_recycle=1800,
                )
    return _engine


def dispose_engine() -> None:
    """Dispose the shared pool during application shutdown or test teardown."""

    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
