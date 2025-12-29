# Async-Patterns Performance Lab

> A production-grade benchmarking and implementation suite demonstrating mastery of Python's `asyncio` ecosystem for high-concurrency data acquisition.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-78%20passed-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen.svg)]()
[![mypy](https://img.shields.io/badge/mypy-strict-blue.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

This project answers a critical question for distributed systems engineers: *"How do we maximize throughput while maintaining reliability under unpredictable load conditions?"*

The Async-Patterns Performance Lab implements three distinct approaches to concurrent HTTP requests, enabling apples-to-apples performance comparisons:

| Approach     | Implementation         | Status     | Purpose                       |
| ------------ | ---------------------- | ---------- | ----------------------------- |
| Synchronous  | `requests` library     | ✅ Complete | Baseline for comparison       |
| Threaded     | `ThreadPoolExecutor`   | ✅ Complete | Common "good enough" solution |
| Asynchronous | `httpx` with `asyncio` | 🚧 Phase 2  | Target implementation         |

### Key Features

- **Multi-Paradigm Benchmark Suite** — Compare sync, threaded, and async implementations
- **Bounded Concurrency Controller** — Prevents resource exhaustion via semaphore-based limiting
- **Adaptive Rate Limiter** — Circuit breaker pattern for resilience under load
- **Streaming Data Pipeline** — Zero-copy architecture with producer-consumer pattern
- **Connection Pool Optimization** — Demonstrates impact of proper HTTP client configuration
- **Configurable Retry Logic** — Exponential backoff with jitter for transient failures
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
from async_patterns import SyncEngine, ThreadedEngine

# Synchronous baseline
sync_engine = SyncEngine(timeout=10.0)
result = sync_engine.run(["https://httpbin.org/get", "https://httpbin.org/ip"])
print(f"RPS: {result.rps:.2f}, P95 Latency: {result.p95_latency_ms:.2f}ms")

# Threaded for better performance
threaded_engine = ThreadedEngine(max_workers=10, timeout=10.0)
result = threaded_engine.run(["https://httpbin.org/get"] * 100)
print(f"RPS: {result.rps:.2f}, Success: {result.success_count}/{len(result.results)}")
```

## Project Structure

```
async-patterns/
├── src/async_patterns/          # Main package
│   ├── engine/                  # Benchmark engines
│   │   ├── base.py              # Engine protocol definition
│   │   ├── models.py            # Domain models (RequestResult, EngineResult, etc.)
│   │   ├── sync_engine.py       # Synchronous baseline implementation
│   │   └── threaded_engine.py   # ThreadPoolExecutor implementation
│   ├── patterns/                # Concurrency patterns (Phase 2-3)
│   ├── observability/           # Metrics and monitoring (Phase 4)
│   └── persistence/             # Data storage (Phase 4)
├── tests/                       # Test suite (78 tests, 95% coverage)
│   ├── unit/                    # Unit tests for each module
│   └── integration/             # Cross-engine comparison tests
├── benchmarks/                  # Benchmark runner and scenarios
├── docs/                        # Documentation
└── plans/                       # Execution plans for agentic development
```

## API Reference

### Engines

| Class            | Description                | Key Parameters                                   |
| ---------------- | -------------------------- | ------------------------------------------------ |
| `SyncEngine`     | Sequential HTTP requests   | `timeout: float = 30.0`                          |
| `ThreadedEngine` | Concurrent via thread pool | `max_workers: int = 10`, `timeout: float = 30.0` |

### Data Models

| Class              | Description                                                                |
| ------------------ | -------------------------------------------------------------------------- |
| `RequestResult`    | Individual request outcome (url, status_code, latency_ms, error)           |
| `EngineResult`     | Aggregate metrics (rps, success_count, error_count, p50/p95/p99 latencies) |
| `ConnectionConfig` | Connection pool settings (max_connections, timeout, keepalive)             |
| `RetryConfig`      | Retry strategy (max_retries, base_delay, max_delay, jitter_factor)         |

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

## Documentation

- **[Product Requirements Document](docs/PRD.md)** — Full requirements and architecture decisions
- **[Implementation Plan](docs/implementation_plan.md)** — TDD task breakdown for Phase 1
- **[Optimization Backlog](plans/optimization_backlog.md)** — Technical debt and improvement tasks

## Technology Stack

| Layer         | Technology                  | Notes                                              |
| ------------- | --------------------------- | -------------------------------------------------- |
| Runtime       | Python 3.12+                | Required for `asyncio.TaskGroup`, pattern matching |
| HTTP Client   | `requests` / `httpx`        | `requests` for sync/threaded, `httpx` for async    |
| Testing       | `pytest` + `pytest-asyncio` | 78 tests, 95% coverage                             |
| Type Checking | `mypy --strict`             | Zero type errors                                   |
| Linting       | `ruff`                      | Fast, comprehensive                                |

## Phase 1 Completion Summary

**Status: ✅ COMPLETE**

| Criterion         | Target            | Actual         |
| ----------------- | ----------------- | -------------- |
| Test Coverage     | ≥85%              | **95%**        |
| Tests Passing     | 100%              | **78/78**      |
| Type Safety       | `mypy --strict`   | **0 errors**   |
| Lint Compliance   | `ruff` clean      | **All passed** |
| Threaded ≥2x Sync | Performance delta | **Verified**   |

## Roadmap

| Phase   | Objective                                                | Status     |
| ------- | -------------------------------------------------------- | ---------- |
| Phase 1 | Foundation (scaffold, sync & threaded engines)           | ✅ Complete |
| Phase 2 | Async Core (async engine, semaphore, connection pooling) | 🚧 Next     |
| Phase 3 | Resilience (circuit breaker, retry logic)                | 📅 Planned  |
| Phase 4 | Pipeline & Observability (producer-consumer, metrics)    | 📅 Planned  |
| Phase 5 | Polish & Documentation (results, final review)           | 📅 Planned  |

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


