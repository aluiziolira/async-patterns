"""Pytest configuration and fixtures for async-patterns tests."""

from __future__ import annotations

import pytest
from aiohttp import ClientSession

from benchmarks.mock_server import MockServer, MockServerConfig, MockServerFixture


@pytest.fixture
async def sample_urls(mock_server: MockServerFixture) -> list[str]:
    """Provide a sample list of URLs for testing using mock server.

    Args:
        mock_server: The mock server fixture.

    Returns:
        List of mock server URLs with various status codes.
    """
    base = mock_server.base_url
    return [
        f"{base}/status/200",
        f"{base}/status/404",
        f"{base}/status/500",
    ]


@pytest.fixture
def mock_successful_response():
    """Mock a successful HTTP response."""

    class MockResponse:
        status_code = 200
        elapsed = 0.1
        url = "https://example.com"
        text = "OK"
        content = b"OK"

        def raise_for_status(self) -> None:
            pass

    return MockResponse()


@pytest.fixture
def mock_error_response():
    """Mock an error HTTP response."""

    class MockResponse:
        status_code = 404
        elapsed = 0.05
        url = "https://example.com/notfound"
        text = "Not Found"
        content = b"Not Found"

        def raise_for_status(self) -> None:
            raise Exception("404 Client Error")

    return MockResponse()


@pytest.fixture
async def mock_server_port() -> int:
    """Generate a unique port for each test to avoid conflicts."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture
async def mock_server(mock_server_port: int) -> MockServerFixture:
    """Start mock server for deterministic testing.

    This fixture starts a mock HTTP server on a unique port for each test,
    ensuring tests don't interfere with each other. The server is automatically
    stopped when the test completes.

    Yields:
        MockServerFixture with base_url property for constructing test URLs.
    """
    import socket

    # Find an available port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    config = MockServerConfig(
        port=port,
        base_latency_ms=1.0,  # Very low latency for fast tests
        jitter_seed=42,  # Reproducible timing
    )
    server = MockServer(config)
    await server.start()

    yield MockServerFixture(
        base_url=f"http://127.0.0.1:{port}",
        config=config,
    )

    await server.stop()


@pytest.fixture
async def mock_server_urls(mock_server: MockServerFixture) -> list[str]:
    """Generate mock server URLs for testing.

    Args:
        mock_server: The mock server fixture.

    Returns:
        List of URLs pointing to the mock server.
    """
    return mock_server.urls(10, "/get")


@pytest.fixture
async def mock_client() -> ClientSession:
    """Provide an aiohttp ClientSession for making HTTP requests.

    The session is closed automatically after the test.

    Yields:
        aiohttp ClientSession instance.
    """
    async with ClientSession() as session:
        yield session
