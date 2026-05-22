"""Consistency check: per-kingdom moon-count Range options vs items.json + KINGDOM_MOON_GATES.

The Cascade/Sand/.../Bowser's MoonCount Range classes in hooks/Options.py
define the user-facing min / max / default for capping each kingdom's Moon
item count in the pool. Those numbers must stay in step with three sources:
  * items.json — the per-kingdom (Power Moon + Multi-Moon) item count drives
    `range_end` and `default`.
  * hooks/World.py::KINGDOM_MOON_GATES — the threshold a kingdom must clear,
    feeding the MM-greedy `range_start` floor (`mms_kept + pms_kept` where
    MMs are preferred for their 3x effective weight).
  * hooks/World.py::KINGDOM_MOON_COUNT_OPTIONS — the wiring table the trim
    hook iterates; each entry must point at an option registered in
    hooks/Options.py::before_options_defined and at a kingdom in
    KINGDOM_MOON_GATES.

Pure-data: text-scrapes both hooks files + parses items.json (no
Archipelago imports), mirroring the approach in test_kingdom_gates.py so
the test runs in the standard test job (not gated on SMOAP_LIVE_AP).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

APWORLD_ROOT = Path(__file__).resolve().parents[1]


def _moon_item_counts() -> dict[str, tuple[int, int]]:
    """{kingdom: (pm_count, mm_count)} from items.json."""
    items = json.loads(
        (APWORLD_ROOT / "data" / "items.json").read_text(encoding="utf-8"),
    )
    pm_suffix = " Kingdom Power Moon"
    mm_suffix = " Kingdom Multi-Moon"
    pm: dict[str, int] = {}
    mm: dict[str, int] = {}
    for it in items:
        name = it.get("name", "")
        count = int(it.get("count", 1))
        if name.endswith(pm_suffix):
            pm[name[: -len(pm_suffix)]] = count
        elif name.endswith(mm_suffix):
            mm[name[: -len(mm_suffix)]] = count
    kingdoms = set(pm) | set(mm)
    return {k: (pm.get(k, 0), mm.get(k, 0)) for k in kingdoms}


def _mm_greedy_floor(threshold: int, mm_count: int) -> int:
    """Floor = the smallest item count whose MM-greedy partial sum reaches
    `threshold` effective moons. Each MM is worth 3 effective; each PM is 1."""
    mms_kept = min(mm_count, threshold // 3)
    pms_kept = max(0, threshold - 3 * mms_kept)
    return mms_kept + pms_kept


_TABLE_BODY_RE = re.compile(
    r'KINGDOM_MOON_(?:GATES|COUNT_OPTIONS)\s*=\s*\{([^}]*)\}', re.DOTALL,
)


def _str_dict_from_world(name: str) -> dict[str, str]:
    src = (APWORLD_ROOT / "hooks" / "World.py").read_text(encoding="utf-8")
    m = re.search(rf'{name}\s*=\s*\{{([^}}]*)\}}', src, re.DOTALL)
    assert m, f"{name} dict not found in hooks/World.py"
    return dict(re.findall(r'"([^"]+)":\s*"([^"]*)"', m.group(1)))


def _int_dict_from_world(name: str) -> dict[str, int]:
    src = (APWORLD_ROOT / "hooks" / "World.py").read_text(encoding="utf-8")
    m = re.search(rf'{name}\s*=\s*\{{([^}}]*)\}}', src, re.DOTALL)
    assert m, f"{name} dict not found in hooks/World.py"
    return {k: int(v) for k, v in re.findall(r'"([^"]+)":\s*(\d+)', m.group(1))}


def _option_class_attrs() -> dict[str, dict[str, int]]:
    """Scrape each Range subclass in hooks/Options.py whose name ends
    `MoonCount`, returning {class_name_in_lower_snake_no_suffix: {range_start, range_end, default}}.

    Indexed by option key (registered name in before_options_defined), not
    class name, so the assertions can cross-reference directly.
    """
    src = (APWORLD_ROOT / "hooks" / "Options.py").read_text(encoding="utf-8")

    # First read the registration block to map class -> option key.
    reg_pat = re.compile(r'options\["([a-z_]+_moon_count)"\]\s*=\s*([A-Za-z_]+)')
    key_by_class = {cls: key for key, cls in reg_pat.findall(src)}

    # Then read each class's three attribute values.
    cls_pat = re.compile(
        r'class\s+([A-Za-z_]+MoonCount)\s*\(\s*Range\s*\)\s*:\s*'
        r'(?:"""(?:.|\n)*?""")?'  # optional docstring
        r'((?:\s*[a-z_]+\s*=\s*[^\n]+\n)+)',
        re.MULTILINE,
    )
    out: dict[str, dict[str, int]] = {}
    for cls_name, body in cls_pat.findall(src):
        if cls_name not in key_by_class:
            continue
        attrs: dict[str, int] = {}
        for attr in ("range_start", "range_end", "default"):
            m = re.search(rf'^\s*{attr}\s*=\s*(\d+)\s*$', body, re.MULTILINE)
            assert m, f"{cls_name} missing {attr} attribute"
            attrs[attr] = int(m.group(1))
        out[key_by_class[cls_name]] = attrs
    return out


def _registered_option_keys() -> set[str]:
    src = (APWORLD_ROOT / "hooks" / "Options.py").read_text(encoding="utf-8")
    return set(re.findall(r'options\["([^"]+)"\]\s*=\s*[A-Za-z_]+', src))


def test_moon_count_options_keys_match_gates():
    """Every gated kingdom has a wired moon-count option, and vice versa."""
    gates = _int_dict_from_world("KINGDOM_MOON_GATES")
    wiring = _str_dict_from_world("KINGDOM_MOON_COUNT_OPTIONS")
    assert set(gates) == set(wiring), (
        "KINGDOM_MOON_COUNT_OPTIONS / KINGDOM_MOON_GATES key drift\n"
        f"  in gates only:   {sorted(set(gates) - set(wiring))}\n"
        f"  in options only: {sorted(set(wiring) - set(gates))}"
    )


def test_moon_count_options_are_registered():
    """Each option key referenced by KINGDOM_MOON_COUNT_OPTIONS is registered
    in before_options_defined and backed by a Range subclass."""
    wiring = _str_dict_from_world("KINGDOM_MOON_COUNT_OPTIONS")
    registered = _registered_option_keys()
    class_attrs = _option_class_attrs()
    missing_in_reg = [k for k in wiring.values() if k not in registered]
    missing_in_cls = [k for k in wiring.values() if k not in class_attrs]
    assert not missing_in_reg, (
        f"Options wired in KINGDOM_MOON_COUNT_OPTIONS but missing from "
        f"before_options_defined: {missing_in_reg}"
    )
    assert not missing_in_cls, (
        f"Options wired but missing a corresponding Range class: {missing_in_cls}"
    )


def test_moon_count_range_values_match_items_json_and_gates():
    """For each per-kingdom moon-count option, the Range numbers track the
    item-pool size and the MM-greedy gate floor."""
    moon_counts = _moon_item_counts()
    gates = _int_dict_from_world("KINGDOM_MOON_GATES")
    wiring = _str_dict_from_world("KINGDOM_MOON_COUNT_OPTIONS")
    class_attrs = _option_class_attrs()

    mismatches: list[str] = []
    for kingdom, opt_name in wiring.items():
        attrs = class_attrs[opt_name]
        pm_count, mm_count = moon_counts.get(kingdom, (0, 0))
        total = pm_count + mm_count
        floor = _mm_greedy_floor(gates[kingdom], mm_count)

        if attrs["range_end"] != total:
            mismatches.append(
                f"{opt_name}: range_end={attrs['range_end']} but items.json has "
                f"{pm_count} PM + {mm_count} MM = {total}"
            )
        if attrs["default"] != total:
            mismatches.append(
                f"{opt_name}: default={attrs['default']} but expected "
                f"default=range_end={total}"
            )
        if attrs["range_start"] != floor:
            mismatches.append(
                f"{opt_name}: range_start={attrs['range_start']} but MM-greedy "
                f"floor for gate={gates[kingdom]} with mm_count={mm_count} is {floor}"
            )

    assert not mismatches, "Range option drift:\n  " + "\n  ".join(mismatches)
