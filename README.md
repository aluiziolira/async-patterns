# Async-Patterns Performance Lab

> A production-grade benchmarking and implementation suite demonstrating mastery of Python's `asyncio` ecosystem for high-concurrency data acquisition.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-220%20passed-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-86%25-brightgreen.svg)]()
[![mypy](https://img.shields.io/badge/mypy-strict-blue.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

This project answers a critical question for distributed systems engineers: *"How do we maximize throughput while maintaining reliability under unpredictable load conditions?"*

The Async-Patterns Performance Lab implements three distinct approaches to concurrent HTTP requests, enabling apples-to-apples performance comparisons:

| Approach     | Implementation         | Status     | Purpose                       |
| ------------ | ---------------------- | ---------- | ----------------------------- |
| Synchronous  | `requests` library     | ✅ Complete | Baseline for comparison       |
| Threaded     | `ThreadPoolExecutor`   | ✅ Complete | Common "good enough" solution |
| Asynchronous | `httpx` with `asyncio` | ✅ Complete | Target implementation         |

### Key Features

- **Multi-Paradigm Benchmark Suite** — Compare sync, threaded, and async implementations
- **Bounded Concurrency Controller** — Prevents resource exhaustion via semaphore-based limiting with timeout support
- **Circuit Breaker Pattern** — Adaptive rate limiter with state machine (CLOSED → OPEN → HALF_OPEN)
- **Retry with Exponential Backoff** — Configurable retry logic with jitter and per-request budget tracking
- **Streaming Data Pipeline** — Zero-copy architecture with producer-consumer pattern and backpressure
- **Connection Pool Optimization** — HTTP/2 multiplexing with configurable keepalive tuning
- **Event Loop Monitoring** — Real-time asyncio health metrics with structured JSON logging
- **Latency Percentiles** — P50/P95/P99 metrics for performance analysis

## Quick Start

### Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/) for dependency management

### Installation

```bash
# Clone the repository
git clone https://github.com/aluiziolira/async-patterns.git
cd async-patterns

# Install dependencies
make install

# Verify installation
make test
```

### Running Benchmarks

```bash
make benchmark
```

### Basic Usage

```python
import asyncio
from async_patterns import SyncEngine, ThreadedEngine, AsyncEngineImpl

# Synchronous baseline
sync_engine = SyncEngine(timeout=10.0)
result = sync_engine.run(["https://httpbin.org/get", "https://httpbin.org/ip"])
print(f"RPS: {result.rps:.2f}, P95 Latency: {result.p95_latency_ms:.2f}ms")

# Threaded for better performance
threaded_engine = ThreadedEngine(max_workers=10, timeout=10.0)
result = threaded_engine.run(["https://httpbin.org/get"] * 100)
print(f"RPS: {result.rps:.2f}, Success: {result.success_count}/{len(result.results)}")

# Async for maximum throughput
async def main():
    engine = AsyncEngineImpl(max_concurrent=50)
    result = await engine.run(["https://httpbin.org/get"] * 100)
    print(f"RPS: {result.rps:.2f}, Memory: {result.peak_memory_mb:.2f}MB")

asyncio.run(main())
```

## Project Structure

```
async-patterns/
├── src/async_patterns/          # Main package
│   ├── engine/                  # Benchmark engines
│   │   ├── base.py              # Engine protocol definition
│   │   ├── async_base.py        # AsyncEngine protocol definition
│   │   ├── models.py            # Domain models (RequestResult, EngineResult, etc.)
│   │   ├── sync_engine.py       # Synchronous baseline implementation
│   │   ├── threaded_engine.py   # ThreadPoolExecutor implementation
│   │   └── async_engine.py      # Async implementation with httpx
│   ├── patterns/                # Concurrency patterns
│   │   ├── semaphore.py         # Bounded concurrency with timeout
│   │   ├── circuit_breaker.py   # Adaptive rate limiting state machine
│   │   ├── retry.py             # Exponential backoff with jitter
│   │   └── pipeline.py          # Producer-consumer with backpressure
│   ├── observability/           # Metrics and monitoring
│   │   └── loop_monitor.py      # Event loop health tracking
│   └── persistence/             # Data storage (placeholder)
├── tests/                       # Test suite (220 tests, 86% coverage)
│   ├── unit/                    # Unit tests for each module
│   └── integration/             # Cross-engine comparison tests
└── benchmarks/                  # Benchmark runner and scenarios
```

## API Reference

### Engines

| Class            | Description                | Key Parameters                                   |
| ---------------- | -------------------------- | ------------------------------------------------ |
| `SyncEngine`     | Sequential HTTP requests   | `timeout: float = 30.0`                          |
| `ThreadedEngine` | Concurrent via thread pool | `max_workers: int = 10`, `timeout: float = 30.0` |
| `AsyncEngineImpl` | Async with bounded concurrency | `max_concurrent: int = 100`, `config: ConnectionConfig` |

### Data Models

| Class              | Description                                                                |
| ------------------ | -------------------------------------------------------------------------- |
| `RequestResult`    | Individual request outcome (url, status_code, latency_ms, error)           |
| `EngineResult`     | Aggregate metrics (rps, success_count, error_count, p50/p95/p99 latencies) |
| `ConnectionConfig` | Connection pool settings (max_connections, timeout, keepalive, http2)      |
| `RetryConfig`      | Retry strategy (max_retries, base_delay, max_delay, jitter_factor)         |

### Concurrency Patterns

| Class                      | Description                           | Key Parameters                              |
| -------------------------- | ------------------------------------- | ------------------------------------------- |
| `SemaphoreLimiter`         | Bounded concurrency with timeout      | `max_concurrent`, `acquire_timeout`         |
| `CircuitBreaker`           | Adaptive rate limiter (state machine) | `failure_threshold`, `error_rate_threshold` |
| `RetryPolicy`              | Exponential backoff with jitter       | `max_attempts`, `base_delay_ms`             |
| `ProducerConsumerPipeline` | Streaming pipeline with backpressure  | `max_queue_size`, `batch_size`              |
| `EventLoopMonitor`         | Asyncio health monitoring             | `sample_interval_seconds`                   |

### EngineResult Properties

```python
result = engine.run(urls)
result.rps              # Requests per second
result.success_count    # Count of 2xx responses
result.error_count      # Count of errors (4xx/5xx or exceptions)
result.p50_latency_ms   # Median latency
result.p95_latency_ms   # 95th percentile latency
result.p99_latency_ms   # 99th percentile latency
result.peak_memory_mb   # Peak memory usage
```

### Circuit Breaker Usage

```python
from async_patterns.patterns import CircuitBreaker

breaker = CircuitBreaker(
    name="api",
    failure_threshold=5,
    error_rate_threshold=0.1,  # 10%
    open_state_duration=60.0,
)

async def make_request():
    async with breaker:
        response = await client.get(url)
        return response
```

### Pipeline Usage

```python
from async_patterns.patterns import ProducerConsumerPipeline

async def process_batch(items: list[dict]):
    await database.insert_many(items)

pipeline = ProducerConsumerPipeline(
    on_batch=process_batch,
    max_queue_size=1000,
    batch_size=100,
    batch_timeout_seconds=5,
)

pipeline.start()
for item in data_stream:
    await pipeline.put(item)
await pipeline.stop()
```

## Development Commands

```bash
make install     # Install dependencies via Poetry
make test        # Run pytest with coverage (target: ≥85%)
make lint        # Run ruff linter and formatter check
make format      # Auto-format code with ruff
make type-check  # Run mypy in strict mode
make benchmark   # Execute benchmark suite
make clean       # Remove cache files and build artifacts
```

## Technology Stack

| Layer         | Technology                  | Notes                                              |
| ------------- | --------------------------- | -------------------------------------------------- |
| Runtime       | Python 3.12+                | Required for `asyncio.TaskGroup`, pattern matching |
| Event Loop    | `uvloop` (optional)         | 2-4x performance on Linux (`poetry install -E performance`) |
| HTTP Client   | `requests` / `httpx`        | `requests` for sync/threaded, `httpx` for async (HTTP/2) |
| Testing       | `pytest` + `pytest-asyncio` | 220 tests, 86% coverage                            |
| Type Checking | `mypy --strict`             | Zero type errors (16 source files)                 |
| Linting       | `ruff`                      | Fast, comprehensive                                |

## Current Status Summary

**Phase 1-4: ✅ COMPLETE**

| Criterion       | Target            | Actual                    |
| --------------- | ----------------- | ------------------------- |
| Test Coverage   | ≥85%              | **86%**                   |
| Tests Passing   | 100%              | **220/220**               |
| Type Safety     | `mypy --strict`   | **0 errors**              |
| Lint Compliance | `ruff` clean      | **All passed**            |
| Async ≥5x Sync  | Performance delta | **Verified**              |
| Circuit Breaker | State machine     | **CLOSED/OPEN/HALF_OPEN** |
| Pipeline        | Backpressure      | **Implemented**           |

## Roadmap

| Phase   | Objective                                                | Status     |
| ------- | -------------------------------------------------------- | ---------- |
| Phase 1 | Foundation (scaffold, sync & threaded engines)           | ✅ Complete |
| Phase 2 | Async Core (async engine, semaphore, connection pooling) | ✅ Complete |
| Phase 3 | Resilience (circuit breaker, retry logic)                | ✅ Complete |
| Phase 4 | Pipeline & Observability (producer-consumer, metrics)    | ✅ Complete |
| Phase 5 | Polish & Documentation (results, final review)           | 🚧 In Progress |

## Contributing

This is a portfolio project demonstrating production-grade async patterns. Contributions are welcome!

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Write tests first (TDD)
4. Implement the feature
5. Ensure `make test && make lint && make type-check` pass
6. Open a Pull Request

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Python asyncio Documentation](https://docs.python.org/3/library/asyncio.html)
- [httpx Documentation](https://www.python-httpx.org/)
- [uvloop Performance](https://github.com/MagicStack/uvloop)
- [Circuit Breaker Pattern (Martin Fowler)](https://martinfowler.com/bliki/CircuitBreaker.html)


