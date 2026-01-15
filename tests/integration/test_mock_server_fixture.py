"""Integration tests for the mock server pytest fixture.

These tests verify that the mock_server fixture works correctly
and provides reliable test infrastructure.
"""

from __future__ import annotations

import pytest

from benchmarks.mock_server import MockServerFixture


class TestMockServerFixtureIntegration:
    """Integration tests for mock_server fixture."""

    @pytest.mark.asyncio
    async def test_mock_server_fixture_starts(self, mock_server: MockServerFixture) -> None:
        """Mock server fixture should start server and provide base_url."""
        assert mock_server.base_url.startswith("http://127.0.0.1:")
        # Should be able to make a request
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{mock_server.base_url}/health") as response:
                assert response.status == 200
                data = await response.json()
                assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_mock_server_urls_helper(self, mock_server: MockServerFixture) -> None:
        """Mock server fixture should provide urls() helper."""
        urls = mock_server.urls(5, "/get")
        assert len(urls) == 5
        for url in urls:
            assert url.startswith(mock_server.base_url)
            assert url.endswith("/get")

    @pytest.mark.asyncio
    async def test_mock_server_url_helper(self, mock_server: MockServerFixture) -> None:
        """Mock server fixture should provide url() helper."""
        url = mock_server.url("/status/200")
        assert url == f"{mock_server.base_url}/status/200"

    @pytest.mark.asyncio
    async def test_mock_server_status_url_helper(self, mock_server: MockServerFixture) -> None:
        """Mock server fixture should provide status_url() helper."""
        url = mock_server.status_url(404)
        assert url == f"{mock_server.base_url}/status/404"

    @pytest.mark.asyncio
    async def test_mock_server_delay_url_helper(self, mock_server: MockServerFixture) -> None:
        """Mock server fixture should provide delay_url() helper."""
        url = mock_server.delay_url(0.5)
        assert url == f"{mock_server.base_url}/delay/0.5"

    @pytest.mark.asyncio
    async def test_mock_server_bytes_url_helper(self, mock_server: MockServerFixture) -> None:
        """Mock server fixture should provide bytes_url() helper."""
        url = mock_server.bytes_url(1024)
        assert url == f"{mock_server.base_url}/bytes/1024"

    @pytest.mark.asyncio
    async def test_mock_server_handles_concurrent_requests(
        self, mock_server: MockServerFixture
    ) -> None:
        """Mock server should handle concurrent requests."""
        import asyncio

        import aiohttp

        urls = [f"{mock_server.base_url}/get" for _ in range(10)]

        async with aiohttp.ClientSession() as session:
            tasks = [session.get(url) for url in urls]
            responses = await asyncio.gather(*tasks)

        for response in responses:
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_mock_server_config_is_passed(self, mock_server: MockServerFixture) -> None:
        """Mock server fixture should pass config correctly."""
        assert mock_server.config.port > 0
        assert mock_server.config.base_latency_ms == 1.0
        assert mock_server.config.jitter_seed == 42
