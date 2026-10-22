"""Exception handlers that turn every failure into the same JSON error envelope."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError

log = logging.getLogger("jarvis.errors")


def _envelope(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": request.scope.get("state", {}).get("request_id"),
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("Application error: %s", exc.message, extra={"code": exc.code})
        return JSONResponse(status_code=exc.status_code, content=_envelope(request, exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Input values are deliberately omitted so secrets are never echoed back.
        fields = [
            {"field": ".".join(str(part) for part in err.get("loc", ())[1:]), "message": err.get("msg", "")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_envelope(request, "validation_error", "Some fields are invalid.", {"fields": fields}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        message = str(exc.detail) if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(status_code=exc.status_code, content=_envelope(request, code, message))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content=_envelope(request, "internal_error", "Something went wrong on the server. Check the Developer Console for details."),
        )
