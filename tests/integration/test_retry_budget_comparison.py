from collections import Counter

import pytest

from benchmarks.retry_budget_comparison import Config, build_parser, run_arm, schedule


def test_cli_parses_fractional_backoff_ms():
    args = build_parser().parse_args(["--backoff-ms", "2.5"])
    assert args.backoff_ms == 2.5


def test_schedule_is_deterministic_and_exact():
    rows, order, counts = schedule(11, "mixed", 42)
    assert (rows, order, counts) == schedule(11, "mixed", 42)
    assert counts == {"healthy": 5, "transient": 4, "persistent": 2}
    assert sorted(order) == list(range(11))
    assert all(
        row["statuses"]
        == {"healthy": [200], "transient": [500, 200], "persistent": [500]}[row["kind"]]
        for row in rows
    )


@pytest.mark.asyncio
async def test_healthy_and_finite_budget_accounting():
    cfg = Config(requests=10, workers=2, connections=2, backoff_ms=0, latency_ms=0, finite_budget=1)
    rows, order, _ = schedule(10, "healthy", 42)
    healthy = await run_arm(cfg, "healthy", 1, rows, order)
    assert healthy["summary"]["statuses"] == {"success": 10}
    assert healthy["summary"]["sent_attempts"] == healthy["summary"]["server_arrivals"] == 10
    assert healthy["summary"]["sent_retries"] == 0
    assert all(healthy["checks"].values())
    rows, order, _ = schedule(10, "mixed", 42)
    finite = await run_arm(cfg, "mixed", 1, rows, order)
    assert finite["summary"]["sent_retries"] == 1
    assert finite["summary"]["sent_attempts"] == 11
    assert finite["summary"]["statuses"]["budget_denied"] > 0
    assert len(finite["server_arrivals"]) == 11
    assert all(finite["checks"].values())
    for record in finite["terminal"]:
        if record["status"] == "budget_denied":
            assert sum(e["id"] == record["id"] for e in finite["attempt_events"]) < cfg.max_attempts


@pytest.mark.asyncio
async def test_timeout_accounts_for_unstarted_and_started_requests():
    cfg = Config(requests=12, workers=3, connections=1, latency_ms=100, timeout_seconds=0.02)
    rows, order, _ = schedule(12, "healthy", 42)
    result = await run_arm(cfg, "healthy", 1, rows, order)
    assert result["interrupted"]
    assert Counter(x["status"] for x in result["terminal"]) == {"cancelled": 12}
    assert any(not x["started"] for x in result["terminal"])
    assert len({x["id"] for x in result["terminal"]}) == 12
    assert result["summary"]["initiated_attempts"] > result["summary"]["sent_attempts"]
    assert all(result["checks"].values())


@pytest.mark.parametrize(
    "field_name", ["backoff_ms", "cap_ms", "latency_ms", "window_seconds", "timeout_seconds"]
)
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_rejects_non_finite_configuration(field_name, value):
    config = Config(**{field_name: value})
    with pytest.raises(ValueError, match="must be finite"):
        config.validate()
