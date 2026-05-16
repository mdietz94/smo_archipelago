"""Per-kingdom time-to-check Gaussian model.

Anchored to two real datapoints:
  * speedrun.com 100% WR ~ 8h33m / 880 moons -> ~31 s/moon (lower bound)
  * HowLongToBeat 100% casual ~ 61.5h / 880 moons -> ~250 s/moon (upper bound)

The default profile sits mid-range and weights kingdoms by perceived difficulty.
Per-kingdom means are judgment calls; expose via --time-profile and
--time-override for tuning.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class KingdomTime:
    mean_sec: float
    stddev_sec: float


# Mid-range default. Lower for early kingdoms (training-wheels moons, lots
# of shop/coin checks) and higher for late kingdoms (harder platforming,
# moon-coin shop moons that need farming).
DEFAULT_PROFILE: dict[str, KingdomTime] = {
    "Cap":       KingdomTime( 90,  36),
    "Cascade":   KingdomTime( 90,  36),
    "Sand":      KingdomTime(150,  60),
    "Lake":      KingdomTime(130,  52),
    "Wooded":    KingdomTime(150,  60),
    "Cloud":     KingdomTime( 60,  20),
    "Lost":      KingdomTime(130,  52),
    "Metro":     KingdomTime(180,  72),
    "Snow":      KingdomTime(180,  72),
    "Seaside":   KingdomTime(180,  72),
    "Luncheon":  KingdomTime(210,  84),
    "Ruined":    KingdomTime(210,  84),
    "Bowser's":  KingdomTime(210,  84),
    "Moon":      KingdomTime(180,  72),
    "Mushroom":  KingdomTime(120,  48),
    "Dark Side":   KingdomTime(300, 120),
    "Darker Side": KingdomTime(360, 144),
}

# "Speedrun" — anchored at 31 s/moon mean. Half the default stddev to model
# practiced consistency.
SPEEDRUN_PROFILE: dict[str, KingdomTime] = {
    k: KingdomTime(max(20, t.mean_sec * 0.35), t.stddev_sec * 0.3)
    for k, t in DEFAULT_PROFILE.items()
}

# "Casual" — pushes mean toward HLTB 250 s/moon.
CASUAL_PROFILE: dict[str, KingdomTime] = {
    k: KingdomTime(t.mean_sec * 1.6, t.stddev_sec * 1.2)
    for k, t in DEFAULT_PROFILE.items()
}

PROFILES: dict[str, dict[str, KingdomTime]] = {
    "default": DEFAULT_PROFILE,
    "speedrun": SPEEDRUN_PROFILE,
    "casual": CASUAL_PROFILE,
}

# Fallback for any location whose kingdom isn't in the table (e.g. "Post-Metro"
# pseudo-region locations, or unknown future content).
FALLBACK = KingdomTime(150, 60)

# Floor — a moon never takes less than 5 simulated seconds.
MIN_CHECK_SEC = 5.0


def get_profile(name: str) -> dict[str, KingdomTime]:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(
            f"unknown time profile {name!r}; choices: {sorted(PROFILES)}"
        ) from None


def apply_overrides(
    profile: dict[str, KingdomTime],
    overrides: dict[str, float],
) -> dict[str, KingdomTime]:
    """`overrides` is {kingdom_name: mean_sec}. Stddev is preserved from the
    base profile (or set to 0.4*mean if the kingdom isn't in the base)."""
    out = dict(profile)
    for k, mean in overrides.items():
        base = out.get(k)
        std = base.stddev_sec if base else mean * 0.4
        out[k] = KingdomTime(mean, std)
    return out


def parse_overrides(spec: str) -> dict[str, float]:
    """Parse a CLI string like 'Sand=180,Metro=200' into a dict."""
    out: dict[str, float] = {}
    if not spec:
        return out
    for pair in spec.split(","):
        if "=" not in pair:
            raise ValueError(f"bad --time-override pair {pair!r}; expected K=N")
        k, v = pair.split("=", 1)
        out[k.strip()] = float(v.strip())
    return out


def sample(
    profile: dict[str, KingdomTime],
    kingdom: str | None,
    rng: random.Random,
) -> float:
    """One Gaussian draw, clamped to MIN_CHECK_SEC."""
    spec = profile.get(kingdom or "", FALLBACK) if kingdom else FALLBACK
    return max(MIN_CHECK_SEC, rng.gauss(spec.mean_sec, spec.stddev_sec))
