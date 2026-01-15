# Development Guide

## Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/) for dependency management

## Installation

```bash
git clone https://github.com/aluiziolira/async-patterns.git
cd async-patterns
make install
```

## Commands

```bash
make install             # Install project dependencies
make test                # Run all tests with coverage
make test-quick          # Run tests without coverage
make lint                # Run ruff + mypy strict
make format              # Auto-format code with ruff
make clean               # Remove cache files
make benchmark           # Performance benchmark (5000 requests)
make benchmark-full      # Extended benchmark (60s, summary output)
make benchmark-resilience # Circuit breaker + retry testing
```

Streaming runner (separate)
Use `benchmarks/streaming_runner.py` programmatically for backpressure-aware persistence
and pipeline metrics (JSONL/SQLite). It is intentionally not wired to a Make target.

## Project Structure

```
async-patterns/
├── src/async_patterns/          # Main package
│   ├── engine/                  # Benchmark engines
│   │   ├── base.py              # Engine protocol definitions (sync/async)
│   │   ├── models.py            # Domain models (RequestResult, EngineResult, etc.)
│   │   ├── sync_engine.py       # Synchronous baseline implementation
│   │   ├── threaded_engine.py   # ThreadPoolExecutor implementation
│   │   └── async_engine.py      # Async implementation with aiohttp
│   ├── patterns/                # Concurrency patterns
│   │   ├── semaphore.py         # Bounded concurrency with timeout
│   │   ├── circuit_breaker.py   # Adaptive rate limiting state machine
│   │   ├── retry.py             # Exponential backoff with jitter
│   │   └── pipeline.py          # Producer-consumer with backpressure
│   ├── observability/           # Metrics and monitoring
│   │   └── loop_monitor.py      # Event loop health tracking
│   └── persistence/             # Data storage
│       ├── base.py              # Base writer protocol
│       ├── jsonl.py             # JSONL file writer
│       └── sqlite.py            # SQLite writer (WAL mode)
├── tests/                       # Test suite (240+ tests, 86% coverage)
│   ├── conftest.py              # Shared pytest fixtures
│   ├── unit/                    # Unit tests for each module
│   └── integration/             # Cross-engine comparison tests
└── benchmarks/                  # Benchmark runner and scenarios
    ├── mock_server.py           # aiohttp test server with error injection
    ├── runner.py                # Main benchmark orchestrator
    ├── streaming_runner.py      # Streaming benchmark implementation
    └── scenarios/               # Benchmark test scenarios
        ├── pooling.py           # Connection pooling comparison
        ├── resilience.py        # Circuit breaker validation
        └── retry.py             # Retry strategy testing
```

## Dependencies

### Core

| Package     | Version | Purpose                           |
| ----------- | ------- | --------------------------------- |
| `python`    | 3.12+   | Runtime environment               |
| `aiohttp`   | ^3.11   | Async HTTP client and mock server |
| `requests`  | ^2.31   | Sync HTTP client for baseline     |
| `aiofiles`  | ^25.1   | Async file I/O support            |
| `aiosqlite` | ^0.22   | Async SQLite access               |

### Optional

| Package  | Purpose                            | Installation                    |
| -------- | ---------------------------------- | ------------------------------- |
| `uvloop` | 2-4x faster event loop (Linux/Mac) | `poetry install -E performance` |

### Development

| Package          | Purpose                   |
| ---------------- | ------------------------- |
| `pytest`         | Test framework            |
| `pytest-asyncio` | Async test support        |
| `pytest-cov`     | Coverage reporting        |
| `pytest-xdist`   | Parallel test execution   |
| `mypy`           | Static type checking      |
| `ruff`           | Fast linter and formatter |

## Time Sources

- Use `time.monotonic()` for interval tracking (retry budgets, time windows).
- Use `time.perf_counter()` for per-request latency measurements.

## Circuit Breaker Integration

- Always `await breaker.record_latency(latency_ms)` after each request.
- Let the `RetryPolicy` handle `CircuitBreaker` checks via its context manager.
- Call `await breaker.wait_pending()` during shutdown to ensure in-flight updates complete.

## Troubleshooting

- `ClientConnectorError` bursts: reduce `max_concurrent` or increase connection limits.
- Latency spikes: verify the event loop monitor is running and not blocked by sync work.
- Retry budget exhaustion: increase `retry_budget` or adjust `retry_budget_window_seconds`.

## Technology Stack

| Layer         | Technology                  | Notes                                              |
| ------------- | --------------------------- | -------------------------------------------------- |
| Runtime       | Python 3.12+                | Required for `asyncio.TaskGroup`, pattern matching |
| Event Loop    | `uvloop` (optional)         | 2-4x performance on Linux/macOS                    |
| HTTP Client   | `requests` / `aiohttp`      | `requests` for sync, `aiohttp` for async           |
| Testing       | `pytest` + `pytest-asyncio` | 240+ tests, 86% coverage                           |
| Type Checking | `mypy --strict`             | Zero type errors                                   |
| Linting       | `ruff`                      | Fast, comprehensive                                |

## Artifact Locations

| Artifact              | Location                  | Description                        |
| --------------------- | ------------------------- | ---------------------------------- |
| **Benchmark Results** | `benchmark_output.json`   | Summary JSON of last benchmark run |
| **Extended Results**  | `benchmark_results.jsonl` | JSONL formatted results            |
| **Coverage Report**   | `htmlcov/index.html`      | Interactive HTML coverage report   |
| **Test Cache**        | `.pytest_cache/`          | pytest execution cache             |
| **MyPy Cache**        | `.mypy_cache/`            | mypy type checking cache           |

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Write tests first (TDD)
4. Implement the feature
5. Ensure `make test && make lint` pass
6. Open a Pull Request
