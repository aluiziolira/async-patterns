# Async-Patterns Performance Lab

> Maximize HTTP throughput in Python: from 130 RPS (sync) to 2,500 RPS (async) — a **20x improvement**.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/coverage-86%25-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Results

| Engine      | Time | Throughput    | vs Sync |
| ----------- | ---- | ------------- | ------- |
| Synchronous | 39s  | 130 RPS       | 1.0x    |
| Threaded    | 8s   | 600 RPS       | 5x      |
| **Async**   | 2s   | **2,500 RPS** | **20x** |

Connection pooling adds another **2x** on top:

```
NAIVE strategy:     3.6s (1,400 RPS)
OPTIMIZED strategy: 2.0s (2,500 RPS)
Improvement Factor: 2x
```

**NAIVE** creates a new HTTP session per request (TCP handshake + TLS negotiation every time). **OPTIMIZED** reuses a single session with persistent connections — eliminating handshake overhead and allowing HTTP keep-alive.

## Why This Matters

Most Python HTTP code falls into two traps: **sequential requests** that waste time waiting on I/O, or **unbounded concurrency** that overwhelms servers and exhausts resources. This project demonstrates the middle path — production-grade async patterns that maximize throughput while maintaining reliability under load.

## Key Patterns

- **Bounded Concurrency** — Semaphore-based limiting prevents resource exhaustion
- **Circuit Breaker** — State machine (CLOSED → OPEN → HALF_OPEN) stops cascade failures
- **Exponential Backoff** — Retry with jitter for graceful recovery from transient errors
- **Producer-Consumer Pipeline** — Backpressure-aware streaming with constant memory footprint
- **Connection Pooling** — Session reuse eliminates TCP/TLS handshake overhead (2.8x improvement)

## Quick Start

**Prerequisites:** Python 3.12+ and [Poetry](https://python-poetry.org/)

```bash
git clone https://github.com/aluiziolira/async-patterns.git
cd async-patterns
make install
make benchmark
```

Async requests use `aiohttp`, while sync/threaded engines use `requests`. Prefer context managers
(`async with AsyncEngine(...)` / `with ThreadedEngine(...)`) to ensure sessions are closed across
all workers.

## Rate Limit Retries (HTTP 429)

429 responses are treated as transient failures by `RetryPolicy`, so they will be retried. When a
server sends a `Retry-After` header, the engine includes it in the raised error message so custom
retry logic can inspect it (the built-in retry policy does not automatically delay on it).

```python
from async_patterns.engine.async_engine import AsyncEngine
from async_patterns.patterns.retry import RetryPolicy, RetryConfig

retry_policy = RetryPolicy(RetryConfig(max_attempts=3, base_delay=0.5))
engine = AsyncEngine(retry_policy=retry_policy, max_concurrent=10)

result = await engine.run(["https://api.example.com/endpoint"])
```

## Benchmark Suite

| Command                     | Description                                             | Duration |
| --------------------------- | ------------------------------------------------------- | -------- |
| `make benchmark`            | Compare Sync vs Threaded vs Async engines (3 runs each) | ~2.5min  |
| `make benchmark-full`       | Sustained load test (duration mode, summary output)     | 60s      |
| `make benchmark-resilience` | Circuit breaker state transitions under 100% errors     | ~6s      |

**Resilience benchmark validates:**
- CLOSED → OPEN transition on failure threshold
- OPEN → HALF_OPEN transition after timeout
- Full recovery cycle back to CLOSED state

**Streaming runner (separate):** For backpressure-aware persistence (JSONL/SQLite) and pipeline
metrics, use `benchmarks/streaming_runner.py` programmatically. It is intentionally not wired to a
Make target.

## Technical Decisions

**Why `aiohttp` over `httpx`?** While `httpx` offers a unified sync/async API, we chose `aiohttp` for its mature async-first architecture and tighter event loop integration. In high-concurrency scenarios (1000+ simultaneous connections), `aiohttp`'s native design avoids the abstraction overhead that sync-compatible libraries carry. The trade-off is API complexity — `aiohttp` requires explicit session management — but for a benchmarking suite focused on maximum throughput, raw performance wins.

**Why bounded concurrency instead of `asyncio.gather()`?** Unbounded `gather()` is the most common async anti-pattern. Firing 10,000 requests simultaneously looks fast in demos but collapses in production: connection pools exhaust, servers return 429s, and memory spikes kill your process. Our semaphore-based limiter enforces a configurable ceiling (default: 100 concurrent requests) while a circuit breaker monitors error rates and latency. When the downstream service degrades, we fail fast rather than pile on. This adds ~2ms overhead per request but prevents the catastrophic failures that make async code "too risky" for production.

## Learn More

- [API Reference](docs/API.md) — Engine classes, data models, pattern interfaces
- [Development Guide](docs/DEVELOPMENT.md) — Setup, commands, project structure

## License

MIT License — see [LICENSE](LICENSE) for details.
