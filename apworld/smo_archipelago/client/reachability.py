"""Runtime reachability evaluation for Talkatoo% (A1).

Evaluates the resolved ``requires`` expressions shipped in
``slot_data["talkatoo_requirements"]`` against the player's RECEIVED-item
counts, so the bridge's Talkatoo cursor window only ever names moons the
player can collect *now*.

This is the runtime half of the split described in
``apworld/smo_archipelago/talkatoo_requirements.py``. The apworld already
resolved every ``{Func(args)}`` at generation time, so each expression
here contains only ``|Item:count|`` atoms, the words AND/OR, parentheses,
and the literals ``0``/``1``. We finish the evaluation exactly as the
apworld's ``Rules.checkRequireStringForArea`` does — replace each atom
with ``1``/``0`` from owned-item counts, fold AND/OR to ``&``/``|``, then
shunting-yard to postfix and evaluate — so runtime reachability matches
AP's own logic graph.

Pure module: no Kivy, no AP, no client imports. Unit-testable directly
with synthetic ``owned_counts``. Keeping it dependency-free also means
``SMOContext`` can import it without dragging anything heavy in.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Mapping

_ATOM_RE = re.compile(r"\|[^|]+\|")
_AND_RE = re.compile(r"\s?\bAND\b\s?", re.IGNORECASE)
_OR_RE = re.compile(r"\s?\bOR\b\s?", re.IGNORECASE)


def owned_counts_from_item_names(names: Iterable[str]) -> dict[str, int]:
    """Tally received item NAMES into ``{name: count}``.

    The atoms in shipped expressions are keyed by exact AP item name —
    captures (``|T-Rex|``) and kingdom moons (``|Sand Kingdom Power
    Moon:13|``) — so a plain name tally is all the evaluator needs.
    Power Moon and Multi-Moon are distinct item names, which is exactly
    what ``KingdomMoons``' OR-chain expects (it enumerates MM/PM splits).
    """
    return dict(Counter(names))


def _infix_to_postfix(expr: str) -> str:
    """Shunting-yard over a string of ``0`` ``1`` ``&`` ``|`` ``!`` ``(``
    ``)``. Port of ``Rules.infix_to_postfix`` (same precedence). Any other
    character is ignored — callers strip whitespace and resolve atoms to
    ``0``/``1`` first, so only operators and literals remain."""
    prec = {"&": 2, "|": 2, "!": 3}
    stack: list[str] = []
    out: list[str] = []
    for c in expr:
        if c in "01":
            out.append(c)
        elif c in prec:
            while stack and stack[-1] != "(" and prec[c] <= prec[stack[-1]]:
                out.append(stack.pop())
            stack.append(c)
        elif c == "(":
            stack.append(c)
        elif c == ")":
            while stack and stack[-1] != "(":
                out.append(stack.pop())
            if stack:
                stack.pop()
    while stack:
        out.append(stack.pop())
    return "".join(out)


def _eval_postfix(expr: str) -> bool:
    """Evaluate a postfix boolean string. Port of
    ``Rules.evaluate_postfix``. Empty/degenerate input is treated as
    True (no constraint)."""
    stack: list[bool] = []
    for c in expr:
        if c == "0":
            stack.append(False)
        elif c == "1":
            stack.append(True)
        elif c == "&":
            b = stack.pop()
            a = stack.pop()
            stack.append(a and b)
        elif c == "|":
            b = stack.pop()
            a = stack.pop()
            stack.append(a or b)
        elif c == "!":
            stack.append(not stack.pop())
    return stack.pop() if stack else True


def eval_requires(expr: str | None, owned_counts: Mapping[str, int]) -> bool:
    """True iff the gen-resolved ``requires`` expression is satisfied by
    ``owned_counts`` (item name -> received count).

    Empty / ``"1"`` => always reachable; ``"0"`` => never. ``|@Category|``
    atoms are unused in SMO moon/region requires; if one ever appears we
    treat it as unsatisfiable (``0``) rather than silently passing, so a
    rule-surface drift surfaces as over-blocking (visible) instead of
    under-blocking (a false unblock that re-opens the original bug).
    """
    if expr is None:
        return True
    s = expr.strip()
    if s == "" or s == "1":
        return True
    if s == "0":
        return False

    for atom in _ATOM_RE.findall(s):
        inner = atom[1:-1].strip()  # drop the surrounding pipes
        if inner.startswith("@"):
            s = s.replace(atom, "0")
            continue
        parts = inner.split(":")
        name = parts[0].strip()
        need = 1
        if len(parts) > 1:
            try:
                need = int(parts[1].strip())
            except ValueError:
                need = 1
        have = owned_counts.get(name, 0)
        s = s.replace(atom, "1" if have >= need else "0")

    s = _AND_RE.sub("&", s)
    s = _OR_RE.sub("|", s)
    s = s.replace(" ", "")
    return _eval_postfix(_infix_to_postfix(s))


class TalkatooReachability:
    """Evaluates moon reachability from ``slot_data["talkatoo_requirements"]``.

    A moon is reachable iff its region is reachable (BFS over the region
    graph, each edge gated by the target region's resolved ``requires``)
    AND the moon's own resolved ``requires`` is satisfied. Both are
    evaluated against the same ``owned_counts`` snapshot.

    When the slot shipped no requirements (older apworld build), construct
    with empty dicts: ``is_moon_reachable`` then returns True for every
    moon, so the bridge degrades to the pre-A1 "walk the order" behavior.
    """

    def __init__(self, regions: Mapping[str, dict] | None,
                 moons: Mapping[str, dict] | None):
        self._regions: dict[str, dict] = dict(regions or {})
        self._moons: dict[str, dict] = dict(moons or {})

    @classmethod
    def from_slot_data(cls, payload: Mapping | None) -> "TalkatooReachability":
        payload = payload or {}
        return cls(payload.get("regions"), payload.get("moons"))

    @property
    def has_data(self) -> bool:
        return bool(self._moons)

    def reachable_regions(self, owned_counts: Mapping[str, int]) -> set[str]:
        """Set of region names reachable from the start region(s) given
        ``owned_counts``. Fixpoint over the region graph: a region is
        reachable if it's a start (and its own requires hold) or a
        reachable predecessor connects to it (and its own requires hold).
        """
        regions = self._regions
        if not regions:
            return set()

        def req_ok(name: str) -> bool:
            return eval_requires(regions[name].get("requires", ""), owned_counts)

        reach = {
            name for name, spec in regions.items()
            if spec.get("start") and req_ok(name)
        }
        changed = True
        while changed:
            changed = False
            # Iterate over a snapshot of currently-reachable regions and
            # relax outgoing edges. Monotonic — owned_counts is fixed for
            # the call, so this converges in <= |regions| passes.
            for src in list(reach):
                for dst in regions.get(src, {}).get("to", []):
                    if dst in regions and dst not in reach and req_ok(dst):
                        reach.add(dst)
                        changed = True
        return reach

    def is_moon_reachable(self, loc_name: str, owned_counts: Mapping[str, int],
                          reachable_regions: set[str] | None = None) -> bool:
        """True iff the pool moon ``loc_name`` ("Kingdom: Shine") is
        reachable now. Unknown moons (not in the shipped map) return True
        — never over-block on missing data.

        Pass ``reachable_regions`` (from a single ``reachable_regions``
        call) to amortize the BFS across a whole window build.
        """
        spec = self._moons.get(loc_name)
        if spec is None:
            return True
        region = spec.get("region", "")
        if region and self._regions:
            rr = (reachable_regions if reachable_regions is not None
                  else self.reachable_regions(owned_counts))
            if region not in rr:
                return False
        return eval_requires(spec.get("requires", ""), owned_counts)
