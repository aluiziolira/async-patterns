"""Unit tests for the mock HTTP server.

These tests verify the mock server's core functionality including:
- Server startup and shutdown
- Health endpoint
- Latency configuration
- Error injection endpoints
- Request metadata handling
"""

from __future__ import annotations

import time
from unittest.mock import patch

import aiohttp
import pytest

from benchmarks.mock_server import MockServer, MockServerConfig


class TestMockServerConfig:
    """Test cases for MockServerConfig."""

    def test_default_config(self) -> None:
        """Default configuration should have expected values."""
        config = MockServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8765
        assert config.base_latency_ms == 10.0
        assert config.jitter_seed is None
        assert config.error_rate == 0.0
        assert config.rate_limit_after is None

    def test_custom_config(self) -> None:
        """Custom configuration should accept all parameters."""
        config = MockServerConfig(
            host="0.0.0.0",
            port=8080,
            base_latency_ms=50.0,
            jitter_seed=42,
            error_rate=0.1,
            rate_limit_after=100,
        )
        assert config.host == "0.0.0.0"
        assert config.port == 8080
        assert config.base_latency_ms == 50.0
        assert config.jitter_seed == 42
        assert config.error_rate == 0.1
        assert config.rate_limit_after == 100

    def test_config_immutable(self) -> None:
        """Config should be immutable (frozen dataclass)."""
        config = MockServerConfig()
        with pytest.raises(  # type: ignore[call-overload]
            (TypeError, AttributeError), match=".*"
        ):
            config.port = 9999  # type: ignore[misc]


