from __future__ import annotations

import json
import logging
import os
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("paperforge.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request as structured JSON to stdout."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - start) * 1000)

        log_level = _level_for_status(response.status_code)
        payload = json.dumps(
            {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "client": request.client.host if request.client else "unknown",
            },
            ensure_ascii=False,
        )
        logger.log(log_level, payload)
        return response


def _level_for_status(status: int) -> int:
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.INFO
