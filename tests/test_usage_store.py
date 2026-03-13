"""Tests for persistent usage accounting."""

from __future__ import annotations

from drclaw.usage.store import AgentUsageStore


def test_usage_store_persists_all_time_and_tracks_this_run(tmp_path):
    path = tmp_path / "runtime" / "agent-usage.json"
    store = AgentUsageStore(path)
    store.record(
        agent_id="main",
        input_tokens=100,
        output_tokens=25,
        input_cost_usd=0.001,
        output_cost_usd=0.002,
        total_cost_usd=0.003,
    )

    snap = store.snapshot()
    row = next(item for item in snap["agents"] if item["agent_id"] == "main")
    assert row["all_time"]["total_tokens"] == 125
    assert row["this_run"]["total_tokens"] == 125
    assert row["all_time"]["total_cost_usd"] == 0.003

    # New process/boot: all-time should persist, this_run should reset.
    reloaded = AgentUsageStore(path)
    snap2 = reloaded.snapshot()
    row2 = next(item for item in snap2["agents"] if item["agent_id"] == "main")
    assert row2["all_time"]["total_tokens"] == 125
    assert row2["this_run"]["total_tokens"] == 0


def test_usage_store_accumulates_multiple_records(tmp_path):
    store = AgentUsageStore(tmp_path / "usage.json")
    store.record(
        agent_id="proj:abc",
        input_tokens=10,
        output_tokens=5,
        input_cost_usd=0.0002,
        output_cost_usd=0.0001,
        total_cost_usd=0.0003,
    )
    store.record(
        agent_id="proj:abc",
        input_tokens=2,
        output_tokens=3,
        input_cost_usd=0.00002,
        output_cost_usd=0.00003,
        total_cost_usd=0.00005,
    )

    snap = store.snapshot()
    row = next(item for item in snap["agents"] if item["agent_id"] == "proj:abc")
    assert row["all_time"]["input_tokens"] == 12
    assert row["all_time"]["output_tokens"] == 8
    assert row["all_time"]["total_tokens"] == 20
    assert round(row["all_time"]["total_cost_usd"], 8) == 0.00035
