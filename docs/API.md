# API Reference

## Engines

| Class            | Description                    | Key Parameters                                                 |
| ---------------- | ------------------------------ | -------------------------------------------------------------- |
| `SyncEngine`     | Sequential HTTP requests       | `timeout: float = 30.0`                                        |
| `ThreadedEngine` | Concurrent via thread pool     | `max_workers: int = 10`, `timeout: float = 30.0`               |
| `AsyncEngine`    | Async with bounded concurrency | `max_concurrent: int = 100`, `circuit_breaker`, `retry_policy` |

### Engine Protocols

The engine interfaces are split into sync and async protocols for accurate typing:

- `SyncEngineProtocol`: `def run(self, urls: list[str]) -> EngineResult`
- `AsyncEngineProtocol`: `async def run(self, urls: list[str]) -> EngineResult`
- `Engine`: union of `SyncEngineProtocol | AsyncEngineProtocol`

### Context Manager Support

All engines support context managers for proper resource cleanup:

```python
# AsyncEngine (async context manager)
async with AsyncEngine(max_concurrent=100) as engine:
    result = await engine.run(urls)
# Sessions closed, pending tasks cancelled on exit

# ThreadedEngine (sync context manager)
with ThreadedEngine(max_workers=10) as engine:
    result = engine.run(urls)
# Thread-local sessions closed across all worker threads on exit
```

### Graceful Shutdown

```python
# Manual shutdown with timeout
await engine.close(timeout=5.0)  # AsyncEngine
engine.close()                   # ThreadedEngine
```

The `close()` method:
- Waits for in-flight requests to complete (up to `timeout` seconds)
- Force-cancels remaining tasks after timeout
- Awaits any in-flight circuit breaker latency updates
- Closes all HTTP sessions and releases response bodies

## Data Models

| Class              | Description                                                                |
| ------------------ | -------------------------------------------------------------------------- |
| `RequestResult`    | Individual request outcome (url, status_code, latency_ms, timestamp (Unix epoch), error) |
| `EngineResult`     | Aggregate metrics (rps, success_count, error_count, p50/p95/p99 latencies) |
| `ConnectionConfig` | Connection pool settings (max_connections, timeout, max_keepalive_connections, keepalive_expiry) |
| `RetryConfig`      | Retry strategy (max_attempts, base_delay, max_delay, jitter_factor, budget) |

## Concurrency Patterns

| Class                      | Description                           | Key Parameters                              |
| -------------------------- | ------------------------------------- | ------------------------------------------- |
| `SemaphoreLimiter`         | Bounded concurrency with timeout      | `max_concurrent`, `acquire_timeout`         |
| `CircuitBreaker`           | Adaptive rate limiter (state machine) | `failure_threshold`, `error_rate_threshold` |
| `RetryPolicy`              | Exponential backoff with jitter       | `max_attempts`, `base_delay`                 |
| `ProducerConsumerPipeline` | Streaming pipeline with backpressure  | `max_queue_size`, `batch_size`              |
| `EventLoopMonitor`         | Asyncio health monitoring             | `sample_interval_seconds`                   |

### Pattern Usage Examples

```python
# Bounded concurrency
async with SemaphoreLimiter(max_concurrent=50) as limiter:
    result = await fetch(url)

# Circuit breaker with automatic state transitions
breaker = CircuitBreaker(name="api", failure_threshold=5)
async with breaker:
    response = await client.get(url)

# Retry with exponential backoff
policy = RetryPolicy(RetryConfig(max_attempts=3, base_delay=1.0))
result = await policy.execute(fetch_data, circuit_breaker=breaker)
# Retries classify aiohttp + asyncio transient errors (connector, timeout, 5xx/429)

# Streaming pipeline with backpressure
pipeline = ProducerConsumerPipeline(
    on_batch=write_to_db,
    batch_size=100,
    max_queue_size=1000,
)
pipeline.start()
await pipeline.put(record)
await pipeline.stop(drain=True)
```

### ConnectionConfig Notes

- `max_keepalive_connections` maps to the per-host connection limit in `aiohttp.TCPConnector`.

### EventLoopMonitor Notes

- `callback_queue_depth` is not supported by asyncio and always returns `None`.

## RetryConfig

`RetryConfig` is the single source of truth for retry settings. All delays are in seconds, and
`max_attempts` includes the initial request.

```python
@dataclass(frozen=True, slots=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter_factor: float = 0.1
    retry_budget: int = 10
    retry_budget_window_seconds: float = 60.0
```

Usage:

```python
config = RetryConfig(max_attempts=5, base_delay=0.5, max_delay=10.0, jitter_factor=0.2)
policy = RetryPolicy(config=config)
```

## EngineResult Properties

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

## Circuit Breaker Pattern

The `CircuitBreaker` implements a state machine (CLOSED → OPEN → HALF_OPEN) to prevent cascade failures in distributed systems. It tracks failure rates, error thresholds, and latency patterns across a sliding time window. When integrated with `AsyncEngine`, it automatically records request latencies and can open the circuit when degradation is detected, preventing additional load on failing services.

**Key States:**
- **CLOSED**: Normal operation, all requests allowed
- **OPEN**: Circuit tripped due to failures/latency, requests rejected for `open_state_duration` seconds
- **HALF_OPEN**: Testing recovery with limited requests (`half_open_max_calls`)

**Trigger Conditions:**
- Failure count exceeds `failure_threshold`
- Error rate exceeds `error_rate_threshold` (evaluated once at least 10 requests are in the window)
- Latency exceeds `latency_threshold_multiplier × baseline_latency_ms`

### CircuitBreaker Methods

| Method                       | Description                                   |
| ---------------------------- | --------------------------------------------- |
| `await record_latency(latency_ms)` | Record request latency; high latency counts as failure |
| `wait_pending()`             | Await any in-flight latency updates (no-op if none) |
| `get_metrics()`              | Get current state, counts, and timestamps     |
| `reset()`                    | Reset to CLOSED state with cleared counters   |

## Benchmark Commands

### `make benchmark`
Standard performance benchmark comparing engine strategies. Runs 5000 requests through SyncEngine, ThreadedEngine, and AsyncEngine with both NAIVE and OPTIMIZED connection pooling. Validates:
- Requests per second (RPS) across different concurrency models
- Connection pooling efficiency (shared session vs. session-per-request)
- Latency percentiles (P50, P95, P99)
- Memory consumption patterns

### `make benchmark-full`
Extended load test for production-like scenarios. Runs for 60 seconds with high concurrency (200 concurrent requests, 32 workers) and persists streaming results to JSONL. Validates:
- Sustained throughput under continuous load
- Memory stability over time with `run_streaming()` session reuse
- Backpressure handling with streaming persistence
- Real-time result processing without memory accumulation

### `make benchmark-resilience`
Circuit breaker and retry pattern validation under 100% error rate injection. Runs two test scenarios with 50 requests each:

**Test 1: Circuit Breaker State Transitions**
- Validates CLOSED → OPEN transition when failure threshold exceeded
- Captures state transitions through concurrent periodic sampling (100ms intervals)
- Explicitly triggers HALF_OPEN transition after `open_state_duration`
- **Pass criteria**: ≥2 state transitions observed (CLOSED → OPEN → HALF_OPEN)

**Test 2: Circuit Breaker Recovery**
- Tests recovery cycle from OPEN back to CLOSED state
- Validates rejection behavior during open state
- Ensures circuit can heal when error conditions resolve
- **Pass criteria**: Circuit successfully recovers to CLOSED state

Both tests showcase the circuit breaker's adaptive behavior and self-healing capabilities under failure scenarios.
