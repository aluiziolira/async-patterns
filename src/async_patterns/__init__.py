"""Async-Patterns Performance Lab."""

from async_patterns.engine import (
    AsyncEngineProtocol,
    Engine,
    EngineResult,
    RequestResult,
    RequestStatus,
    SyncEngine,
    SyncEngineProtocol,
    ThreadedEngine,
)
from async_patterns.engine.async_engine import AsyncEngine, PoolingStrategy
from async_patterns.patterns.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
)
from async_patterns.patterns.pipeline import BatchedWriter, ProducerConsumerPipeline
from async_patterns.patterns.retry import RetryConfig, RetryPolicy
from async_patterns.patterns.semaphore import AcquisitionTimeoutError, SemaphoreLimiter
from async_patterns.persistence import JsonlWriter, SqliteWriter, StorageWriter

__version__ = "0.1.0"

__all__ = [
    # Engine
    "AsyncEngine",
    "AsyncEngineProtocol",
    "Engine",
    "EngineResult",
    "PoolingStrategy",
    "RequestResult",
    "RequestStatus",
    "SyncEngine",
    "SyncEngineProtocol",
    "ThreadedEngine",
    # Patterns
    "AcquisitionTimeoutError",
    "BatchedWriter",
    "CircuitBreaker",
    "CircuitBreakerError",
    "CircuitState",
    "ProducerConsumerPipeline",
    "RetryConfig",
    "RetryPolicy",
    "SemaphoreLimiter",
    # Persistence
    "JsonlWriter",
    "SqliteWriter",
    "StorageWriter",
]
