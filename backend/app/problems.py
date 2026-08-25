"""统一的 RFC 9457 风格错误响应。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ProblemException(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str,
        fields: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.fields = fields or {}
        self.extra = extra or {}
        self.headers = headers or {}


def problem_response(request: Request, exc: ProblemException) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    body: dict[str, Any] = {
        "type": f"urn:partyops:problem:{exc.code.lower()}",
        "title": exc.title,
        "status": exc.status,
        "detail": exc.detail,
        "code": exc.code,
        "trace_id": trace_id,
    }
    if exc.fields:
        body["fields"] = exc.fields
    body.update(exc.extra)
    return JSONResponse(
        status_code=exc.status,
        content=body,
        headers=exc.headers,
        media_type="application/problem+json",
    )


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemException)
    async def handle_problem(request: Request, exc: ProblemException) -> JSONResponse:
        return problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields: dict[str, str] = {}
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"] if part != "body")
            fields[location or "body"] = str(error["msg"])
        return problem_response(
            request,
            ProblemException(
                422,
                "VALIDATION_ERROR",
                "输入内容有误",
                "请检查标记字段后重试。",
                fields=fields,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
        logging.getLogger("partyops").exception(
            "unhandled_request_error trace_id=%s", trace_id, exc_info=exc
        )
        return problem_response(
            request,
            ProblemException(
                500,
                "INTERNAL_ERROR",
                "系统暂时无法完成操作",
                f"请稍后重试；如持续出现，请在运行诊断中提供追踪编号 {trace_id}。",
            ),
        )
