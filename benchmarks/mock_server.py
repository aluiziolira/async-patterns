#!/usr/bin/env python3
"""Mock HTTP Server for deterministic benchmarking.

This module provides a configurable mock HTTP server for testing
async patterns without external dependencies. It supports:
- Configurable fixed latency with optional seeded jitter
- Health and metadata endpoints
- Error injection (status codes, delays, 429s, 500s, byte payloads)
- Deterministic behavior for reproducible benchmarks

Usage:
    # Basic usage with default settings
    server = MockServer()
    await server.start()

    # Custom configuration
    config = MockServerConfig(
        host="127.0.0.1",
        port=8765,
        base_latency_ms=10.0,
        jitter_seed=42,
    )
    server = MockServer(config)
    await server.start()
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

# Use aiohttp for async HTTP server (already a dependency via httpx)
from aiohttp import web


@dataclass(frozen=True, slots=True)
class MockServerConfig:
    """Configuration for the mock HTTP server.

    Attributes:
        host: Host address to bind to (default: 127.0.0.1)
        port: Port number to listen on (default: 8765)
        base_latency_ms: Fixed latency in milliseconds to add to responses
        jitter_seed: Optional seed for reproducible random jitter
        error_rate: Probability of injecting a 500 error (0-1)
        rate_limit_after: Number of requests before returning 429 (None = disabled)
        rate_limit_retry_after_min: Minimum Retry-After seconds for 429
        rate_limit_retry_after_max: Maximum Retry-After seconds for 429
    """

    host: str = "127.0.0.1"
    port: int = 8765
    base_latency_ms: float = 10.0
    jitter_seed: int | None = None
    error_rate: float = 0.0
    rate_limit_after: int | None = None
    rate_limit_retry_after_min: int = 1
    rate_limit_retry_after_max: int = 5


@dataclass
class MockServer:
    """Async HTTP mock server for deterministic benchmarking.

    This server provides configurable latency and error injection for
    testing async patterns without external dependencies.

    Example:
        ```python
        import asyncio
        from benchmarks.mock_server import MockServer, MockServerConfig

        async def main():
            config = MockServerConfig(base_latency_ms=5.0)
            server = MockServer(config)
            await server.start()
            print(f"Server running at http://{server.config.host}:{server.config.port}")
            # ... run benchmarks ...
            await server.stop()

        asyncio.run(main())
        ```
    """

    config: MockServerConfig
    _request_count: int = field(default=0, init=False)
    _random: random.Random = field(default_factory=random.Random, init=False)
    _runner: web.AppRunner | None = field(default=None, init=False)
    _site: web.TCPSite | None = field(default=None, init=False)
    _app: web.Application | None = field(default=None, init=False)
    _bound_port: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Initialize random number generator for jitter."""
        if self.config.jitter_seed is not None:
            self._random = random.Random(self.config.jitter_seed)
        else:
            self._random = random.Random()

    @property
    def base_url(self) -> str:
        """Get the base URL of the server."""
        port = self._bound_port or self.config.port
        return f"http://{self.config.host}:{port}"

    @property
    def request_count(self) -> int:
        """Get the number of requests handled so far."""
        return self._request_count

    def _calculate_delay(self) -> float:
        """Calculate response delay with optional jitter.

        Returns:
            Delay in seconds to wait before responding.
        """
        base_delay = self.config.base_latency_ms / 1000.0
        if self.config.jitter_seed is not None:
            # Jitter range: 0% to 20% of base latency
            jitter = self._random.uniform(0, 0.2) * base_delay
            return base_delay + jitter
        return base_delay

    def _get_request_metadata(self, request: web.Request) -> dict[str, Any]:
        """Extract metadata from the request.

        Args:
            request: The aiohttp request object.

        Returns:
            Dictionary with request metadata.
        """
        headers = dict(request.headers)
        parsed_url = urlparse(str(request.url))

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "method": request.method,
            "path": request.path,
            "query_string": request.query_string if request.query_string else "",
            "headers": headers,
            "url": str(request.url),
            "host": parsed_url.netloc,
            "path_only": parsed_url.path,
        }

    async def _apply_delay(self) -> None:
        """Apply the configured delay before responding."""
        delay = self._calculate_delay()
        await asyncio.sleep(delay)

    async def _check_rate_limit(self) -> tuple[bool, int]:
        """Check if request should be rate limited.

        Returns:
            Tuple of (should_rate_limit, retry_after_seconds).
        """
        if self.config.rate_limit_after is None:
            return False, 0

        self._request_count += 1

        if self._request_count > self.config.rate_limit_after:
            retry_after = self._random.randint(
                self.config.rate_limit_retry_after_min,
                self.config.rate_limit_retry_after_max,
            )
            return True, retry_after

        return False, 0

    async def _check_error_injection(self) -> bool:
        """Check if an error should be injected.

        Returns:
            True if a 500 error should be returned.
        """
        return self._random.random() < self.config.error_rate

    async def handle_health(self, request: web.Request) -> web.Response:
        """Handle health check requests.

        Returns:
            200 OK response immediately (no delay).
        """
        return web.json_response({"status": "healthy", "server": "mock"})

    async def handle_get(self, request: web.Request) -> web.Response:
        """Handle GET requests with request metadata.

        Returns:
            JSON response with request metadata after configured delay.
        """
        await self._apply_delay()

        # Check for error injection
        if await self._check_error_injection():
            return web.json_response(
                {"error": "simulated error"},
                status=500,
            )

        metadata = self._get_request_metadata(request)
        return web.json_response(
            {
                "args": dict(request.query),
                "headers": metadata["headers"],
                "origin": "127.0.0.1",
                "url": metadata["url"],
                "json": metadata,
            }
        )

    async def handle_status(self, request: web.Request) -> web.Response:
        """Handle status code requests.

        Path: /status/{code}

        Returns:
            Response with specified status code.
        """
        await self._apply_delay()

        try:
            code = int(request.match_info.get("code", 200))
        except ValueError:
            return web.json_response({"error": "Invalid status code"}, status=400)

        # Check for error injection on 5xx
        if code >= 500 and await self._check_error_injection():
            code = 500

        return web.Response(status=code)

    async def handle_delay(self, request: web.Request) -> web.Request:
        """Handle delay requests.

        Path: /delay/{seconds}

        Returns:
            Response after specified delay.
        """
        try:
            seconds = float(request.match_info.get("seconds", 0))
        except ValueError:
            return web.Response(text="Invalid delay value", status=400)

        # Limit maximum delay to prevent abuse
        seconds = min(seconds, 60.0)

        await asyncio.sleep(seconds)

        return web.json_response(
            {
                "delay": seconds,
                "message": "Response delayed",
            }
        )

    async def handle_429(self, request: web.Request) -> web.Response:
        """Handle rate limit (429) requests.

        Returns:
            429 Too Many Requests with Retry-After header.
        """
        await self._apply_delay()

        # Check for rate limiting
        should_rate_limit, retry_after = await self._check_rate_limit()

        if should_rate_limit:
            return web.json_response(
                {"error": "rate limited", "retry_after": retry_after},
                status=429,
                headers={"Retry-After": str(retry_after)},
            )

        return web.json_response(
            {
                "message": "Request processed successfully",
                "requests_remaining": "unlimited",
            }
        )

    async def handle_500(self, request: web.Request) -> web.Response:
        """Handle 500 error injection requests.

        Returns:
            500 Internal Server Error based on error rate.
        """
        await self._apply_delay()

        if await self._check_error_injection():
            return web.json_response(
                {"error": "internal server error", "type": "simulated"},
                status=500,
            )

        return web.json_response({"message": "Request processed successfully"})

    async def handle_bytes(self, request: web.Request) -> web.Response:
        """Handle byte payload requests.

        Path: /bytes/{n}

        Returns:
            Response with n random bytes.
        """
        await self._apply_delay()

        try:
            n = int(request.match_info.get("n", 1024))
        except ValueError:
            return web.json_response({"error": "Invalid byte count"}, status=400)

        # Limit maximum bytes to prevent abuse
        n = min(max(1, n), 1048576)  # 1 byte to 1 MB

        # Generate random bytes
        data = self._random.randbytes(n)

        return web.Response(
            body=data,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(n),
            },
        )

    def _create_app(self) -> web.Application:
        """Create the aiohttp application with routes.

        Returns:
            Configured aiohttp web application.
        """
        app = web.Application()

        # Core endpoints
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/get", self.handle_get)

        # Error injection endpoints
        app.router.add_get("/status/{code}", self.handle_status)
        app.router.add_get("/delay/{seconds}", self.handle_delay)
        app.router.add_get("/429", self.handle_429)
        app.router.add_get("/500", self.handle_500)
        app.router.add_get("/bytes/{n}", self.handle_bytes)

        return app

    async def start(self) -> None:
        """Start the mock HTTP server.

        Raises:
            RuntimeError: If the server is already running.
        """
        if self._runner is not None:
            raise RuntimeError("Server is already running")

        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(
            self._runner,
            self.config.host,
            self.config.port,
        )
        await self._site.start()
        self._bound_port = self.config.port
        bound_port = None
        if self._site._server and self._site._server.sockets:
            sockname = self._site._server.sockets[0].getsockname()
            if isinstance(sockname, tuple) and len(sockname) >= 2:
                bound_port = sockname[1]
        if bound_port is None and self._runner and self._runner.addresses:
            address = self._runner.addresses[0]
            if isinstance(address, tuple) and len(address) >= 2:
                bound_port = address[1]
        if bound_port is not None:
            self._bound_port = bound_port

        # Reset request count on start
        self._request_count = 0

    async def stop(self) -> None:
        """Stop the mock HTTP server.

        Raises:
            RuntimeError: If the server is not running.
        """
        if self._runner is None:
            raise RuntimeError("Server is not running")

        await self._runner.cleanup()
        self._runner = None
        self._site = None
        self._app = None
        self._bound_port = None

    async def __aenter__(self) -> MockServer:
        """Async context manager entry.

        Returns:
            Self for use in async context manager.
        """
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit.

        Ensures the server is stopped.
        """
        await self.stop()


@dataclass
class MockServerFixture:
    """Fixture for pytest integration providing mock server access.

    Attributes:
        base_url: Base URL of the mock server.
        config: Configuration used to create the server.
    """

    base_url: str
    config: MockServerConfig

    def url(self, path: str) -> str:
        """Generate a full URL for a given path.

        Args:
            path: The path component (should start with /).

        Returns:
            Full URL to the mock server.
        """
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def urls(self, count: int, path: str = "/get") -> list[str]:
        """Generate multiple URLs for the same endpoint.

        Args:
            count: Number of URLs to generate.
            path: The path component for each URL.

        Returns:
            List of full URLs.
        """
        return [self.url(path) for _ in range(count)]

    def status_url(self, code: int) -> str:
        """Generate a URL that returns a specific status code.

        Args:
            code: HTTP status code to return.

        Returns:
            Full URL for the status endpoint.
        """
        return self.url(f"/status/{code}")

    def delay_url(self, seconds: float) -> str:
        """Generate a URL with a specific delay.

        Args:
            seconds: Delay in seconds.

        Returns:
            Full URL for the delay endpoint.
        """
        return self.url(f"/delay/{seconds}")

    def bytes_url(self, n: int) -> str:
        """Generate a URL that returns n random bytes.

        Args:
            n: Number of bytes to return.

        Returns:
            Full URL for the bytes endpoint.
        """
        return self.url(f"/bytes/{n}")
