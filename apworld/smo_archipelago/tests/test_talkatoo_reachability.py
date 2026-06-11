"""Unit tests for the Talkatoo% runtime reachability evaluator (A1).

Pure module under test — `client/reachability.py` has no AP/Kivy deps,
so these run without an Archipelago checkout. They pin the two things
the gen-time `talkatoo_requirements.py` resolver hands off to runtime:

  1. `eval_requires` matches the apworld's own requires-string semantics
     (the |Item:count| + AND/OR/parens passes of Rules.checkRequireString
     ForArea), against received-item counts instead of CollectionState.
  2. `TalkatooReachability` gates a moon on (region reachable) AND (own
     requires) — the exact model AP uses, so Talkatoo never names a moon
     the player can't collect now, and the headline multiworld deadlock
     (T-Rex moons gating the rest of Cascade) no longer blocks progress.
"""

from __future__ import annotations

from client.reachability import (
    TalkatooReachability,
    eval_requires,
    owned_counts_from_item_names,
)


# --- eval_requires: grammar parity with Rules.checkRequireStringForArea ---

def test_empty_and_literals():
    assert eval_requires("", {}) is True
    assert eval_requires(None, {}) is True
    assert eval_requires("1", {}) is True
    assert eval_requires("0", {}) is False


def test_single_capture_atom():
    assert eval_requires("|T-Rex|", {"T-Rex": 1}) is True
    assert eval_requires("|T-Rex|", {}) is False
    # ":1" is the explicit form of a bare atom.
    assert eval_requires("|T-Rex:1|", {"T-Rex": 1}) is True
    assert eval_requires("|T-Rex:1|", {"T-Rex": 0}) is False


def test_count_atom_threshold():
    expr = "|Sand Kingdom Power Moon:13|"
    assert eval_requires(expr, {"Sand Kingdom Power Moon": 13}) is True
    assert eval_requires(expr, {"Sand Kingdom Power Moon": 12}) is False
    assert eval_requires(expr, {"Sand Kingdom Power Moon": 99}) is True


def test_and_or_parens():
    expr = "|Bullet Bill| and |Knucklotec's Fist|"
    assert eval_requires(expr, {"Bullet Bill": 1, "Knucklotec's Fist": 1}) is True
    assert eval_requires(expr, {"Bullet Bill": 1}) is False
    expr2 = "(|Ty-foo| and |Shiverian Racer|) or |Gushen|"
    assert eval_requires(expr2, {"Gushen": 1}) is True
    assert eval_requires(expr2, {"Ty-foo": 1}) is False
    assert eval_requires(expr2, {"Ty-foo": 1, "Shiverian Racer": 1}) is True


def test_case_insensitive_operators():
    # KingdomMoons emits uppercase AND/OR; the Peace rules emit lowercase.
    expr = "|A:1| AND |B:1|"
    assert eval_requires(expr, {"A": 1, "B": 1}) is True
    assert eval_requires("|A:1| or |B:1|", {"B": 1}) is True


def test_kingdom_moons_or_chain():
    # Shape of a resolved KingdomMoons(Sand, 16) gate: PM=16, or 1 MM+13 PM,
    # or 2 MM+10 PM. Power Moon and Multi-Moon are distinct item names.
    expr = ("(|Sand Kingdom Power Moon:16| OR "
            "(|Sand Kingdom Multi-Moon:1| AND |Sand Kingdom Power Moon:13|) OR "
            "(|Sand Kingdom Multi-Moon:2| AND |Sand Kingdom Power Moon:10|))")
    assert eval_requires(expr, {"Sand Kingdom Power Moon": 16}) is True
    assert eval_requires(expr, {"Sand Kingdom Multi-Moon": 1,
                                "Sand Kingdom Power Moon": 13}) is True
    assert eval_requires(expr, {"Sand Kingdom Multi-Moon": 2,
                                "Sand Kingdom Power Moon": 10}) is True
    # 1 MM + 12 PM = 15 effective < 16 — not enough on any clause.
    assert eval_requires(expr, {"Sand Kingdom Multi-Moon": 1,
                                "Sand Kingdom Power Moon": 12}) is False