class TestMockServerCore:
    """Test cases for MockServer core functionality."""

    @pytest.mark.asyncio
    async def test_server_start_and_stop(self) -> None:
        """Server should start and stop successfully."""
        server = MockServer(MockServerConfig(port=0))
        await server.start()
        assert server._runner is not None
        assert server._site is not None
        await server.stop()
        assert server._runner is None

    @pytest.mark.asyncio
    async def test_server_context_manager(self) -> None:
        """Server should work as async context manager."""
        async with MockServer(MockServerConfig(port=0)) as server:
            assert server._runner is not None
        assert server._runner is None

    @pytest.mark.asyncio
    async def test_base_url_property(self) -> None:
        """Base URL should be correctly formatted."""
        server = MockServer(MockServerConfig(host="127.0.0.1", port=8767))
        assert server.base_url == "http://127.0.0.1:8767"

    @pytest.mark.asyncio
    async def test_health_endpoint(self) -> None:
        """Health endpoint should return healthy status."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.base_url}/health") as response:
                    assert response.status == 200
                    data = await response.json()
                    assert data["status"] == "healthy"
                    assert data["server"] == "mock"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_get_endpoint(self) -> None:
        """GET endpoint should return request metadata."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"{server.base_url}/get",
                    headers={"X-Custom-Header": "test-value"},
                ) as response,
            ):
                assert response.status == 200
                data = await response.json()
                assert "json" in data
                assert data["json"]["method"] == "GET"
                assert data["json"]["path"] == "/get"
                assert data["headers"]["X-Custom-Header"] == "test-value"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_get_with_query_params(self) -> None:
        """GET endpoint should return query parameters."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(f"{server.base_url}/get?key1=value1&key2=value2") as response,
            ):
                assert response.status == 200
                data = await response.json()
                assert data["args"]["key1"] == "value1"
                assert data["args"]["key2"] == "value2"
        finally:
            await server.stop()


class TestMockServerLatency:
    """Test cases for mock server latency configuration."""

    @pytest.mark.asyncio
    async def test_zero_latency(self) -> None:
        """Server with zero latency should respond quickly."""

        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                start = time.perf_counter()
                async with session.get(f"{server.base_url}/health") as _:
                    pass
                elapsed = time.perf_counter() - start
                # Should respond in less than 100ms with no latency
                assert elapsed < 0.1
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_configured_latency(self) -> None:
        """Server should respect configured latency."""

        server = MockServer(MockServerConfig(port=0, base_latency_ms=50))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                start = time.perf_counter()
                async with session.get(f"{server.base_url}/get") as _:
                    pass
                elapsed = time.perf_counter() - start
                # Should take at least 40ms (with some tolerance)
                assert elapsed >= 0.04
                # Should not take more than 100ms
                assert elapsed < 0.15
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_jitter_reproducibility(self) -> None:
        """Server with seeded jitter should produce reproducible delays."""

        # Two servers with same seed should behave similarly
        config1 = MockServerConfig(port=0, base_latency_ms=100, jitter_seed=42)
        config2 = MockServerConfig(port=0, base_latency_ms=100, jitter_seed=42)

        server1 = MockServer(config1)
        server2 = MockServer(config2)

        await server1.start()
        await server2.start()

        try:
            async with aiohttp.ClientSession() as session:
                # First request to each server
                start1 = time.perf_counter()
                async with session.get(f"{server1.base_url}/get") as _:
                    elapsed1 = time.perf_counter() - start1

                start2 = time.perf_counter()
                async with session.get(f"{server2.base_url}/get") as _:
                    elapsed2 = time.perf_counter() - start2

                # Both should have similar timing (same seed, same jitter)
                # Allow 10% tolerance for system variance
                assert abs(elapsed1 - elapsed2) < 0.02
        finally:
            await server1.stop()
            await server2.stop()


class TestMockServerErrorInjection:
    """Test cases for mock server error injection endpoints."""

    @pytest.mark.asyncio
    async def test_status_endpoint_200(self) -> None:
        """Status endpoint should return specified status code."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.base_url}/status/200") as response:
                    assert response.status == 200
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_status_endpoint_404(self) -> None:
        """Status endpoint should return 404."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.base_url}/status/404") as response:
                    assert response.status == 404
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_status_endpoint_500(self) -> None:
        """Status endpoint should return 500."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.base_url}/status/500") as response:
                    assert response.status == 500
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_delay_endpoint(self) -> None:
        """Delay endpoint should wait before responding."""

        server = MockServer(MockServerConfig(port=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                start = time.perf_counter()
                async with session.get(f"{server.base_url}/delay/0.2") as response:
                    elapsed = time.perf_counter() - start
                    assert response.status == 200
                    data = await response.json()
                    assert data["delay"] == 0.2
                    # Should take approximately 200ms
                    assert 0.15 < elapsed < 0.35
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_bytes_endpoint(self) -> None:
        """Bytes endpoint should return specified number of bytes."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.base_url}/bytes/1024") as response:
                    assert response.status == 200
                    data = await response.read()
                    assert len(data) == 1024
                    assert response.headers.get("Content-Length") == "1024"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_429_endpoint_without_rate_limit(self) -> None:
        """429 endpoint should return 200 when not rate limited."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.base_url}/429") as response:
                    # Should not be rate limited yet
                    assert response.status == 200
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_429_endpoint_with_rate_limit(self) -> None:
        """429 endpoint should return 429 after rate limit."""
        server = MockServer(
            MockServerConfig(
                port=0,
                base_latency_ms=0,
                rate_limit_after=2,
            )
        )
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                # First two requests should succeed
                for i in range(2):
                    async with session.get(f"{server.base_url}/429") as response:
                        assert response.status == 200

                # Third request should be rate limited
                async with session.get(f"{server.base_url}/429") as response:
                    assert response.status == 429
                    assert "Retry-After" in response.headers
                    retry_after = int(response.headers["Retry-After"])
                    assert 1 <= retry_after <= 5
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_500_endpoint_with_error_rate(self) -> None:
        """500 endpoint should return 500 based on error rate."""
        server = MockServer(
            MockServerConfig(
                port=0,
                base_latency_ms=0,
                error_rate=1.0,  # Always return 500
            )
        )
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.base_url}/500") as response:
                    assert response.status == 500
        finally:
            await server.stop()


