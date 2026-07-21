"""统一的业务异常与 HTTP 错误响应协议。"""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langchain_core.tools import ToolException
import logging

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_REFRESH_TOKEN_MISSING = "AUTH_REFRESH_TOKEN_MISSING"
    AUTH_REFRESH_TOKEN_EXPIRED = "AUTH_REFRESH_TOKEN_EXPIRED"
    AUTH_REFRESH_TOKEN_INVALID = "AUTH_REFRESH_TOKEN_INVALID"
    AUTH_REFRESH_TOKEN_REVOKED = "AUTH_REFRESH_TOKEN_REVOKED"
    AUTH_LOGIN_FAILED = "AUTH_LOGIN_FAILED"
    AUTH_ACCOUNT_DISABLED = "AUTH_ACCOUNT_DISABLED"
    AUTH_CONFIG_ERROR = "AUTH_CONFIG_ERROR"
    USER_ALREADY_EXISTS = "USER_ALREADY_EXISTS"
    REQUEST_VALIDATION_FAILED = "REQUEST_VALIDATION_FAILED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_FORBIDDEN = "SESSION_FORBIDDEN"
    SESSION_BUSY = "SESSION_BUSY"
    SESSION_CURSOR_INVALID = "SESSION_CURSOR_INVALID"
    SESSION_TITLE_INVALID = "SESSION_TITLE_INVALID"
    CHAT_REQUEST_INVALID = "CHAT_REQUEST_INVALID"
    CHAT_AGENT_FAILED = "CHAT_AGENT_FAILED"
    CHAT_AGENT_TIMEOUT = "CHAT_AGENT_TIMEOUT"
    CHAT_REPLY_NOT_WAITING = "CHAT_REPLY_NOT_WAITING"
    CHAT_STREAM_INTERRUPTED = "CHAT_STREAM_INTERRUPTED"
    STATION_NOT_FOUND = "STATION_NOT_FOUND"
    DATA_NOT_FOUND = "DATA_NOT_FOUND"
    DATA_SOURCE_UNSUPPORTED = "DATA_SOURCE_UNSUPPORTED"
    DATA_RANGE_INVALID = "DATA_RANGE_INVALID"
    DATA_POINT_LIMIT_EXCEEDED = "DATA_POINT_LIMIT_EXCEEDED"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """可安全暴露给前端的结构化业务异常。"""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code.value if isinstance(code, ErrorCode) else str(code)
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.retryable = retryable
        self.headers = headers or {}


class ToolError(ToolException):
    """Agent-facing tool error that preserves a machine-readable code."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code.value if isinstance(code, ErrorCode) else str(code)
        self.message = message
        self.details = details or {}
        self.retryable = retryable


def _error_payload(
    *,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "status": status_code,
            "retryable": retryable,
            "details": details or {},
            "request_id": request_id or f"req_{uuid4().hex[:16]}",
        }
    }


def error_response(
    *,
    code: ErrorCode | str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
    headers: dict[str, str] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    code_value = code.value if isinstance(code, ErrorCode) else str(code)
    request_id = request_id or f"req_{uuid4().hex[:16]}"
    response_headers = dict(headers or {})
    response_headers.setdefault("X-Request-ID", request_id)
    response = JSONResponse(
        status_code=status_code,
        content=_error_payload(
            code=code_value,
            message=message,
            status_code=status_code,
            details=details,
            retryable=retryable,
            request_id=request_id,
        ),
        headers=response_headers,
    )
    return response


def _http_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: ErrorCode.AUTH_REQUIRED.value,
        403: ErrorCode.SESSION_FORBIDDEN.value,
        404: "RESOURCE_NOT_FOUND",
        409: "CONFLICT",
        422: ErrorCode.REQUEST_VALIDATION_FAILED.value,
        429: "RATE_LIMITED",
        502: "UPSTREAM_ERROR",
        503: "SERVICE_UNAVAILABLE",
        504: "UPSTREAM_TIMEOUT",
    }.get(status_code, ErrorCode.INTERNAL_ERROR.value)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
        retryable=exc.retryable,
        headers=exc.headers,
    )


async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
    return error_response(
        code=_http_code(exc.status_code),
        message=detail,
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        code=ErrorCode.REQUEST_VALIDATION_FAILED,
        message="请求参数校验失败",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details={"fields": exc.errors()},
    )


async def internal_error_handler(_: Request, exc: Exception) -> JSONResponse:
    request_id = f"req_{uuid4().hex[:16]}"
    logger.exception("Unhandled request error: request_id=%s", request_id)
    return error_response(
        code=ErrorCode.INTERNAL_ERROR,
        message="服务暂时不可用，请稍后重试",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        retryable=True,
        request_id=request_id,
    )
