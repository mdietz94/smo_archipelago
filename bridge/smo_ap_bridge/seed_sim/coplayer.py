"""Coplayer (other-game) check-rate model.

A coplayer is treated as a stochastic faucet of items: every `check_complete`
event they fire, a known fraction of those checks contain SMO-bound items
(known exactly from the spoiler). The sim doesn't model their game state —
they're presumed to always have something productive to do.

Per-game means are educated guesses anchored from speedrun WRs / HLTB; the
goal is to compare 'a fast game vs slow game' coplayer impact on SMO pacing,
not to predict absolute completion times.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class CoplayerProfile:
    name: str
    total_checks: int
    mean_sec_per_check: float
    stddev_sec_per_check: float


PRESETS: dict[str, CoplayerProfile] = {
    "alttp": CoplayerProfile("ALttP",         216,  90.0, 30.0),
    "oot":   CoplayerProfile("OoT",           340, 120.0, 40.0),
    "kh":    CoplayerProfile("KH",            500, 180.0, 60.0),
    "hk":    CoplayerProfile("HollowKnight",  400,  90.0, 45.0),
    "sm":    CoplayerProfile("SuperMetroid",  100,  60.0, 25.0),
    # Solo / sanity: another SMO player. Use as a baseline.
    "smo":   CoplayerProfile("SMO-self",      565, 150.0, 60.0),
}


def parse_coplayer_spec(spec: str) -> tuple[CoplayerProfile, str | None]:
    """Parse a `--coplayer` arg into (profile, slot_match_or_none).

    Accepted forms:
        alttp                                  -> (PRESETS["alttp"], None)
        alttp:PlayerB                          -> (PRESETS["alttp"], "PlayerB")
        custom:checks=300,mean=150,std=40      -> (custom CoplayerProfile, None)
        custom:checks=300,mean=150,std=40,name=Friend
        custom:...:PlayerC                     -> custom + slot match

    Slot match (the `:Name` suffix) is for spoilers with multiple coplayer
    slots; the sim uses it to match this profile to the spoiler slot of that
    name. Without it, the profile applies to the first non-SMO slot.
    """
    if not spec:
        raise ValueError("empty --coplayer spec")

    # Detect a trailing :SlotName by splitting carefully — the custom form
    # itself contains commas/`=` but no further colon, so the *last* `:` (if
    # any) past the head is the slot delimiter.
    head, slot = spec, None
    if spec.lower().startswith("custom:"):
        # custom:<kvs>[:slot]
        body = spec[len("custom:"):]
        if ":" in body:
            kvs, slot = body.rsplit(":", 1)
        else:
            kvs = body
        kwargs = _parse_kvs(kvs)
        try:
            profile = CoplayerProfile(
                name=str(kwargs.get("name", "Custom")),
                total_checks=int(kwargs["checks"]),
                mean_sec_per_check=float(kwargs["mean"]),
                stddev_sec_per_check=float(kwargs.get("std", float(kwargs["mean"]) * 0.4)),
            )
        except KeyError as e:
            raise ValueError(
                f"custom coplayer missing {e!s} (need checks=, mean=)"
            ) from None
        return profile, slot
    else:
        if ":" in spec:
            head, slot = spec.split(":", 1)
        if head not in PRESETS:
            raise ValueError(
                f"unknown coplayer preset {head!r}; choices: {sorted(PRESETS)}, or 'custom:...'"
            )
        return PRESETS[head], slot


def _parse_kvs(spec: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in spec.split(","):
        if "=" not in part:
            raise ValueError(f"bad custom-coplayer kv {part!r}; expected key=value")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def sample_interarrival(profile: CoplayerProfile, rng: random.Random) -> float:
    """Time-to-next-check for this coplayer, clamped to >= 1s."""
    return max(1.0, rng.gauss(profile.mean_sec_per_check, profile.stddev_sec_per_check))
