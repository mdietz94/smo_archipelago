"""Apworld generation sweep test.

Drives `scripts/ap_generate.py` over a matrix of yaml option combinations
to confirm the new per-kingdom Peace toggles + per-area annoying-cluster
toggles produce seeds that AP can actually generate. Each combination is
run twice: once as the only slot, once paired with a second random world
to exercise multi-world fill logic.

Skipped by default. Enable with `SMOAP_LIVE_AP=1` to opt in (the same
gate as test_ap_loopback — generation needs the Archipelago submodule
checked out and its pip deps installed in the repo-root `.venv`).

    SMOAP_LIVE_AP=1 .venv/Scripts/python -m pytest -v apworld/smo_archipelago/tests/test_apworld_generation.py

Speed: each `--skip_output` generation is ~3-5s, so the full sweep takes a
few minutes. Set `SMOAP_GEN_TEST_FAST=1` to keep only the headline cases
(all-on, all-off, master-off, one random multi) for a sub-minute smoke run.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
AP_ROOT = REPO / "vendor" / "Archipelago"
GEN_SCRIPT = REPO / "scripts" / "ap_generate.py"
INSTALL_SCRIPT = REPO / "scripts" / "install_apworld.py"

PER_KINGDOM_PEACE_TOGGLES = [
    "include_cap_peace_moons",
    "include_cascade_peace_moons",
    "include_sand_peace_moons",
    "include_lake_peace_moons",
    "include_wooded_peace_moons",
    "include_lost_peace_moons",
    "include_metro_peace_moons",
    "include_snow_peace_moons",
    "include_seaside_peace_moons",
    "include_luncheon_peace_moons",
    "include_bowsers_peace_moons",
    "include_cloud_peace_moons",
]

ANNOYING_CLUSTER_TOGGLES = [
    "include_deep_woods_moons",
    "include_minigame_moons",
    "include_hint_art_moons",
    "include_tourist_moons",
    "include_long_course_moons",
    "include_precision_capture_moons",
]

# Per-kingdom moon-count floor (matches hooks/Options.py::*MoonCount.range_start
# and tests/test_kingdom_moon_count.py). Used by the moon_count_* scenarios to
# stress _trim_kingdom_moons_to_options + _demote_surplus_kingdom_moons against
# the worst-case (smallest gate-satisfying) pool size.
MOON_COUNT_FLOORS = {
    "cascade_moon_count":  3,
    "sand_moon_count":     12,
    "lake_moon_count":     6,
    "wooded_moon_count":   12,
    "lost_moon_count":     10,
    "metro_moon_count":    16,
    "snow_moon_count":     8,
    "seaside_moon_count":  8,
    "luncheon_moon_count": 14,
    "ruined_moon_count":   1,
    "bowsers_moon_count":  6,
}

# Worlds known to be small + dependency-light, suitable as a multi-world partner.
# generate_early/create_items shouldn't require external files (no rom, no save).
CANDIDATE_PARTNER_WORLDS = [
    "APQuest",
    "Bumper Stickers",
    "Paint",
    "Meritous",
    "Yacht Dice",
]


pytestmark = pytest.mark.skipif(
    os.environ.get("SMOAP_LIVE_AP") != "1",
    reason="set SMOAP_LIVE_AP=1 to run apworld generation tests "
           "(requires vendor/Archipelago checkout + AP pip deps installed)",
)


@pytest.fixture(scope="module")
def _apworld_zip_built():
    """Build the apworld zip once for the whole module."""
    result = subprocess.run(
        [sys.executable, str(INSTALL_SCRIPT)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"install_apworld.py failed:\n{result.stdout}\n{result.stderr}")
    return True


def _smo_yaml(overrides: dict[str, bool], slot_name: str = "Mario") -> str:
    """Render an SMO player yaml. Only set keys that differ from default-on."""
    base = {
        "accessibility": "minimal",
        "death_link": False,
        # Toggles always specified for parity with the loopback seed
        # (capturesanity defaults OFF, so it needs an explicit true to
        # be exercised at all).
        "capturesanity": True,
    }
    base.update(overrides)
    body = "\n".join(f"  {k}: {str(v).lower() if isinstance(v, bool) else v}"
                     for k, v in base.items())
    return (
        f"name: {slot_name}\n"
        f"game: Spicy Meatball Overdrive\n"
        f"description: gen-sweep test\n"
        f"\n"
        f"Spicy Meatball Overdrive:\n"
        f"{body}\n"
    )


def _partner_yaml(game: str, slot_name: str = "Partner") -> str:
    # Empty per-game block; the world's defaults will fill in.
    return (
        f"name: {slot_name}\n"
        f"game: {game}\n"
        f"description: gen-sweep test partner\n"
        f"\n"
        f"{game}: {{}}\n"
    )


def _run_generation(yaml_dir: Path) -> subprocess.CompletedProcess[str]:
    out_dir = yaml_dir / "out"
    out_dir.mkdir(exist_ok=True)
    return subprocess.run(
        [
            sys.executable, str(GEN_SCRIPT),
            "--player_files_path", str(yaml_dir),
            "--outputpath", str(out_dir),
            "--skip_output",
            # Deterministic across runs for reproducibility on failure.
            "--seed", "20260516",
        ],
        capture_output=True, text=True, check=False,
        # Generate.py calls `input()` on fatal errors via an atexit handler;
        # close stdin so it returns EOFError quickly instead of blocking.
        stdin=subprocess.DEVNULL,
    )


def _assert_gen_ok(result: subprocess.CompletedProcess[str], scenario: str) -> None:
    if result.returncode != 0 or "Total Time:" not in result.stdout:
        # Surface the last ~40 lines on failure for quick triage. Avoids
        # dumping the 1000+ lines of "loading worlds..." chatter.
        tail = "\n".join(result.stdout.splitlines()[-40:])
        err = result.stderr.strip()
        pytest.fail(
            f"AP generation failed for scenario {scenario!r} (exit={result.returncode})\n"
            f"--- stdout tail ---\n{tail}\n"
            f"--- stderr ---\n{err}"
        )


# ---- option-combination scenarios ----

def _all_on() -> dict[str, bool]:
    return {}  # all defaults are already on


def _individual_off_cases() -> list[tuple[str, dict[str, bool]]]:
    """One off-case per new toggle (12 + 6 = 18 cases)."""
    cases = []
    for k in PER_KINGDOM_PEACE_TOGGLES + ANNOYING_CLUSTER_TOGGLES:
        cases.append((f"only_{k}_off", {k: False}))
    return cases


def _all_off() -> dict[str, bool]:
    """Every disabling toggle off. Stresses the kingdom-moon demotion logic in
    hooks/World.py:after_create_items -- without it, items.json's static
    progression count (450 moons) exceeds the trimmed location pool and AP
    raises FillError. With demotion, the surplus moons are useful-classified
    and Manual.adjust_filler_items trims them to fit."""
    return {k: False for k in PER_KINGDOM_PEACE_TOGGLES + ANNOYING_CLUSTER_TOGGLES}


def _moon_count_all_floor() -> dict[str, int]:
    """Every per-kingdom moon-count option pinned to its floor. Worst case for
    _demote_surplus_kingdom_moons — the trim pass leaves only the gate-required
    number of Moon items per kingdom, and adjust_filler_items has to refill the
    freed slots with filler / traps."""
    return dict(MOON_COUNT_FLOORS)


def _moon_count_with_peace_off() -> dict[str, object]:
    """All moon-counts at floor AND all peace toggles off. Combines two
    independent trim mechanisms — sanity check they stack without conflict."""
    overrides: dict[str, object] = dict(MOON_COUNT_FLOORS)
    overrides.update({k: False for k in PER_KINGDOM_PEACE_TOGGLES})
    return overrides


def _build_scenarios() -> list[tuple[str, dict[str, bool]]]:
    fast = os.environ.get("SMOAP_GEN_TEST_FAST") == "1"
    if fast:
        return [
            ("all_on", _all_on()),
            ("festival_goal", {"goal": "festival"}),
            # Phase 5 (Gap #3): exercise the talkatoo_order validator
            # against the default option set so a regression in the
            # greedy permutation builder is caught even on the fast run.
            ("talkatoo_mode", {"talkatoo_mode": True}),
            ("moon_count_all_floor", _moon_count_all_floor()),
        ]
    return [
        ("all_on", _all_on()),
        ("all_off", _all_off()),
        ("festival_goal", {"goal": "festival"}),
        ("talkatoo_mode", {"talkatoo_mode": True}),
        ("moon_count_all_floor", _moon_count_all_floor()),
        ("moon_count_cascade_floor", {"cascade_moon_count": MOON_COUNT_FLOORS["cascade_moon_count"]}),
        ("moon_count_with_peace_off", _moon_count_with_peace_off()),
        *_individual_off_cases(),
    ]


SCENARIOS = _build_scenarios()


@pytest.mark.parametrize("scenario_name,overrides", SCENARIOS)
def test_smo_generation_solo(_apworld_zip_built, scenario_name, overrides):
    """SMO alone — confirm each option combination yields a generatable seed."""
    with tempfile.TemporaryDirectory(prefix=f"smo_gen_{scenario_name}_") as td:
        td_path = Path(td)
        (td_path / "Mario.yaml").write_text(_smo_yaml(overrides), encoding="utf-8")
        result = _run_generation(td_path)
        _assert_gen_ok(result, f"solo/{scenario_name}")


@pytest.mark.parametrize("scenario_name,overrides", SCENARIOS)
def test_smo_generation_with_random_partner(_apworld_zip_built, scenario_name, overrides):
    """SMO + one randomly chosen partner world — confirms multi-world fill works."""
    # Deterministic partner per-scenario so failures reproduce.
    rng = random.Random(f"{scenario_name}-partner")
    partner = rng.choice(CANDIDATE_PARTNER_WORLDS)

    with tempfile.TemporaryDirectory(prefix=f"smo_gen_multi_{scenario_name}_") as td:
        td_path = Path(td)
        (td_path / "Mario.yaml").write_text(_smo_yaml(overrides), encoding="utf-8")
        (td_path / "Partner.yaml").write_text(_partner_yaml(partner), encoding="utf-8")
        result = _run_generation(td_path)
        _assert_gen_ok(result, f"multi/{scenario_name}+{partner}")
