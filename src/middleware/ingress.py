import asyncio
import time
from collections import defaultdict, deque
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

class RateLimitingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to apply rate limiting before expensive body parsing.

    This middleware limits the number of requests per client IP within a
    specified time window. Requests exceeding the limit are rejected with
    a 429 Too Many Requests status.

    The rate limit check occurs at the very beginning of the request
    processing pipeline to prevent expensive operations like body parsing
    for rate-limited requests, thus mitigating resource exhaustion.

    This in-memory implementation uses an asyncio.Lock to ensure thread-safety
    for its internal state under concurrent access. For production deployments
    requiring higher throughput, resilience, or scaling across multiple instances,
    this should be replaced with a distributed and persistent store like Redis.
    """
    def __init__(self, app: ASGIApp, limit: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.limit = limit
        self.window_seconds = window_seconds
        # In-memory store for demonstration purposes.
        # Stores client IP -> deque of request timestamps.
        self.client_requests: defaultdict[str, deque[float]] = defaultdict(deque)
        # Protects access to the shared self.client_requests dictionary.
        self.lock = asyncio.Lock()

    def _get_client_ip(self, request: Request) -> str:
        """
        Extracts the client IP address from the request.
        For production environments behind a proxy, consider checking
        'X-Forwarded-For' or similar headers first, with careful validation
        to prevent IP spoofing.
        """
        # Example for X-Forwarded-For (uncomment and adapt for production):
        # if "x-forwarded-for" in request.headers:
        #     # Assumes a single proxy or takes the first IP in the list
        #     return request.headers["x-forwarded-for"].split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown" # Fallback for requests without client information

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        # Only apply rate limiting to HTTP requests
        if request.scope["type"] != "http":
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        current_time = time.time()

        # Acquire a lock to protect the shared in-memory rate limiting state.
        # This serializes critical sections where `self.client_requests` is modified or read.
        # This global lock can become a bottleneck under extreme load; consider
        # a distributed rate limiter for high-performance production systems.
        async with self.lock:
            # Clean up old request timestamps that fall outside the current sliding window.
            # This ensures that only requests within the 'window_seconds' period are counted.
            while self.client_requests[client_ip] and \
                  self.client_requests[client_ip][0] < current_time - self.window_seconds:
                self.client_requests[client_ip].popleft()

            # Apply the rate limit check *before* allowing the request to proceed
            # to any downstream middleware or endpoint handler that might perform
            # expensive operations like reading the request body.
            if len(self.client_requests[client_ip]) >= self.limit:
                # If the rate limit is exceeded, reject the request immediately.
                # A 'Retry-After' header informs the client when they can retry.
                retry_after_seconds = self.window_seconds # Default if queue is empty (shouldn't happen here)

                if self.client_requests[client_ip]:
                    # Calculate the precise time until the oldest request expires
                    # and a new request would be allowed.
                    oldest_request_timestamp = self.client_requests[client_ip][0]
                    time_until_oldest_expires = (oldest_request_timestamp + self.window_seconds) - current_time
                    # Ensure minimum of 0 and add 1 second for a robust retry instruction.
                    retry_after_seconds = max(0, int(time_until_oldest_expires) + 1)

                headers = {"Retry-After": str(retry_after_seconds)}
                return PlainTextResponse("Too Many Requests", status_code=429, headers=headers)

            # If the request is within the allowed limits, record its timestamp.
            # This action is also protected by the lock.
            self.client_requests[client_ip].append(current_time)

        # Proceed to the next middleware in the stack or the final route handler.
        # Any expensive operations like body parsing will only occur *after* this
        # rate limit check has successfully passed.
        response = None
        try:
            response = await call_next(request)
        finally:
            # As per the bug report, this block is intended for clearing request-local state.
            # This specific middleware's state (self.client_requests) is global and
            # designed to persist across requests (until entries expire from the window),
            # so no specific cleanup for its own state is required here.
            pass
        return response