"""Async-Patterns Performance Lab.

A production-grade benchmarking and implementation suite demonstrating mastery
of Python's asyncio ecosystem for high-concurrency data acquisition.
"""

from async_patterns.engine import (
    Engine,
    EngineResult,
    RequestResult,
    RequestStatus,
    SyncEngine,
    ThreadedEngine,
)
from async_patterns.patterns.semaphore import AcquisitionTimeoutError

__version__ = "0.1.0"

__all__ = [
    "AcquisitionTimeoutError",
    "Engine",
    "EngineResult",
    "RequestResult",
    "RequestStatus",
    "SyncEngine",
    "ThreadedEngine",
]
