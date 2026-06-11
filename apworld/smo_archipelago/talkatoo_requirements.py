"""Talkatoo% runtime-reachability data (A1).

Ships per-pool-moon and per-region access requirements — resolved to
boolean expressions over ``|Item:count|`` atoms — so the bridge can
compute, AT RUNTIME, which moons are actually reachable given the
player's RECEIVED items, instead of blindly walking the fixed gen-time
``talkatoo_order``.

Why a fixed order isn't enough
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``talkatoo_order.py`` builds its order with a *solo collect model*:
"collecting moon[i] grants the item placed at moon[i], which unlocks
moon[i+1]". That holds in a solo seed. In multiworld it does NOT: the
item at your moon location is usually destined for ANOTHER player, so
collecting your own moons advances other players, not you. Your own
gates — captures like ``|T-Rex|`` and kingdom-entry thresholds like
``{KingdomMoons(Cascade,5)}`` — are satisfied by items you RECEIVE,
whose arrival schedule is set by other players and is unknowable at
generation time.

The symptom (observed 2026-06-10): Talkatoo's cursor window in a
kingdom can be three moons that all need an item you haven't received,
while reachable moons sit just past the window — the player is
artificially blocked even though AP's fill never imposed that
restriction. See docs/TALKATOO.md "Runtime reachability".

The fix: ship the *requirements* (not just an order) and let the bridge
evaluate them against received items every time the item set or the
checked set changes. Talkatoo then names only moons reachable now, and
its window is empty ONLY when the slot genuinely has no reachable
non-progression moon — exactly the case where the player should be
waiting on AP anyway.

Data shape
~~~~~~~~~~
``slot_data["talkatoo_requirements"]`` ::

    {
      "regions": { "<Region>": {"requires": "<expr>", "to": [<conn>...],
                                "start": <bool>} },
      "moons":   { "<Kingdom: Shine>": {"region": "<Region>",
                                        "requires": "<expr>"} },
    }

Each ``<expr>`` is the area's ``requires`` string with every
``{Func(args)}`` already resolved (options baked in for this seed),
leaving only ``|Item:count|`` atoms, the words AND/OR, parentheses, and
the literals ``0``/``1``. The bridge (client/reachability.py) finishes
the job: replace each atom with 0/1 from received-item counts, then
boolean-evaluate. That mirrors the gen-time evaluator in ``Rules.py``
(``checkRequireStringForArea``) minus the ``{Func}`` pass, so runtime
reachability matches AP's own logic graph.

Only NON-progression pool moons are shipped (same set as
``talkatoo_order``) — progression moons bypass the Talkatoo block on the
Switch and never go through the cursor window.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from .talkatoo_order import _split_kingdom_prefix

if TYPE_CHECKING:  # pragma: no cover
    from BaseClasses import MultiWorld
    from . import SMOWorld

log = logging.getLogger(__name__)

# Mirror of the {Func(args)} pattern in Rules.checkRequireStringForArea.
_FUNC_RE = re.compile(r"\{(\w+)\(([^)]*)\)\}")


class TalkatooRequirementsError(Exception):
    """Raised when a ``requires`` string references a function the
    resolver can't find. Indicates the apworld's rule surface drifted
    from this resolver — a developer error, surfaced loudly so it can't
    silently ship an unevaluatable expression to the bridge."""


def _resolve_funcs(world: "SMOWorld", multiworld: "MultiWorld", state: Any,
                   player: int, raw: Any) -> str:
    """Run ONLY the ``{Func(args)}`` resolution pass over a ``requires``
    value, leaving ``|Item:count|`` atoms + AND/OR/parens intact.

    Mirrors Rules.checkRequireStringForArea's func pass (the part that
    dispatches ``{Func}`` to ``Rules``/``hooks.Rules`` and splices the
    bool/str result back in), but STOPS before the ``|Item|`` evaluation
    pass so the atoms survive for the bridge to evaluate against received
    items. Iterates to a fixpoint in case a function returns a string
    that itself contains a ``{Func}`` (none currently do — cheap
    insurance against future rule nesting).

    Empty / list-form requires (regions.json uses ``[]``; some areas use
    ``""``) collapse to ``""`` = always satisfied.
    """
    from . import Rules as base_rules
    from .hooks import Rules as hook_rules

    if isinstance(raw, list):
        # SMO only ever uses the empty list as list-form requires. Treat
        # any list as "no constraint" rather than guessing AND/OR joins.
        return ""
    s = str(raw or "").strip()
    if not s:
        return ""

    for _ in range(16):  # fixpoint; SMO resolves in one pass today
        matches = _FUNC_RE.findall(s)
        if not matches:
            break
        for func_name, arg_str in matches:
            args = arg_str.split(",")
            if args == [""]:
                args = []
            # Dispatch order matches Rules.set_rules: base module globals
            # first (YamlDisabled, ItemValue, ...), then hooks.Rules
            # (SandPeace, KingdomMoons, RegionalCap, OptOne, ...).
            func = getattr(base_rules, func_name, None)
            if func is None or not callable(func):
                func = getattr(hook_rules, func_name, None)
            if not callable(func):
                raise TalkatooRequirementsError(
                    f"talkatoo_requirements: unknown requires function "
                    f"{func_name!r} (in {raw!r})"
                )
            result = func(world, multiworld, state, player, *args)
            token = "{" + func_name + "(" + arg_str + ")}"
            if isinstance(result, bool):
                s = s.replace(token, "1" if result else "0")
            else:
                s = s.replace(token, str(result))
    return s.strip()


def build_talkatoo_requirements(
    world: "SMOWorld",
    multiworld: "MultiWorld",
    player: int,
    progression_names: set[str],
    region_table: dict[str, dict],
    location_table: list[dict],
) -> dict[str, dict]:
    """Build ``slot_data["talkatoo_requirements"]`` (see module docstring).

    ``region_table`` is data/regions.json (name -> spec).
    ``location_table`` is data/locations.json (list of loc specs).
    ``progression_names`` is the same set passed to ``build_talkatoo_order``
    so the moon set matches the cursor-window set exactly.
    """
    from BaseClasses import CollectionState
    state = CollectionState(multiworld)

    regions: dict[str, dict] = {}
    for name, spec in region_table.items():
        regions[name] = {
            "requires": _resolve_funcs(
                world, multiworld, state, player, spec.get("requires", "")),
            "to": list(spec.get("connects_to", []) or []),
            "start": bool(spec.get("starting", False)),
        }

    # Region + raw-requires per location name, from the static table.
    loc_spec_by_name = {loc["name"]: loc for loc in location_table}

    moons: dict[str, dict] = {}
    for loc in multiworld.get_locations(player):
        name = loc.name
        if name in progression_names:
            continue
        if loc.item is not None and loc.item.name == "__Victory__":
            continue
        split = _split_kingdom_prefix(name)
        if split is None or split[0] == "Capture":
            continue
        spec = loc_spec_by_name.get(name)
        if spec is None:
            # Location present in the multiworld but not the static table
            # (shouldn't happen for pool moons). Skip rather than ship a
            # half-specified entry the bridge would mis-evaluate.
            continue
        moons[name] = {
            "region": spec.get("region", ""),
            "requires": _resolve_funcs(
                world, multiworld, state, player, spec.get("requires", "")),
        }

    log.info(
        "[talkatoo-req] shipped requirements for %d moons across %d regions",
        len(moons), len(regions),
    )
    return {"regions": regions, "moons": moons}
