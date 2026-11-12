# Async-Patterns Performance Lab

A small lab for comparing sync, threaded, and async HTTP concurrency patterns in Python — bounded concurrency, retries, circuit breaking, and streaming persistence.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Results

Historical absolute RPS numbers previously listed here are **not pinned to a recorded run artifact** in this repo, so they are omitted. Re-measure on your machine:

```bash
make install
make benchmark
```

That runs `python -m benchmarks.runner --request-count 5000 --pooling` (Sync vs Threaded vs Async, NAIVE vs OPTIMIZED pooling). Record the printed summary plus Python version, OS, and CPU count if you need citable numbers.

Connection pooling comparison (same command, `--pooling`): **NAIVE** opens a new HTTP session per request; **OPTIMIZED** reuses one session with keep-alive. The runner reports both; treat the ratio as environment-specific.

## Why This Matters

Most Python HTTP code falls into two traps: **sequential requests** that waste time waiting on I/O, or **unbounded concurrency** that overwhelms servers and exhausts resources. This project demonstrates the middle path — production-grade async patterns that maximize throughput while maintaining reliability under load.

## Key Patterns

- **Bounded Concurrency** — Semaphore-based limiting prevents resource exhaustion
- **Circuit Breaker** — State machine (CLOSED → OPEN → HALF_OPEN) stops cascade failures
- **Exponential Backoff** — Retry with jitter; honors `Retry-After` when present
- **Producer-Consumer Pipeline** — Backpressure-aware streaming with constant memory footprint
- **Connection Pooling** — Session reuse eliminates TCP/TLS handshake overhead

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

429 responses are treated as transient failures by `RetryPolicy`. When a response includes
`Retry-After` (delta-seconds or HTTP-date), the sleep before the next attempt is
`min(max(backoff, retry_after), retry_after_max)`. Malformed values fall back to normal
backoff. Cap via `RetryConfig.retry_after_max` (default 60s).

```python
from async_patterns.engine.async_engine import AsyncEngine
from async_patterns.patterns.retry import RetryPolicy, RetryConfig

retry_policy = RetryPolicy(RetryConfig(max_attempts=3, base_delay=0.5, retry_after_max=30.0))
engine = AsyncEngine(retry_policy=retry_policy, max_concurrent=10)

result = await engine.run(["https://api.example.com/endpoint"])
```

## Benchmark Suite

| Command                     | Description                                             |
| --------------------------- | ------------------------------------------------------- |
| `make benchmark`            | Compare Sync vs Threaded vs Async engines (3 runs each) |
| `make benchmark-full`       | Sustained load test (duration mode, summary output)     |
| `make benchmark-resilience` | Circuit breaker state transitions under 100% errors     |

**Resilience benchmark validates:**
- CLOSED → OPEN transition on failure threshold
- OPEN → HALF_OPEN transition after timeout
- Full recovery cycle back to CLOSED state

**Streaming runner (separate):** For backpressure-aware persistence (JSONL/SQLite) and pipeline
metrics, use `benchmarks/streaming_runner.py` programmatically. It is intentionally not wired to a
Make target.

## Known limits

- **JSONL durability under hard cancel:** flushes write a batch as one payload and restore the
  in-memory buffer if the await is interrupted before completion. A cancel that lands inside the
  write syscall can still tear the last line on disk; `close()` does not attempt to repair torn
  lines.
- **Interrupted in-flight HTTP work:** cancellation does not synthesize a `RequestResult`. The
  URL is either recorded once (completed before cancel) or absent from the result set (not
  completed). Callers that need an explicit "cancelled" status must track outstanding URLs
  themselves.
- **Half-open concurrency vs trials:** `half_open_max_calls` caps total trial admissions;
  `probe_count` caps concurrent probes within that budget.

## Technical Decisions

**Why `aiohttp` over `httpx`?** While `httpx` offers a unified sync/async API, we chose `aiohttp` for its mature async-first architecture and tighter event loop integration. In high-concurrency scenarios (1000+ simultaneous connections), `aiohttp`'s native design avoids the abstraction overhead that sync-compatible libraries carry. The trade-off is API complexity — `aiohttp` requires explicit session management — but for a benchmarking suite focused on maximum throughput, raw performance wins.

**Why bounded concurrency instead of `asyncio.gather()`?** Unbounded `gather()` is the most common async anti-pattern. Firing 10,000 requests simultaneously looks fast in demos but collapses in production: connection pools exhaust, servers return 429s, and memory spikes kill your process. Our semaphore-based limiter enforces a configurable ceiling (default: 100 concurrent requests) while a circuit breaker monitors error rates and latency. When the downstream service degrades, we fail fast rather than pile on.

## Learn More

- [API Reference](docs/API.md) — Engine classes, data models, pattern interfaces
- [Development Guide](docs/DEVELOPMENT.md) — Setup, commands, project structure

## License

MIT License — see [LICENSE](LICENSE) for details.

### Independent retry-budget comparison

Run `python -m benchmarks.retry_budget_comparison` from the repository root. This independent loopback lab compares the existing retry policy with a non-binding budget and a shared finite budget, using paired healthy and mixed schedules, alternating arm order, a bounded worker pool, and a fresh server, session, and policy per arm. It writes raw JSON to a unique operating-system temporary directory. The default mixed schedule allocates 40% healthy, 40% first-attempt failures that recover, and 20% persistent failures; integer allocation, request order, per-attempt outcomes, server arrivals, terminal outcomes, timings, source hashes, and environment are retained. Header timings and full logical completion timings measure different intervals. This controlled local experiment does not establish production behavior.
