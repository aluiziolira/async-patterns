"""Paired, local retry-budget experiment; output is raw evidence, not production proof."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import statistics
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
from aiohttp import web

from async_patterns.patterns.retry import RetryBudgetExceededError, RetryExhaustedError, RetryPolicy

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Config:
    requests: int = 200
    repeats: int = 5
    workers: int = 10
    connections: int = 10
    max_attempts: int = 3
    backoff_ms: float = 10
    cap_ms: float = 50
    latency_ms: float = 5
    finite_budget: int = 40
    seed: int = 42
    window_seconds: float = 3600
    timeout_seconds: float = 30

    def validate(self):
        if not all(
            math.isfinite(value)
            for value in (
                self.backoff_ms,
                self.cap_ms,
                self.latency_ms,
                self.window_seconds,
                self.timeout_seconds,
            )
        ):
            raise ValueError("delays, window and timeout must be finite")
        if (
            min(self.requests, self.repeats, self.workers, self.connections, self.finite_budget) < 1
            or self.max_attempts < 2
        ):
            raise ValueError("counts and budget must be positive; max_attempts must be >= 2")
        if (
            min(self.backoff_ms, self.cap_ms, self.latency_ms) < 0
            or self.window_seconds <= self.timeout_seconds
            or self.timeout_seconds <= 0
        ):
            raise ValueError("delays must be nonnegative and window must exceed positive timeout")


def schedule(n, scenario, seed):
    if scenario not in ("healthy", "mixed"):
        raise ValueError("unknown scenario")
    # Largest remainder allocation: exact integer counts, with deterministic ties.
    weights = (1.0, 0.0, 0.0) if scenario == "healthy" else (0.4, 0.4, 0.2)
    exact = [n * x for x in weights]
    counts = [int(x) for x in exact]
    for i in sorted(range(3), key=lambda i: (-(exact[i] - counts[i]), i))[: n - sum(counts)]:
        counts[i] += 1
    kinds = [
        kind
        for kind, count in zip(("healthy", "transient", "persistent"), counts, strict=True)
        for _ in range(count)
    ]
    rng = random.Random(seed)
    rng.shuffle(kinds)
    rows = [
        {
            "id": i,
            "kind": kind,
            "statuses": (
                [200] if kind == "healthy" else [500, 200] if kind == "transient" else [500]
            ),
        }
        for i, kind in enumerate(kinds)
    ]
    order = list(range(n))
    rng.shuffle(order)
    return rows, order, dict(zip(("healthy", "transient", "persistent"), counts, strict=True))


async def run_arm(config, scenario, budget, rows, order):
    config.validate()
    started_at_utc = datetime.now(UTC).isoformat()
    arrivals, events = [], []
    terminal = {
        i: {"id": i, "status": "cancelled", "started": False, "logical_completion_ms": None}
        for i in order
    }
    by_id = {row["id"]: row for row in rows}

    async def handler(request):
        i, attempt = int(request.match_info["id"]), int(request.query["attempt"])
        statuses = by_id[i]["statuses"]
        status = statuses[min(attempt - 1, len(statuses) - 1)]
        arrivals.append(
            {"id": i, "attempt": attempt, "status": status, "at_s": time.monotonic() - start}
        )
        await asyncio.sleep(config.latency_ms / 1000)
        return web.Response(status=status, text="ok" if status == 200 else "error")

    app = web.Application()
    app.router.add_get("/{id}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    policy = RetryPolicy(
        max_attempts=config.max_attempts,
        base_delay_ms=config.backoff_ms,
        max_delay_ms=config.cap_ms,
        jitter_factor=0,
        retry_budget=budget,
        retry_budget_window_seconds=config.window_seconds,
    )
    window_start = policy._budget_window_start
    start = time.monotonic()
    queue = asyncio.Queue()
    for i in order:
        queue.put_nowait(i)
    interrupted = False
    error = None
    trace = aiohttp.TraceConfig()

    async def record_headers_sent(_session, context, _params):
        event = context.trace_request_ctx
        event["headers_sent"] = True
        event["sent_at_s"] = time.monotonic() - start

    trace.on_request_headers_sent.append(record_headers_sent)
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=config.connections), trace_configs=[trace]
    ) as session:

        async def worker():
            while True:
                try:
                    i = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                record = terminal[i]
                record["started"] = True
                logical_start = time.monotonic()
                attempt = 0

                async def request_once(logical_id=i):
                    nonlocal attempt
                    attempt += 1
                    t = time.monotonic()
                    event = {
                        "id": logical_id,
                        "attempt": attempt,
                        "at_s": t - start,
                        "headers_sent": False,
                        "sent_at_s": None,
                        "status": None,
                        "header_ms": None,
                        "transport_error": None,
                    }
                    events.append(event)
                    try:
                        async with session.get(
                            f"http://127.0.0.1:{port}/{logical_id}",
                            params={"attempt": attempt},
                            trace_request_ctx=event,
                        ) as response:
                            event["header_ms"] = (time.monotonic() - t) * 1000
                            event["status"] = response.status
                            await response.read()
                            response.raise_for_status()
                    except asyncio.CancelledError:
                        event["transport_error"] = "cancelled"
                        raise
                    except Exception as exc:
                        if event["status"] is None:
                            event["transport_error"] = f"{type(exc).__name__}: {exc}"
                        raise

                try:
                    await policy.execute(request_once)
                    record["status"] = "success"
                except RetryBudgetExceededError:
                    record["status"] = "budget_denied"
                except RetryExhaustedError:
                    record["status"] = "failure"
                except asyncio.CancelledError:
                    record["status"] = "cancelled"
                    raise
                except Exception as exc:
                    record["status"] = "failure"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    record["logical_completion_ms"] = (time.monotonic() - logical_start) * 1000
                    queue.task_done()

        tasks = [asyncio.create_task(worker()) for _ in range(config.workers)]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), config.timeout_seconds)
        except (TimeoutError, asyncio.CancelledError, KeyboardInterrupt) as exc:
            interrupted = True
            error = type(exc).__name__
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await runner.cleanup()
    duration = time.monotonic() - start
    counts = Counter(x["status"] for x in terminal.values())
    sent_events = [event for event in events if event["headers_sent"]]
    initial_attempts = sum(event["attempt"] == 1 for event in sent_events)
    retries = sum(event["attempt"] > 1 for event in sent_events)
    initiated_retries = sum(event["attempt"] > 1 for event in events)
    sent_keys = Counter((event["id"], event["attempt"]) for event in sent_events)
    arrival_keys = Counter((arrival["id"], arrival["attempt"]) for arrival in arrivals)
    unobserved_sends = [
        event for event in sent_events if (event["id"], event["attempt"]) not in arrival_keys
    ]
    arrivals_reconciled = (
        all(count == 1 for count in sent_keys.values())
        and all(count == 1 for count in arrival_keys.values())
        and arrival_keys <= sent_keys
        and all(event["transport_error"] is not None for event in unobserved_sends)
    )
    admitted = policy.get_metrics().attempt_count
    checks = {
        "all_ids_terminal": len(terminal) == config.requests
        and all(
            x["status"] in ("success", "failure", "budget_denied", "cancelled")
            for x in terminal.values()
        ),
        "no_window_reset": policy._budget_window_start == window_start
        and duration < config.window_seconds,
        "admitted_matches_initiated_retries": admitted == initiated_retries,
        "retry_budget_respected": retries <= admitted <= budget,
        "sent_attempts_partition": len(sent_events) == initial_attempts + retries,
        "arrivals_reconciled": arrivals_reconciled,
    }
    return {
        "scenario": scenario,
        "budget": budget,
        "config": asdict(config),
        "started_at_utc": started_at_utc,
        "duration_s": duration,
        "interrupted": interrupted,
        "interruption": error,
        "attempt_events": events,
        "server_arrivals": arrivals,
        "terminal": list(terminal.values()),
        "checks": checks,
        "summary": {
            "statuses": dict(counts),
            "initiated_attempts": len(events),
            "sent_attempts": len(sent_events),
            "initial_attempts": initial_attempts,
            "sent_retries": retries,
            "server_arrivals": len(arrivals),
            "unobserved_sends": len(unobserved_sends),
            "amplification": len(sent_events) / config.requests,
            "admitted_retries": admitted,
        },
    }


def source_info():
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), *args], text=True
        ).strip()

    paths = [
        ROOT / "benchmarks/retry_budget_comparison.py",
        ROOT / "tests/integration/test_retry_budget_comparison.py",
        ROOT / "src/async_patterns/patterns/retry.py",
        ROOT / "src/async_patterns/engine/models.py",
    ]
    return {
        "head": git("rev-parse", "HEAD"),
        "status_porcelain": git("status", "--porcelain"),
        "hash_normalization": "UTF-8 source text with LF line endings",
        "sha256": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(
                p.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
            for p in paths
        },
    }


def aggregate(runs):
    out = {}
    for scenario in ("healthy", "mixed"):
        for arm in ("non_binding", "finite"):
            selected = [r for r in runs if r["scenario"] == scenario and r["arm"] == arm]
            metrics = {}
            for name, getter in {
                "success": lambda r: r["summary"]["statuses"].get("success", 0),
                "failure": lambda r: r["summary"]["statuses"].get("failure", 0),
                "budget_denied": lambda r: r["summary"]["statuses"].get("budget_denied", 0),
                "sent_retries": lambda r: r["summary"]["sent_retries"],
                "amplification": lambda r: r["summary"]["amplification"],
                "duration_s": lambda r: r["duration_s"],
            }.items():
                values = [getter(r) for r in selected]
                if values:
                    metrics[name] = {
                        "values": values,
                        "mean": statistics.mean(values),
                        "min": min(values),
                        "max": max(values),
                        "stdev": statistics.stdev(values) if len(values) > 1 else 0,
                    }
            out[f"{scenario}/{arm}"] = metrics
    return out


async def experiment(config, output):
    config.validate()
    result = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "config": asdict(config),
        "source": source_info(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "aiohttp": importlib.metadata.version("aiohttp"),
        },
        "runs": [],
    }

    def save():
        result["aggregate"] = aggregate(result["runs"])
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    bad = False
    try:
        for scenario in ("healthy", "mixed"):
            rows, order, allocation = schedule(config.requests, scenario, config.seed)
            for repeat in range(config.repeats):
                arms = ["non_binding", "finite"] if repeat % 2 == 0 else ["finite", "non_binding"]
                for position, arm in enumerate(arms):
                    budget = (
                        config.requests * (config.max_attempts - 1)
                        if arm == "non_binding"
                        else config.finite_budget
                    )
                    run = await run_arm(config, scenario, budget, rows, order)
                    run.update(
                        {
                            "arm": arm,
                            "repeat": repeat,
                            "arm_position": position,
                            "seed": config.seed,
                            "schedule": rows,
                            "request_order": order,
                            "allocation": allocation,
                        }
                    )
                    result["runs"].append(run)
                    save()
                    if run["interrupted"] or not all(run["checks"].values()):
                        bad = True
                        return 1
    finally:
        result["completed_at_utc"] = datetime.now(UTC).isoformat()
        save()
    return int(bad)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name, field in Config.__dataclass_fields__.items():
        parser.add_argument(
            "--" + name.replace("_", "-"), type=type(field.default), default=field.default
        )
    args = parser.parse_args()
    config = Config(**vars(args))
    config.validate()
    directory = Path(tempfile.mkdtemp(prefix="retry-budget-comparison-"))
    output = directory / "results.json"
    print(f"Raw output: {output}", flush=True)
    return asyncio.run(experiment(config, output))


if __name__ == "__main__":
    raise SystemExit(main())
