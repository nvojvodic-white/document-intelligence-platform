import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Optional static gate in front of /api/*, on top of per-user tokens.

    Disabled when PLATFORM_API_KEY is unset.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Preflight carries no credentials by design; gating it would break
        # every cross-origin call before the real request is made.
        if request.method == "OPTIONS":
            return await call_next(request)

        platform_key = os.getenv("PLATFORM_API_KEY")
        if not platform_key:
            return await call_next(request)

        if request.headers.get("X-API-Key") != platform_key:
            return JSONResponse(
                status_code=401, content={"detail": "Invalid or missing API key"}
            )

        return await call_next(request)
