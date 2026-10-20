"""Unit tests for in-memory rate limiting and sliding window abuse protection."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.config import get_settings
from app.core.rate_limit import SlidingWindowRateLimiter


def make_mock_request(
    client_ip: str = "192.168.1.100", headers: list[tuple[bytes, bytes]] | None = None
) -> Request:
    """Creates a mock Starlette request object."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": headers or [],
        "client": (client_ip, 12345),
    }
    return Request(scope)


class TestRateLimiter:
    """Verifies sliding window rate limiting semantics and client identity hardening."""

    async def test_requests_within_limit_pass(self, monkeypatch):
        """Requests under max_requests execute without raising 429."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=10, scope="test_pass")
        req = make_mock_request()

        # 3 requests should all pass
        await limiter(req)
        await limiter(req)
        await limiter(req)
        assert len(limiter.records[limiter._get_client_key(req)]) == 3

    async def test_requests_exceeding_limit_raise_429(self, monkeypatch):
        """Requests exceeding max_requests within window raise HTTP 429."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60, scope="test_block")
        req = make_mock_request()

        await limiter(req)
        await limiter(req)

        with pytest.raises(HTTPException) as exc_info:
            await limiter(req)

        assert exc_info.value.status_code == 429
        assert "Rate limit exceeded" in exc_info.value.detail
        assert "Retry-After" in exc_info.value.headers

    async def test_different_clients_tracked_independently(self, monkeypatch):
        """Client A reaching limit does not block Client B."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60, scope="test_isolated")
        req_a = make_mock_request("10.0.0.1")
        req_b = make_mock_request("10.0.0.2")

        await limiter(req_a)
        with pytest.raises(HTTPException):
            await limiter(req_a)

        # Client B is not blocked
        await limiter(req_b)

    async def test_forwarded_for_ignored_when_proxy_headers_untrusted(self, monkeypatch):
        """When TRUST_PROXY_HEADERS is False, spoofed X-Forwarded-For is ignored."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)

        limiter = SlidingWindowRateLimiter(
            max_requests=2, window_seconds=60, scope="test_spoof_off"
        )
        req1 = make_mock_request("192.168.1.50", headers=[(b"x-forwarded-for", b"203.0.113.1")])
        req2 = make_mock_request("192.168.1.50", headers=[(b"x-forwarded-for", b"203.0.113.2")])

        # Both requests come from socket 192.168.1.50 with rotating spoofed X-Forwarded-For
        await limiter(req1)
        await limiter(req2)

        # 3rd request from same socket with another spoofed header must be blocked
        req3 = make_mock_request("192.168.1.50", headers=[(b"x-forwarded-for", b"203.0.113.3")])
        with pytest.raises(HTTPException) as exc_info:
            await limiter(req3)
        assert exc_info.value.status_code == 429

    async def test_forwarded_for_ignored_from_untrusted_proxy_socket(self, monkeypatch):
        """When TRUST_PROXY_HEADERS is True, but connecting socket is NOT trusted,
        X-Forwarded-For is ignored.
        """
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "10.0.0.0/8"])

        limiter = SlidingWindowRateLimiter(
            max_requests=1, window_seconds=60, scope="test_untrusted_socket"
        )
        # Direct socket 198.51.100.99 is NOT in TRUSTED_PROXIES
        req_untrusted = make_mock_request(
            "198.51.100.99", headers=[(b"x-forwarded-for", b"1.1.1.1")]
        )
        assert limiter._get_client_key(req_untrusted) == "test_untrusted_socket:198.51.100.99"

    async def test_forwarded_for_honored_from_trusted_proxy_socket(self, monkeypatch):
        """When TRUST_PROXY_HEADERS is True and socket is trusted proxy, client IP is
        extracted from X-Forwarded-For.
        """
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "10.0.0.0/8"])

        limiter = SlidingWindowRateLimiter(
            max_requests=1, window_seconds=60, scope="test_trusted_socket"
        )
        # Direct socket 127.0.0.1 is a trusted proxy
        req_trusted = make_mock_request(
            "127.0.0.1", headers=[(b"x-forwarded-for", b"203.0.113.195, 10.0.0.1")]
        )
        assert limiter._get_client_key(req_trusted) == "test_trusted_socket:203.0.113.195"

    async def test_malformed_forwarded_for_falls_back_to_socket_ip(self, monkeypatch):
        """Malformed or malicious X-Forwarded-For strings fall back to direct socket IP."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["127.0.0.1"])

        limiter = SlidingWindowRateLimiter(
            max_requests=1, window_seconds=60, scope="test_malformed"
        )
        req_bad = make_mock_request(
            "127.0.0.1", headers=[(b"x-forwarded-for", b"not-a-valid-ip-address; DROP TABLE;")]
        )
        assert limiter._get_client_key(req_bad) == "test_malformed:127.0.0.1"

    async def test_polling_rate_limiters_block_excessive_polls(self, monkeypatch):
        """Verifies poll_service and poll_session scopes enforce configured limits."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "RATE_LIMIT_POLL_SERVICE_MAX", 2)
        monkeypatch.setattr(settings, "RATE_LIMIT_POLL_SERVICE_WINDOW", 60)

        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60, scope="poll_service")
        req = make_mock_request()

        await limiter(req)
        await limiter(req)

        with pytest.raises(HTTPException) as exc_info:
            await limiter(req)

        assert exc_info.value.status_code == 429
        assert "poll_service" in exc_info.value.detail

    async def test_visit_plan_rate_limiter_blocks_excessive_requests(self, monkeypatch):
        """Verifies visit_plan scope enforces configured rate limits."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "RATE_LIMIT_VISIT_PLAN_MAX", 2)
        monkeypatch.setattr(settings, "RATE_LIMIT_VISIT_PLAN_WINDOW", 60)

        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60, scope="visit_plan")
        req = make_mock_request()

        await limiter(req)
        await limiter(req)

        with pytest.raises(HTTPException) as exc_info:
            await limiter(req)

        assert exc_info.value.status_code == 429
        assert "visit_plan" in exc_info.value.detail

    async def test_observation_rate_limiter_blocks_excessive_submissions(self, monkeypatch):
        """Verifies observation scope enforces configured rate limits."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        settings = get_settings()
        monkeypatch.setattr(settings, "RATE_LIMIT_OBSERVATION_MAX", 2)
        monkeypatch.setattr(settings, "RATE_LIMIT_OBSERVATION_WINDOW", 60)

        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60, scope="observation")
        req = make_mock_request()

        await limiter(req)
        await limiter(req)

        with pytest.raises(HTTPException) as exc_info:
            await limiter(req)

        assert exc_info.value.status_code == 429
        assert "observation" in exc_info.value.detail

    async def test_expired_keys_are_evicted_from_records(self, monkeypatch):
        """Verifies cleanup_expired_keys removes stale client entries from records dict."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=10, scope="test_evict")

        # Manually populate records with old and fresh timestamps
        limiter.records["test_evict:old_client_1"] = [100.0, 102.0]
        limiter.records["test_evict:old_client_2"] = [104.0]
        limiter.records["test_evict:active_client"] = [200.0]

        # Monotonic time reference: 205.0 (cutoff = 205.0 - 10 = 195.0)
        # old_client_1 and old_client_2 have timestamps < 195.0; active_client has 200.0 > 195.0
        evicted = limiter.cleanup_expired_keys(now=205.0)

        assert evicted == 2
        assert "test_evict:old_client_1" not in limiter.records
        assert "test_evict:old_client_2" not in limiter.records
        assert "test_evict:active_client" in limiter.records
        assert len(limiter.records) == 1
