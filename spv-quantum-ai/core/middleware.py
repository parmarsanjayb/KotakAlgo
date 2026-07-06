"""
core/middleware.py
──────────────────
Production-grade FastAPI middleware for SPV Quantum AI.

Provides:
  - CorrelationIDMiddleware  — injects X-Correlation-ID on every request/response
  - RequestLoggingMiddleware — structured access log (method, path, status, ms)
"""

import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.logging import get_logger

logger = get_logger("http_middleware")

# Context-variable so handlers can read the current correlation ID
from contextvars import ContextVar

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Reads the X-Correlation-ID header from incoming requests.
    If absent, generates a fresh UUID4 correlation ID.
    Stores it in a ContextVar for downstream access, and echoes it back
    in the response header.
    """

    HEADER = "X-Correlation-ID"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        corr_id = request.headers.get(self.HEADER) or str(uuid.uuid4())
        token = correlation_id_var.set(corr_id)
        try:
            response: Response = await call_next(request)
            response.headers[self.HEADER] = corr_id
            return response
        finally:
            correlation_id_var.reset(token)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Emits a structured log line for every HTTP request:
      method, path, status_code, duration_ms, correlation_id
    Skips health-check noise (e.g. /api/health/status polling).
    """

    _SKIP_PATHS = {"/api/health/status", "/api/status"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        t0 = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)

        logger.info(
            f"{request.method} {request.url.path} → {response.status_code} ({duration_ms}ms)",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            correlation_id=correlation_id_var.get(""),
        )
        return response


def get_correlation_id() -> str:
    """Helper callable usable from any async handler to read the current correlation ID."""
    return correlation_id_var.get("")
