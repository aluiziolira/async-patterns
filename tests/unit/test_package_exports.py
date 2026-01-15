"""Tests for package-level exports."""

from __future__ import annotations


class TestPackageExports:
    """Test cases for top-level package imports."""

    def test_import_async_engine_impl(self) -> None:
        """AsyncEngine should be importable from async_patterns."""
        from async_patterns import AsyncEngine

        assert AsyncEngine is not None

    def test_import_engine_protocol(self) -> None:
        """Engine protocol should be importable from async_patterns."""
        from async_patterns import Engine

        assert Engine is not None

    def test_import_sync_engine_protocol(self) -> None:
        """SyncEngineProtocol should be importable from async_patterns."""
        from async_patterns import SyncEngineProtocol

        assert SyncEngineProtocol is not None

    def test_import_async_engine_protocol(self) -> None:
        """AsyncEngineProtocol should be importable from async_patterns."""
        from async_patterns import AsyncEngineProtocol

        assert AsyncEngineProtocol is not None

    def test_import_engine_result(self) -> None:
        """EngineResult should be importable from async_patterns."""
        from async_patterns import EngineResult

        assert EngineResult is not None

    def test_import_request_result(self) -> None:
        """RequestResult should be importable from async_patterns."""
        from async_patterns import RequestResult

        assert RequestResult is not None

    def test_import_request_status(self) -> None:
        """RequestStatus should be importable from async_patterns."""
        from async_patterns import RequestStatus

        assert RequestStatus is not None

    def test_import_sync_engine(self) -> None:
        """SyncEngine should be importable from async_patterns."""
        from async_patterns import SyncEngine

        assert SyncEngine is not None

    def test_import_threaded_engine(self) -> None:
        """ThreadedEngine should be importable from async_patterns."""
        from async_patterns import ThreadedEngine

        assert ThreadedEngine is not None

    def test_import_pooling_strategy(self) -> None:
        """PoolingStrategy should be importable from async_patterns."""
        from async_patterns import PoolingStrategy

        assert PoolingStrategy is not None

    def test_import_circuit_breaker(self) -> None:
        """CircuitBreaker should be importable from async_patterns."""
        from async_patterns import CircuitBreaker

        assert CircuitBreaker is not None

    def test_import_circuit_breaker_error(self) -> None:
        """CircuitBreakerError should be importable from async_patterns."""
        from async_patterns import CircuitBreakerError

        assert CircuitBreakerError is not None

    def test_import_circuit_state(self) -> None:
        """CircuitState should be importable from async_patterns."""
        from async_patterns import CircuitState

        assert CircuitState is not None

    def test_import_batched_writer(self) -> None:
        """BatchedWriter should be importable from async_patterns."""
        from async_patterns import BatchedWriter

        assert BatchedWriter is not None

    def test_import_producer_consumer_pipeline(self) -> None:
        """ProducerConsumerPipeline should be importable from async_patterns."""
        from async_patterns import ProducerConsumerPipeline

        assert ProducerConsumerPipeline is not None

    def test_import_retry_config(self) -> None:
        """RetryConfig should be importable from async_patterns."""
        from async_patterns import RetryConfig

        assert RetryConfig is not None

    def test_import_retry_policy(self) -> None:
        """RetryPolicy should be importable from async_patterns."""
        from async_patterns import RetryPolicy

        assert RetryPolicy is not None

    def test_import_semaphore_limiter(self) -> None:
        """SemaphoreLimiter should be importable from async_patterns."""
        from async_patterns import SemaphoreLimiter

        assert SemaphoreLimiter is not None

    def test_import_acquisition_timeout_error(self) -> None:
        """AcquisitionTimeoutError should be importable from async_patterns."""
        from async_patterns import AcquisitionTimeoutError

        assert AcquisitionTimeoutError is not None

    def test_import_jsonl_writer(self) -> None:
        """JsonlWriter should be importable from async_patterns."""
        from async_patterns import JsonlWriter

        assert JsonlWriter is not None

    def test_import_sqlite_writer(self) -> None:
        """SqliteWriter should be importable from async_patterns."""
        from async_patterns import SqliteWriter

        assert SqliteWriter is not None

    def test_import_storage_writer(self) -> None:
        """StorageWriter should be importable from async_patterns."""
        from async_patterns import StorageWriter

        assert StorageWriter is not None

    def test_all_exports_match_declared(self) -> None:
        """All items in __all__ should be importable."""
        import async_patterns

        for name in async_patterns.__all__:
            assert hasattr(async_patterns, name), f"{name} not found in async_patterns"
