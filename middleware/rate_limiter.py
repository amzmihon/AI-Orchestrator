"""
Rate Limiter — simple in-memory per-user rate limiting middleware.

Uses a sliding window counter to enforce requests-per-minute limits.
This protects the LLM backend from being overwhelmed by a single user.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-user sliding window rate limiter.

    Only applies to POST /api/chat requests (the expensive LLM endpoint).
    Uses the Authorization header to identify users. Falls back to IP
    if no auth header is present.
    """

    def __init__(self, app, max_requests: int | None = None, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests or settings.rate_limit_per_minute
        self.window_seconds = window_seconds
        # { user_key: [timestamp, timestamp, ...] }
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _get_user_key(self, request: Request) -> str:
        """Extract a unique key per user from the request."""
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            # Use first 32 chars of token as key (avoids storing full tokens)
            return f"token:{auth[7:39]}"
        # Fallback to client IP
        return f"ip:{request.client.host if request.client else 'unknown'}"

    def _cleanup(self, key: str, now: float) -> None:
        """Remove timestamps outside the sliding window."""
        cutoff = now - self.window_seconds
        self._requests[key] = [
            t for t in self._requests[key] if t > cutoff
        ]

    async def dispatch(self, request: Request, call_next):
        # Only rate-limit the chat endpoint
        if request.method == "POST" and request.url.path == "/api/chat":
            now = time.time()
            key = self._get_user_key(request)
            self._cleanup(key, now)

            if len(self._requests[key]) >= self.max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Rate limit exceeded. Maximum {self.max_requests} "
                        f"requests per minute. Please wait and try again."
                    ),
                )

            self._requests[key].append(now)

        return await call_next(request)
