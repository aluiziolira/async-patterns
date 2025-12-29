"""Pytest configuration and fixtures for async-patterns tests."""

from __future__ import annotations

import pytest


@pytest.fixture()
def sample_urls() -> list[str]:
    """Provide a sample list of URLs for testing."""
    return [
        "https://httpbin.org/status/200",
        "https://httpbin.org/status/404",
        "https://httpbin.org/status/500",
    ]


@pytest.fixture()
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


@pytest.fixture()
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
