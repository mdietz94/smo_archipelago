"""Smoke tests for chart rendering — assert PNGs are written + non-empty."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("numpy")

from smo_ap_bridge.seed_sim import charts, coplayer, sim, timing
from smo_ap_bridge.seed_sim.spoiler import parse_spoiler

FIXTURE = Path(__file__).parent / "fixtures" / "sample_spoiler.txt"


def _run_a_few():
    sim.register_capture_names({"Goomba"})
    sp = parse_spoiler(FIXTURE)
    profile = timing.get_profile("default")
    cp_spec = [(coplayer.PRESETS["alttp"], "Link")]
    return [
        sim.run_one(sp, profile, cp_spec, sim_seed=i, time_cap_sec=8 * 3600)
        for i in range(3)
    ]


def test_render_all_writes_pngs(tmp_path: Path) -> None:
    results = _run_a_few()
    written = charts.render_all(results, tmp_path)
    assert len(written) == 5
    expected = {
        "reachable_over_time.png", "kingdom_dwell.png", "bk_heatmap.png",
        "completion_when_leaving.png", "coplayer_gap_hist.png",
    }
    assert {p.name for p in written} == expected
    for p in written:
        assert p.exists()
        assert p.stat().st_size > 0


def test_render_handles_empty_results(tmp_path: Path) -> None:
    # All renderers must tolerate empty inputs (e.g. all sims hit the time cap).
    written = charts.render_all([], tmp_path)
    for p in written:
        assert p.exists()
        assert p.stat().st_size > 0
