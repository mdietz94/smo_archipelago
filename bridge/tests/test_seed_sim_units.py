"""Tests for the timing + coplayer helper modules."""

from __future__ import annotations

import random
import statistics

import pytest

from smo_ap_bridge.seed_sim import coplayer, timing


# --- timing ---------------------------------------------------------------

def test_default_profile_covers_known_kingdoms() -> None:
    expected = {"Cap", "Cascade", "Sand", "Lake", "Wooded", "Cloud", "Lost",
                "Metro", "Snow", "Seaside", "Luncheon", "Ruined", "Bowser's",
                "Moon", "Mushroom", "Dark Side", "Darker Side"}
    assert expected.issubset(set(timing.DEFAULT_PROFILE))


def test_profile_lookups() -> None:
    for name in ("default", "speedrun", "casual"):
        prof = timing.get_profile(name)
        assert "Sand" in prof
    with pytest.raises(ValueError, match="unknown time profile"):
        timing.get_profile("not-a-profile")


def test_sample_respects_floor() -> None:
    rng = random.Random(0)
    samples = [timing.sample(timing.DEFAULT_PROFILE, "Cap", rng) for _ in range(2000)]
    assert min(samples) >= timing.MIN_CHECK_SEC
    # Mean should be roughly the profile's mean for Cap (90s).
    assert 60 < statistics.mean(samples) < 130


def test_sample_unknown_kingdom_uses_fallback() -> None:
    rng = random.Random(0)
    s = timing.sample(timing.DEFAULT_PROFILE, "Imaginary", rng)
    assert s >= timing.MIN_CHECK_SEC


def test_sample_handles_none_kingdom() -> None:
    rng = random.Random(0)
    s = timing.sample(timing.DEFAULT_PROFILE, None, rng)
    assert s >= timing.MIN_CHECK_SEC


def test_parse_overrides() -> None:
    out = timing.parse_overrides("Sand=180,Metro=200.5")
    assert out == {"Sand": 180.0, "Metro": 200.5}
    assert timing.parse_overrides("") == {}
    with pytest.raises(ValueError):
        timing.parse_overrides("Sand180")


def test_apply_overrides_preserves_stddev() -> None:
    base = timing.DEFAULT_PROFILE
    out = timing.apply_overrides(base, {"Sand": 222.0, "Unknown": 100.0})
    assert out["Sand"].mean_sec == 222.0
    assert out["Sand"].stddev_sec == base["Sand"].stddev_sec
    # Unknown kingdom: stddev is 0.4 * mean.
    assert out["Unknown"].stddev_sec == pytest.approx(40.0)


# --- coplayer -------------------------------------------------------------

def test_preset_parse() -> None:
    prof, slot = coplayer.parse_coplayer_spec("alttp")
    assert prof.name == "ALttP"
    assert slot is None


def test_preset_with_slot() -> None:
    prof, slot = coplayer.parse_coplayer_spec("kh:PlayerB")
    assert prof.name == "KH"
    assert slot == "PlayerB"


def test_custom_parse() -> None:
    prof, slot = coplayer.parse_coplayer_spec(
        "custom:checks=300,mean=150,std=40,name=Friend"
    )
    assert prof.name == "Friend"
    assert prof.total_checks == 300
    assert prof.mean_sec_per_check == 150.0
    assert prof.stddev_sec_per_check == 40.0
    assert slot is None


def test_custom_with_slot() -> None:
    prof, slot = coplayer.parse_coplayer_spec(
        "custom:checks=100,mean=60:PlayerC"
    )
    assert prof.total_checks == 100
    assert prof.stddev_sec_per_check == pytest.approx(24.0)  # 0.4 * 60
    assert slot == "PlayerC"


def test_unknown_preset_raises() -> None:
    with pytest.raises(ValueError, match="unknown coplayer preset"):
        coplayer.parse_coplayer_spec("not-a-game")


def test_custom_missing_required_raises() -> None:
    with pytest.raises(ValueError, match="missing"):
        coplayer.parse_coplayer_spec("custom:mean=100")


def test_sample_interarrival_clamped() -> None:
    rng = random.Random(0)
    cp = coplayer.PRESETS["alttp"]
    samples = [coplayer.sample_interarrival(cp, rng) for _ in range(1000)]
    assert min(samples) >= 1.0