class TestMockServerFixture:
    """Test cases for MockServerFixture utility methods."""

    def test_url_generation(self) -> None:
        """Fixture should generate correct URLs."""
        from benchmarks.mock_server import MockServerFixture

        fixture = MockServerFixture(
            base_url="http://127.0.0.1:8765",
            config=MockServerConfig(),
        )
        assert fixture.url("/get") == "http://127.0.0.1:8765/get"
        assert fixture.url("/health") == "http://127.0.0.1:8765/health"

    def test_urls_generation(self) -> None:
        """Fixture should generate multiple URLs."""
        from benchmarks.mock_server import MockServerFixture

        fixture = MockServerFixture(
            base_url="http://127.0.0.1:8765",
            config=MockServerConfig(),
        )
        urls = fixture.urls(3, "/get")
        assert len(urls) == 3
        assert all(u == "http://127.0.0.1:8765/get" for u in urls)

    def test_status_url(self) -> None:
        """Fixture should generate status URLs correctly."""
        from benchmarks.mock_server import MockServerFixture

        fixture = MockServerFixture(
            base_url="http://127.0.0.1:8765",
            config=MockServerConfig(),
        )
        assert fixture.status_url(200) == "http://127.0.0.1:8765/status/200"
        assert fixture.status_url(404) == "http://127.0.0.1:8765/status/404"

    def test_delay_url(self) -> None:
        """Fixture should generate delay URLs correctly."""
        from benchmarks.mock_server import MockServerFixture

        fixture = MockServerFixture(
            base_url="http://127.0.0.1:8765",
            config=MockServerConfig(),
        )
        assert fixture.delay_url(0.5) == "http://127.0.0.1:8765/delay/0.5"

    def test_bytes_url(self) -> None:
        """Fixture should generate bytes URLs correctly."""
        from benchmarks.mock_server import MockServerFixture

        fixture = MockServerFixture(
            base_url="http://127.0.0.1:8765",
            config=MockServerConfig(),
        )
        assert fixture.bytes_url(1024) == "http://127.0.0.1:8765/bytes/1024"


class TestMockServerEdgeCases:
    """Test cases for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_invalid_status_code(self) -> None:
        """Server should handle invalid status code gracefully."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{server.base_url}/status/invalid") as response:
                    assert response.status == 400
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_bytes_limit(self) -> None:
        """Server should limit bytes to prevent abuse."""
        server = MockServer(MockServerConfig(port=0, base_latency_ms=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                # Request more than 1MB (should be limited to 1MB)
                async with session.get(f"{server.base_url}/bytes/2000000") as response:
                    assert response.status == 200
                    data = await response.read()
                    # Should be limited to 1MB
                    assert len(data) == 1048576
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_delay_limit(self) -> None:
        """Server should limit delay to prevent abuse."""
        server = MockServer(MockServerConfig(port=0))
        await server.start()

        try:
            async with aiohttp.ClientSession() as session:
                # Mock asyncio.sleep to avoid 60s wait
                # Use a side_effect to track how long the delay was supposed to be
                import asyncio

                original_sleep = asyncio.sleep
                sleep_calls = []

                async def mock_sleep(duration, **kwargs):
                    """Track sleep calls without actually waiting."""
                    sleep_calls.append(duration)
                    # Don't actually sleep

                with patch.object(asyncio, "sleep", side_effect=mock_sleep):
                    async with session.get(f"{server.base_url}/delay/120") as response:
                        data = await response.json()
                        # Verify the delay was limited to 60s
                        assert data["delay"] == 60.0

                        # The server should have called sleep with exactly 60s (not 120s)
                        assert 60.0 in sleep_calls
                        assert 120.0 not in sleep_calls
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_double_start_raises(self) -> None:
        """Starting an already running server should raise."""
        server = MockServer(MockServerConfig(port=0))
        await server.start()

        try:
            with pytest.raises(RuntimeError, match="already running"):
                await server.start()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_raises(self) -> None:
        """Stopping a non-running server should raise."""
        server = MockServer(MockServerConfig(port=0))

        with pytest.raises(RuntimeError, match="not running"):
            await server.stop()