def test_category_atom_is_conservatively_blocked():
    # SMO moon/region requires never use |@Category|; if one ever appears
    # we block (visible over-restriction) rather than silently passing.
    assert eval_requires("|@Whatever|", {}) is False


def test_owned_counts_tallies_by_name():
    names = ["T-Rex", "Sand Kingdom Power Moon", "Sand Kingdom Power Moon",
             "Sand Kingdom Multi-Moon"]
    counts = owned_counts_from_item_names(names)
    assert counts == {
        "T-Rex": 1,
        "Sand Kingdom Power Moon": 2,
        "Sand Kingdom Multi-Moon": 1,
    }


# --- TalkatooReachability: region BFS + own-requires gating ---

def _model():
    regions = {
        "Cascade Kingdom": {"requires": "", "to": ["Sand Kingdom"], "start": True},
        "Sand Kingdom": {"requires": "|Cascade Kingdom Power Moon:5|",
                         "to": ["Cap Kingdom", "Lake Kingdom"], "start": False},
        "Cap Kingdom": {"requires": "", "to": [], "start": False},
        "Lake Kingdom": {"requires": "|Sand Kingdom Power Moon:16|",
                         "to": [], "start": False},
    }
    moons = {
        "Cascade: With a T-Rex": {"region": "Cascade Kingdom", "requires": "|T-Rex:1|"},
        "Cascade: An Easy One": {"region": "Cascade Kingdom", "requires": ""},
        "Cap: Bonneter Coin": {"region": "Cap Kingdom", "requires": "|Paragoomba:1|"},
    }
    return TalkatooReachability(regions, moons)


def test_reachable_regions_gated_by_received_moons():
    m = _model()
    assert m.reachable_regions({}) == {"Cascade Kingdom"}
    # 5 effective Cascade moons unlock Sand (and Cap, an ungated side-branch
    # off Sand). Lake still needs 16 Sand moons.
    five = {"Cascade Kingdom Power Moon": 5}
    assert m.reachable_regions(five) == {"Cascade Kingdom", "Sand Kingdom", "Cap Kingdom"}
    full = {"Cascade Kingdom Power Moon": 5, "Sand Kingdom Power Moon": 16}
    assert "Lake Kingdom" in m.reachable_regions(full)


def test_moon_blocked_by_own_requires_but_others_reachable():
    """The headline scenario: a Cascade moon needs T-Rex (an item from
    another player's world). Until it arrives, that moon is unreachable —
    but the non-T-Rex Cascade moon stays reachable, so the player is never
    stuck. AP's set-reachability is preserved; the serialization gate is
    gone."""
    m = _model()
    assert m.is_moon_reachable("Cascade: With a T-Rex", {}) is False
    assert m.is_moon_reachable("Cascade: An Easy One", {}) is True
    # T-Rex received -> the gated moon opens up.
    assert m.is_moon_reachable("Cascade: With a T-Rex", {"T-Rex": 1}) is True


def test_moon_blocked_by_unreachable_region():
    m = _model()
    # Cap is only reachable once Sand is (5 Cascade moons). Even with the
    # capture in hand, the region gate keeps the moon out.
    assert m.is_moon_reachable("Cap: Bonneter Coin", {"Paragoomba": 1}) is False
    owned = {"Cascade Kingdom Power Moon": 5, "Paragoomba": 1}
    assert m.is_moon_reachable("Cap: Bonneter Coin", owned) is True


def test_unknown_moon_and_empty_model_never_overblock():
    m = _model()
    assert m.is_moon_reachable("Cascade: Not In Map", {}) is True
    empty = TalkatooReachability(None, None)
    assert empty.has_data is False
    assert empty.is_moon_reachable("anything", {}) is True
