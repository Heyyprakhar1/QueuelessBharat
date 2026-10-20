"""In-memory sliding window rate limiter for abuse control and DoS deterrence.

Architecture & Instance Scope Note:
-----------------------------------
1. Local in-memory scope:
   This SlidingWindowRateLimiter tracks sliding windows per Python application process.
   It provides deterrence against brute-force abuse on single instances. In an autoscaled
   multi-instance deployment (e.g. multiple AWS Elastic Beanstalk instances behind an ALB),
   rate quotas are enforced independently per container rather than globally. Shared global
   enforcement requires an external distributed cache (such as Amazon ElastiCache Redis).

2. Trusted Proxy & Header Sanitization:
   In direct development or container execution without a trusted reverse proxy,
   TRUST_PROXY_HEADERS is False by default. The limiter identifies callers strictly by
   direct socket IP (request.client.host), discarding arbitrary client-supplied headers.
   When TRUST_PROXY_HEADERS is True and the direct connecting socket is within TRUSTED_PROXIES,
   the candidate IP is extracted from X-Forwarded-For. To prevent client IP spoofing, the edge
   load balancer or reverse proxy must sanitize or strip untrusted client-supplied X-Forwarded-For
   headers before appending the verified downstream remote IP.
"""

import asyncio
import ipaddress
import time
from collections import defaultdict
from collections.abc import Sequence

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


def is_trusted_proxy(ip_str: str, trusted_proxies: Sequence[str]) -> bool:
    """Checks if an IP address belongs to the configured list of trusted proxies or CIDR blocks."""
    if not ip_str or ip_str == "unknown_client":
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    for proxy_pattern in trusted_proxies:
        proxy_pattern = proxy_pattern.strip()
        if not proxy_pattern:
            continue
        try:
            if "/" in proxy_pattern:
                if ip in ipaddress.ip_network(proxy_pattern, strict=False):
                    return True
            else:
                if ip == ipaddress.ip_address(proxy_pattern):
                    return True
        except ValueError:
            if ip_str == proxy_pattern:
                return True
    return False


class SlidingWindowRateLimiter:
    """Thread-safe and coroutine-safe in-memory rate limiter using sliding timestamp window."""

    def __init__(self, max_requests: int, window_seconds: int, scope: str = "default") -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.scope = scope
        self.records: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last_cleanup: float = 0.0

    def cleanup_expired_keys(self, now: float | None = None) -> int:
        """Removes client keys whose timestamps are all outside the sliding window.

        Args:
            now: Monotonic timestamp reference (defaults to time.monotonic()).

        Returns:
            int: Count of evicted expired client keys.
        """
        curr_time = now if now is not None else time.monotonic()
        cutoff = curr_time - self.window_seconds
        evicted = 0
        for key in list(self.records.keys()):
            active = [t for t in self.records[key] if t > cutoff]
            if active:
                self.records[key] = active
            else:
                del self.records[key]
                evicted += 1
        return evicted

    def _get_client_key(self, request: Request) -> str:
        settings = get_settings()
        direct_ip = (
            request.client.host if request.client and request.client.host else "unknown_client"
        )

        # Explicit Trusted Proxy Enforcement:
        # Only parse X-Forwarded-For if TRUST_PROXY_HEADERS is explicitly enabled
        # AND the direct connecting socket IP is within the configured TRUSTED_PROXIES list.
        if settings.TRUST_PROXY_HEADERS and is_trusted_proxy(direct_ip, settings.TRUSTED_PROXIES):
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                candidate_ip = forwarded.split(",")[0].strip()
                try:
                    # Validate candidate is a syntactically valid IP address
                    ipaddress.ip_address(candidate_ip)
                    return f"{self.scope}:{candidate_ip}"
                except ValueError:
                    # Ignore malformed or spoofed forwarded IP and fallback to direct socket
                    pass

        return f"{self.scope}:{direct_ip}"

    async def __call__(self, request: Request) -> None:
        import os

        settings = get_settings()
        if (
            not settings.RATE_LIMIT_ENABLED
            or settings.ENVIRONMENT == "test"
            or "PYTEST_CURRENT_TEST" in os.environ
        ):
            return

        client_key = self._get_client_key(request)
        max_requests = self.max_requests
        window_seconds = self.window_seconds

        if self.scope == "auth_login":
            max_requests = settings.RATE_LIMIT_LOGIN_MAX
            window_seconds = settings.RATE_LIMIT_LOGIN_WINDOW
        elif self.scope == "auth_register":
            max_requests = settings.RATE_LIMIT_REGISTER_MAX
            window_seconds = settings.RATE_LIMIT_REGISTER_WINDOW
        elif self.scope == "queue_checkin":
            max_requests = settings.RATE_LIMIT_CHECKIN_MAX
            window_seconds = settings.RATE_LIMIT_CHECKIN_WINDOW
        elif self.scope == "poll_service":
            max_requests = settings.RATE_LIMIT_POLL_SERVICE_MAX
            window_seconds = settings.RATE_LIMIT_POLL_SERVICE_WINDOW
        elif self.scope == "poll_session":
            max_requests = settings.RATE_LIMIT_POLL_SESSION_MAX
            window_seconds = settings.RATE_LIMIT_POLL_SESSION_WINDOW
        elif self.scope == "visit_plan":
            max_requests = settings.RATE_LIMIT_VISIT_PLAN_MAX
            window_seconds = settings.RATE_LIMIT_VISIT_PLAN_WINDOW
        elif self.scope == "observation":
            max_requests = settings.RATE_LIMIT_OBSERVATION_MAX
            window_seconds = settings.RATE_LIMIT_OBSERVATION_WINDOW

        now = time.monotonic()
        cutoff = now - window_seconds

        async with self._lock:
            # Periodic opportunistic cleanup: prune all expired keys (at most once every 60s)
            if now - self._last_cleanup >= 60.0:
                self.cleanup_expired_keys(now)
                self._last_cleanup = now

            # Purge timestamps older than the sliding window
            timestamps = [t for t in self.records[client_key] if t > cutoff]
            if len(timestamps) >= max_requests:
                earliest = timestamps[0]
                retry_after = max(1, int(window_seconds - (now - earliest)))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Rate limit exceeded for {self.scope}. "
                        f"Please retry in {retry_after} seconds."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )
            timestamps.append(now)
            self.records[client_key] = timestamps


# Preconfigured rate limiter dependencies for sensitive routes
rate_limit_auth_login = SlidingWindowRateLimiter(
    max_requests=100, window_seconds=60, scope="auth_login"
)
rate_limit_auth_register = SlidingWindowRateLimiter(
    max_requests=50, window_seconds=60, scope="auth_register"
)
rate_limit_queue_checkin = SlidingWindowRateLimiter(
    max_requests=200, window_seconds=60, scope="queue_checkin"
)
rate_limit_poll_service = SlidingWindowRateLimiter(
    max_requests=120, window_seconds=60, scope="poll_service"
)
rate_limit_poll_session = SlidingWindowRateLimiter(
    max_requests=60, window_seconds=60, scope="poll_session"
)
rate_limit_visit_plan = SlidingWindowRateLimiter(
    max_requests=120, window_seconds=60, scope="visit_plan"
)
rate_limit_observation = SlidingWindowRateLimiter(
    max_requests=30, window_seconds=60, scope="observation"
)
