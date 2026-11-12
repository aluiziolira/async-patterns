"""Unit tests for Retry-After parsing and delay selection."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import pytest

from async_patterns.engine.models import RetryConfig
from async_patterns.patterns.retry import RetryPolicy, parse_retry_after


class TestParseRetryAfter:
    """Tests for parse_retry_after helper."""

    def test_delta_seconds(self) -> None:
        assert parse_retry_after("12") == 12.0

    def test_http_date_future(self) -> None:
        now = 1_700_000_000.0
        retry_at = datetime.fromtimestamp(now, tz=UTC) + timedelta(seconds=45)
        assert parse_retry_after(format_datetime(retry_at), now=now) == pytest.approx(45.0)

    def test_http_date_past(self) -> None:
        now = 1_700_000_000.0
        retry_at = datetime.fromtimestamp(now, tz=UTC) - timedelta(seconds=30)
        assert parse_retry_after(format_datetime(retry_at), now=now) == 0.0

    def test_malformed_returns_none(self) -> None:
        assert parse_retry_after("not-a-valid-retry-after") is None
        assert parse_retry_after("-5") is None
        assert parse_retry_after("²") is None
        assert parse_retry_after("") is None
        assert parse_retry_after("  ") is None


class TestRetryAfterDelaySelection:
    """Tests that RetryPolicy honors Retry-After over backoff when larger."""

    @pytest.mark.asyncio
    async def test_uses_retry_after_seconds_when_larger_than_backoff(self) -> None:
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                base_delay=0.1,
                max_delay=60.0,
                jitter_factor=0.0,
                retry_after_max=60.0,
            )
        )
        call_count = 0
        sleeps: list[float] = []

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                request_info = MagicMock()
                request_info.real_url = "http://example.com"
                import aiohttp

                raise aiohttp.ClientResponseError(
                    request_info=request_info,
                    history=(),
                    status=429,
                    message="Rate limited",
                    headers={"Retry-After": "5"},
                )
            return "ok"

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            assert await policy.execute(flaky) == "ok"

        assert sleeps == [5.0]

    @pytest.mark.asyncio
    async def test_uses_http_date_retry_after(self) -> None:
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                base_delay=0.05,
                max_delay=60.0,
                jitter_factor=0.0,
                retry_after_max=60.0,
            )
        )
        call_count = 0
        sleeps: list[float] = []
        now = 1_700_000_000.0
        retry_at = datetime.fromtimestamp(now, tz=UTC) + timedelta(seconds=8)

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                request_info = MagicMock()
                request_info.real_url = "http://example.com"
                import aiohttp

                raise aiohttp.ClientResponseError(
                    request_info=request_info,
                    history=(),
                    status=429,
                    message="Rate limited",
                    headers={"Retry-After": format_datetime(retry_at)},
                )
            return "ok"

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with (
            patch("asyncio.sleep", side_effect=fake_sleep),
            patch("async_patterns.patterns.retry.time.time", return_value=now),
        ):
            assert await policy.execute(flaky) == "ok"

        assert sleeps == [pytest.approx(8.0)]

    @pytest.mark.asyncio
    async def test_past_http_date_falls_back_to_backoff(self) -> None:
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                base_delay=0.2,
                max_delay=60.0,
                jitter_factor=0.0,
                retry_after_max=60.0,
            )
        )
        call_count = 0
        sleeps: list[float] = []
        now = 1_700_000_000.0
        retry_at = datetime.fromtimestamp(now, tz=UTC) - timedelta(seconds=10)

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                request_info = MagicMock()
                request_info.real_url = "http://example.com"
                import aiohttp

                raise aiohttp.ClientResponseError(
                    request_info=request_info,
                    history=(),
                    status=429,
                    message="Rate limited",
                    headers={"Retry-After": format_datetime(retry_at)},
                )
            return "ok"

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with (
            patch("asyncio.sleep", side_effect=fake_sleep),
            patch("async_patterns.patterns.retry.time.time", return_value=now),
        ):
            assert await policy.execute(flaky) == "ok"

        # Past Retry-After → 0.0, so max(backoff=0.2, 0.0) = 0.2
        assert sleeps == [pytest.approx(0.2)]

    @pytest.mark.asyncio
    async def test_malformed_retry_after_uses_backoff(self) -> None:
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                base_delay=0.25,
                max_delay=60.0,
                jitter_factor=0.0,
                retry_after_max=60.0,
            )
        )
        call_count = 0
        sleeps: list[float] = []

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                request_info = MagicMock()
                request_info.real_url = "http://example.com"
                import aiohttp

                raise aiohttp.ClientResponseError(
                    request_info=request_info,
                    history=(),
                    status=429,
                    message="Rate limited",
                    headers={"Retry-After": "later-please"},
                )
            return "ok"

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            assert await policy.execute(flaky) == "ok"

        assert sleeps == [pytest.approx(0.25)]

    @pytest.mark.asyncio
    async def test_retry_after_capped_by_retry_after_max(self) -> None:
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                base_delay=0.1,
                max_delay=60.0,
                jitter_factor=0.0,
                retry_after_max=3.0,
            )
        )
        call_count = 0
        sleeps: list[float] = []

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                request_info = MagicMock()
                request_info.real_url = "http://example.com"
                import aiohttp

                raise aiohttp.ClientResponseError(
                    request_info=request_info,
                    history=(),
                    status=429,
                    message="Rate limited",
                    headers={"Retry-After": "30"},
                )
            return "ok"

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            assert await policy.execute(flaky) == "ok"

        assert sleeps == [3.0]

    @pytest.mark.asyncio
    async def test_backoff_wins_when_larger_than_retry_after(self) -> None:
        policy = RetryPolicy(
            config=RetryConfig(
                max_attempts=2,
                base_delay=2.0,
                max_delay=60.0,
                jitter_factor=0.0,
                retry_after_max=60.0,
            )
        )
        call_count = 0
        sleeps: list[float] = []

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                request_info = MagicMock()
                request_info.real_url = "http://example.com"
                import aiohttp

                raise aiohttp.ClientResponseError(
                    request_info=request_info,
                    history=(),
                    status=429,
                    message="Rate limited",
                    headers={"Retry-After": "1"},
                )
            return "ok"

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            assert await policy.execute(flaky) == "ok"

        assert sleeps == [pytest.approx(2.0)]
