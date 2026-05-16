"""Tests for the discrete-event simulator core."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from smo_ap_bridge.seed_sim import coplayer, sim, timing
from smo_ap_bridge.seed_sim.spoiler import parse_spoiler

FIXTURE = Path(__file__).parent / "fixtures" / "sample_spoiler.txt"


@pytest.fixture(scope="module")
def fixture_spoiler():
    sim.register_capture_names({"Goomba"})
    return parse_spoiler(FIXTURE)


def test_kingdom_of_location() -> None:
    assert sim.kingdom_of_location("Cap: Frog-Jumping Above the Fog") == "Cap"
    assert sim.kingdom_of_location("Sand: Atop the Highest Tower") == "Sand"
    assert sim.kingdom_of_location("Capture: Goomba") is None
    assert sim.kingdom_of_location("No-prefix-here") is None


def test_progression_classifier() -> None:
    sim.register_capture_names({"Goomba"})
    assert sim.looks_like_progression("Cascade Kingdom Power Moon")
    assert sim.looks_like_progression("Cascade Kingdom Multi-Moon")
    assert sim.looks_like_progression("Long Jump")
    assert sim.looks_like_progression("Goomba")
    # Filler shop item shouldn't classify as progression.
    assert not sim.looks_like_progression("Skeleton Outfit")


def test_run_one_no_coplayer_hits_cap(fixture_spoiler) -> None:
    # The fixture routes 1 of Mario's sphere-1 progression items to Link.
    # With no coplayer faucet, Mario gets stuck and the run hits the time
    # cap — that IS the correct outcome (the seed isn't completable solo).
    profile = timing.get_profile("speedrun")
    r = sim.run_one(
        fixture_spoiler, profile, coplayer_specs=[],
        sim_seed=42, time_cap_sec=2 * 3600,
    )
    assert r.final_time_sec > 0
    assert r.reachable_timeline
    # Visited at least the starting kingdom (Cap).
    assert "Cap" in r.kingdom_visit_order
    # Player 100%'d Cap because they couldn't progress.
    assert r.completion_at_exit.get("Cap", 0.0) >= 0.99


def test_run_one_with_coplayer(fixture_spoiler) -> None:
    profile = timing.get_profile("default")
    cp_spec = [(coplayer.PRESETS["alttp"], "Link")]
    r = sim.run_one(
        fixture_spoiler, profile, coplayer_specs=cp_spec,
        sim_seed=42, time_cap_sec=20 * 3600,
    )
    # The coplayer should have produced at least one item arrival, since Link
    # ships 3 items to Mario in the fixture and the time cap is huge.
    assert r.coplayer_gaps_sec or r.unlock_source_counts
    # Sphere reached should advance past 1.
    assert r.sphere_reached >= 2


def test_completion_at_exit_is_fraction(fixture_spoiler) -> None:
    profile = timing.get_profile("speedrun")
    r = sim.run_one(
        fixture_spoiler, profile, coplayer_specs=[],
        sim_seed=1, time_cap_sec=10 * 3600,
    )
    for k, frac in r.completion_at_exit.items():
        assert 0.0 <= frac <= 1.0, f"{k}={frac}"


def test_kingdom_dwell_nonnegative(fixture_spoiler) -> None:
    profile = timing.get_profile("default")
    r = sim.run_one(
        fixture_spoiler, profile, coplayer_specs=[],
        sim_seed=7, time_cap_sec=5 * 3600,
    )
    for k, dwell in r.kingdom_dwell_sec.items():
        assert dwell >= 0.0, f"{k}={dwell}"


def test_reproducibility(fixture_spoiler) -> None:
    profile = timing.get_profile("default")
    r1 = sim.run_one(
        fixture_spoiler, profile, coplayer_specs=[],
        sim_seed=999, time_cap_sec=5 * 3600,
    )
    r2 = sim.run_one(
        fixture_spoiler, profile, coplayer_specs=[],
        sim_seed=999, time_cap_sec=5 * 3600,
    )
    assert r1.final_time_sec == r2.final_time_sec
    assert r1.kingdom_visit_order == r2.kingdom_visit_order
    assert r1.kingdom_dwell_sec == r2.kingdom_dwell_sec


def test_run_many_yields_correct_count(fixture_spoiler) -> None:
    profile = timing.get_profile("default")
    out = list(sim.run_many(
        [fixture_spoiler, fixture_spoiler], profile, coplayer_specs=[],
        base_seed=1, sims_per_spoiler=3, time_cap_sec=5 * 3600,
    ))
    assert len(out) == 6
