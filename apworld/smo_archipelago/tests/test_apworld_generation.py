"""Apworld generation sweep test.

Drives `scripts/ap_generate.py` over a matrix of yaml option combinations
to confirm the new per-kingdom Peace toggles + per-area annoying-cluster
toggles produce seeds that AP can actually generate. Each combination is
run twice: once as the only slot, once paired with a second random world
to exercise multi-world fill logic.

Skipped by default. Enable with `SMOAP_LIVE_AP=1` to opt in (the same
gate as test_ap_loopback — generation needs the Archipelago submodule
checked out and its pip deps installed in the bridge venv).

    SMOAP_LIVE_AP=1 bridge/.venv/bin/python -m pytest -v bridge/tests/test_apworld_generation.py

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


# "all-new-off" (every per-kingdom Peace toggle + every annoying-cluster
# toggle set to false) is intentionally NOT enumerated -- the resulting
# location count drops below the apworld's progression item count and AP's
# Fill stage raises FillError. It's a known-bad config we warn users against,
# not a scenario we're trying to support.
def _build_scenarios() -> list[tuple[str, dict[str, bool]]]:
    fast = os.environ.get("SMOAP_GEN_TEST_FAST") == "1"
    if fast:
        return [("all_on", _all_on())]
    return [
        ("all_on", _all_on()),
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
