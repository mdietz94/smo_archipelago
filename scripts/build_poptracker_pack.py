"""Generate the SMO Archipelago PopTracker pack from the apworld data.

Reads:
  apworld/smo_archipelago/data/{items,locations,regions,categories}.json
  apworld/smo_archipelago/hooks/Options.py    (for option defaults + display names)
  poptracker/pack-src/                        (hand-authored skeleton)

Emits:
  poptracker/build/smo-poptracker/            (assembled pack dir)
  poptracker/build/smo-poptracker-v<ver>.zip  (when --zip is passed)

Same constraints as scripts/sync_capture_table.py: single-file, stdlib-only,
re-runnable after any apworld change to keep the tracker in sync.

Usage:
    python scripts/build_poptracker_pack.py            # build only
    python scripts/build_poptracker_pack.py --zip      # also produce the zip
    python scripts/build_poptracker_pack.py --version 1.0.0
    python scripts/build_poptracker_pack.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import zipfile
import zlib
from pathlib import Path
from typing import Any


# ---------- AP id allocation (mirrors apworld/.../Game.py + Items.py + Locations.py)

def starting_index(game: str, player: str) -> int:
    """Replica of Game.py's algorithm so the pack's id tables match the
    real AP server's ids without importing the apworld."""
    s = (ord(game[0]) * 100_000_000
         + ord(game[1]) * 70_000_000
         + ord(game[-1]) * 10_000_000)
    if len(game) > 3:
        for i in range(2, len(game) - 1):
            s += ord(game[i]) * 100_000
    for ch in player:
        s += ord(ch) * 1_000
    return s


def allocate_item_ids(items: list[dict], filler_name: str, start: int) -> list[tuple[int, dict]]:
    """Replica of Items.py: sequential ids starting at `start`, filler appended first."""
    out = list(items)
    out.append({"name": filler_name})  # filler item gets the next id
    return [(start + i, it) for i, it in enumerate(out)]


def allocate_location_ids(locations: list[dict], start: int) -> list[tuple[int, dict]]:
    """Replica of Locations.py: sequential ids starting at start+500.
    If no victory location is defined, a synthetic "__Game Complete__"
    is appended. SMO has explicit victory locations so this branch is unused
    in practice, but matched here for fidelity."""
    out = list(locations)
    has_victory = any(l.get("victory") for l in out)
    if not has_victory:
        out.append({"name": "__Game Complete__", "region": "SMO", "requires": []})
    return [(start + 500 + i, l) for i, l in enumerate(out)]


# ---------- naming

# Names of items that actually exist in the item pool (items.json). Populated
# at the start of build(). Used to mirror the apworld's OptOne/OptAll clamp
# semantics: a `requires` reference to a name that is NOT a pool item (e.g. base
# moves like "Ground Pound"/"Wall Jump"/"Swim", or excluded captures like "Jaxi")
# clamps to |Name:0| in the apworld -> trivially true. Without this set the
# translation would emit a reference to a nonexistent tracker code, which is
# permanently false and wrongly holds those locations out of logic whenever
# capturesanity is ON.
POOL_ITEM_NAMES: set[str] = set()

_SAFE_CODE_RE = re.compile(r"[^a-z0-9]+")


def code_for(name: str) -> str:
    """PopTracker item/location codes: lowercase, alnum + underscore.
    Stable transform so generated mapping tables always agree."""
    return _SAFE_CODE_RE.sub("_", name.lower()).strip("_")


def section_for(region: str, name: str) -> str:
    """Section reference for LOCATION_MAPPING: @<Kingdom>/<Moon-name-section>.
    PopTracker section refs follow @<top-level-location>/<section-name>; we
    use the kingdom as the top-level location and each moon's name as a
    section directly (no nested children — that one extra level of nesting
    is not supported by PopTracker's location format)."""
    return f"@{region}/{name}"


# ---------- requires-string parser (apworld mini-language → AST → PopTracker)

class RequireParseError(Exception):
    pass


# AST shapes:
#   ("true",)
#   ("false",)
#   ("item", name, count)          -- |Name| or |Name:N|; count defaults to 1
#   ("func", name, [args...])      -- {Name(args...)}
#   ("and", [children])
#   ("or",  [children])


def tokenize(s: str) -> list[tuple[str, str]]:
    """Token kinds: LPAREN RPAREN LBRACE RBRACE PIPE COMMA AND OR IDENT STRING.
    The mini-language is space-tolerant and case-insensitive on AND/OR."""
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            tokens.append(("LPAREN", "("))
            i += 1
        elif c == ")":
            tokens.append(("RPAREN", ")"))
            i += 1
        elif c == "{":
            tokens.append(("LBRACE", "{"))
            i += 1
        elif c == "}":
            tokens.append(("RBRACE", "}"))
            i += 1
        elif c == ",":
            tokens.append(("COMMA", ","))
            i += 1
        elif c == "|":
            # item ref: |Name| or |Name:N|; read up to the closing pipe
            j = s.find("|", i + 1)
            if j < 0:
                raise RequireParseError(f"unclosed | in {s!r}")
            tokens.append(("ITEM", s[i + 1:j]))
            i = j + 1
        else:
            # identifier or keyword: run of non-special chars
            j = i
            while j < len(s) and s[j] not in "(){},|" and not s[j].isspace():
                j += 1
            word = s[i:j]
            lw = word.lower()
            if lw == "and":
                tokens.append(("AND", word))
            elif lw == "or":
                tokens.append(("OR", word))
            else:
                tokens.append(("IDENT", word))
            i = j
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.toks = tokens
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, kind: str) -> tuple[str, str]:
        t = self.peek()
        if not t or t[0] != kind:
            raise RequireParseError(f"expected {kind} at token {self.i}, got {t}")
        self.i += 1
        return t

    def parse(self) -> tuple:
        node = self.parse_or()
        if self.peek() is not None:
            raise RequireParseError(f"trailing tokens at {self.i}: {self.toks[self.i:]}")
        return node

    def parse_or(self) -> tuple:
        left = self.parse_and()
        children = [left]
        while self.peek() and self.peek()[0] == "OR":
            self.take("OR")
            children.append(self.parse_and())
        return ("or", children) if len(children) > 1 else left

    def parse_and(self) -> tuple:
        left = self.parse_atom()
        children = [left]
        while self.peek() and self.peek()[0] == "AND":
            self.take("AND")
            children.append(self.parse_atom())
        return ("and", children) if len(children) > 1 else left

    def parse_atom(self) -> tuple:
        t = self.peek()
        if t is None:
            raise RequireParseError("unexpected end of input")
        if t[0] == "LPAREN":
            self.take("LPAREN")
            node = self.parse_or()
            self.take("RPAREN")
            return node
        if t[0] == "LBRACE":
            return self.parse_func()
        if t[0] == "ITEM":
            self.take("ITEM")
            body = t[1]
            if ":" in body:
                name, count = body.split(":", 1)
                count = count.strip()
                try:
                    n = int(count)
                except ValueError:
                    raise RequireParseError(f"bad count in |{body}|")
                return ("item", name.strip(), n)
            return ("item", body.strip(), 1)
        raise RequireParseError(f"unexpected token {t}")

    def parse_func(self) -> tuple:
        self.take("LBRACE")
        name_tok = self.take("IDENT")
        # Function call: {Name(arg, arg, ...)}; args are raw strings up to , or )
        # except for nested {} or || which are extracted with the existing tokenizer.
        # For SMO's usage the only callers pass either no args, or a bare ident +
        # an int, or a colon-form string like "Coins:12" — we capture the raw
        # comma-separated tail until the matching ) and store each arg as a string.
        self.take("LPAREN")
        args: list[str] = []
        cur = ""
        depth = 0
        while True:
            t = self.peek()
            if t is None:
                raise RequireParseError("unclosed {}-function")
            if t[0] == "RPAREN" and depth == 0:
                self.take("RPAREN")
                if cur.strip():
                    args.append(cur.strip())
                break
            if t[0] == "COMMA" and depth == 0:
                self.take("COMMA")
                args.append(cur.strip())
                cur = ""
                continue
            if t[0] == "LPAREN":
                depth += 1
            elif t[0] == "RPAREN":
                depth -= 1
            # accumulate the source form. The tokenizer drops whitespace, so a
            # multi-word bare arg like `Bullet Bill` or `Ground Pound` arrives as
            # two adjacent IDENT tokens — re-insert the separating space or they
            # fuse into `BulletBill`/`GroundPound` (wrong item code, and the
            # pool-membership check below would never match). Piped args
            # (|Bowser Statue|) keep their spaces via the ITEM token already.
            if t[0] == "IDENT" and cur and not cur.endswith((" ", "(", ",", "|")):
                cur += " "
            cur += _emit_token(t)
            self.i += 1
        self.take("RBRACE")
        return ("func", name_tok[1], args)


def _emit_token(t: tuple[str, str]) -> str:
    kind, val = t
    if kind == "ITEM":
        return f"|{val}|"
    if kind == "LPAREN":
        return "("
    if kind == "RPAREN":
        return ")"
    if kind == "AND":
        return " and "
    if kind == "OR":
        return " or "
    if kind == "COMMA":
        return ","
    if kind in ("IDENT",):
        return val
    return val


def parse_requires(s: str) -> tuple:
    """Top-level entry. Empty / [] → ('true',)."""
    if s is None:
        return ("true",)
    if isinstance(s, list):
        if not s:
            return ("true",)
        # The apworld allows the requires to be a list of strings joined with AND
        children = [parse_requires(item) for item in s]
        return children[0] if len(children) == 1 else ("and", children)
    s = s.strip()
    if not s:
        return ("true",)
    tokens = tokenize(s)
    if not tokens:
        return ("true",)
    return _Parser(tokens).parse()


# ---------- AST → PopTracker access_rules (OR-of-AND)

def to_dnf(ast: tuple) -> list[list[str]]:
    """Convert AST to PopTracker access_rules (OR-of-AND list-of-lists).
    Each inner list is a single AND clause; outer list is the OR.
    Atomic items in the inner list are PopTracker rule-strings (codes or $func|args)."""
    if ast[0] == "true":
        return [[]]  # one always-true clause
    if ast[0] == "false":
        return []   # no satisfying clauses
    if ast[0] == "item":
        _, name, n = ast
        rule = _item_rule(name, n)
        return [[rule]] if rule else [[]]
    if ast[0] == "func":
        _, name, args = ast
        return _func_to_dnf(name, args)
    if ast[0] == "and":
        # cartesian-product the children's DNFs
        result: list[list[str]] = [[]]
        for child in ast[1]:
            child_dnf = to_dnf(child)
            new_result: list[list[str]] = []
            for left in result:
                for right in child_dnf:
                    merged = list(left)
                    for term in right:
                        if term not in merged:
                            merged.append(term)
                    new_result.append(merged)
            result = new_result
            if not result:
                return []  # AND with false branch
        return result
    if ast[0] == "or":
        result = []
        for child in ast[1]:
            for clause in to_dnf(child):
                if clause not in result:
                    result.append(clause)
        return result
    raise ValueError(f"unknown AST node {ast}")


def _item_rule(name: str, count: int) -> str | None:
    """A reference to an apworld item (always |Name| or |Name:N|).
    n=1 → just the code; n>1 → $has_count helper."""
    # A reference to a name that never enters the pool (base move / excluded
    # capture) clamps to |Name:0| in the apworld → trivially true. Mirror that.
    if POOL_ITEM_NAMES and name not in POOL_ITEM_NAMES:
        return None
    c = code_for(name)
    if count <= 0:
        return None  # |Name:0| is trivially true → empty clause
    if count == 1:
        return c
    return f"$has_count|{c}|{count}"


def _func_to_dnf(name: str, args: list[str]) -> list[list[str]]:
    """Translate a {FunctionName(args)} call.

    Some functions are inlined here because they have option-conditional
    semantics that PopTracker's access_rules engine can't express without
    Lua helpers. Each helper lives in pack-src/scripts/logic.lua.
    """
    # Wrappers that degrade to true when capturesanity is off.
    if name == "OptOne":
        # args: ["Item Name"] (pipes/count optional). When capturesanity off →
        # true; on → has(item). But a name that never enters the pool (base move
        # like "Ground Pound"/"Wall Jump"/"Swim", or an excluded capture like
        # "Jaxi") clamps to |Name:0| in the apworld → trivially true regardless
        # of capturesanity. Emitting has(<nonexistent code>) here would be
        # permanently false and wrongly hold the location out of logic.
        if not args:
            return [[]]
        item_name = args[0].strip().strip("|@$").split(":")[0]
        if POOL_ITEM_NAMES and item_name not in POOL_ITEM_NAMES:
            return [[]]
        item_code = code_for(item_name)
        return [["$capturesanity_off"], [item_code]]
    if name == "OptAll":
        # args: ["|X| and |Y|"]. Parse the inner expression and OR with capturesanity_off.
        if not args:
            return [[]]
        inner_ast = parse_requires(args[0])
        inner_dnf = to_dnf(inner_ast)
        result = [["$capturesanity_off"]]
        for clause in inner_dnf:
            if clause not in result:
                result.append(clause)
        return result
    if name == "YamlDisabled":
        # true when the named yaml option is OFF
        if not args:
            return [[]]
        return [[f"$is_opt_off|{args[0].strip()}"]]
    if name == "YamlEnabled":
        if not args:
            return [[]]
        return [[f"$is_opt|{args[0].strip()}"]]
    if name == "KingdomMoons":
        # args: ["KingdomName", "N"]
        if len(args) != 2:
            raise ValueError(f"KingdomMoons expects 2 args, got {args}")
        return [[f"$has_kingdom_moons|{args[0].strip()}|{args[1].strip()}"]]
    if name == "ItemValue":
        # args: ["coins:12"] — defer to Lua. Unused in current data but
        # documented in Rules.py; harmless to wire up.
        if not args:
            return [[]]
        return [[f"$item_value|{args[0].strip()}"]]
    # Everything else: invoke a Lua function with the same snake_case name.
    # Most of these are bare rule functions in Rules.py with no args.
    fn_code = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    if args:
        return [[f"${fn_code}|" + "|".join(a.strip() for a in args)]]
    return [[f"${fn_code}"]]


def dnf_to_access_rules(dnf: list[list[str]]) -> list[str]:
    """Convert OR-of-AND list-of-lists to PopTracker access_rules format
    (list of comma-separated AND strings)."""
    if not dnf:
        return ["false"]  # unsatisfiable; PopTracker has no built-in 'false',
                           # but a missing item code evaluates as false anyway
    rules: list[str] = []
    seen = set()
    for clause in dnf:
        if not clause:
            return []  # one always-true clause → omit access_rules entirely
        key = ",".join(sorted(clause))
        if key in seen:
            continue
        seen.add(key)
        rules.append(",".join(clause))
    return rules


# ---------- region chain → per-location prerequisite

def build_region_prereqs(regions: dict[str, dict]) -> dict[str, list[str]]:
    """For each region, return the DNF of access_rules to enter it.
    Walks the inbound edges (from connects_to) to chain prerequisites.

    Each region's effective access = AND(parent's access, this region's requires).
    Multiple inbound edges → OR over each parent path. For SMO the graph
    is mostly linear, so the worst case stays manageable.
    """
    # Build inbound edges from connects_to
    inbound: dict[str, list[str]] = {name: [] for name in regions}
    for name, info in regions.items():
        for tgt in info.get("connects_to", []):
            if tgt in inbound:
                inbound[tgt].append(name)
    # Memoized DNF computation
    cache: dict[str, list[list[str]]] = {}
    on_stack: set[str] = set()

    def compute(region: str) -> list[list[str]]:
        if region in cache:
            return cache[region]
        if region in on_stack:
            # cycle (shouldn't happen but be defensive): treat as always-true to avoid lockout
            return [[]]
        on_stack.add(region)
        info = regions.get(region, {})
        own_dnf = to_dnf(parse_requires(info.get("requires", "")))
        parents = inbound.get(region, [])
        starting = info.get("starting", False) or not parents
        if starting:
            result = own_dnf
        else:
            # OR over parents' access, then AND with own requires
            parent_dnf: list[list[str]] = []
            for p in parents:
                for clause in compute(p):
                    if clause not in parent_dnf:
                        parent_dnf.append(clause)
            # AND parent_dnf with own_dnf
            result = []
            for pc in parent_dnf:
                for oc in own_dnf:
                    merged = list(pc)
                    for term in oc:
                        if term not in merged:
                            merged.append(term)
                    if merged not in result:
                        result.append(merged)
        on_stack.discard(region)
        cache[region] = result
        return result

    return {r: dnf_to_access_rules(compute(r)) for r in regions}


# ---------- category gating from categories.json

def category_option_gates(categories: dict[str, dict]) -> dict[str, list[str]]:
    """Return {category_name: [yaml_option_keys...]} — the yaml options whose
    being-ON enables this category. Empty list means the category is always
    enabled (no option gates it)."""
    out: dict[str, list[str]] = {}
    for cat, info in categories.items():
        opts = info.get("yaml_option") or []
        # Each entry may start with "!" meaning "disabled-when" — none in current data
        out[cat] = [o.lstrip("!") for o in opts]
    return out


# ---------- placeholder map image (stdlib-only PNG encoder)

def make_solid_png(w: int, h: int, rgba: tuple[int, int, int, int] = (32, 32, 32, 255)) -> bytes:
    """Encode a solid-color RGBA PNG of given size. Used as the map
    background for the pack's kingdom-pin layout. zlib produces a
    very small file because all rows are identical."""
    sig = b'\x89PNG\r\n\x1a\n'

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)

    # IHDR: width, height, bit_depth=8, color_type=6 (RGBA), the three zero
    # bytes are compression=0, filter=0, interlace=0.
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    # Each scanline prefixed with filter-type byte 0 (None), then RGBA per px.
    row = bytes([0]) + bytes(rgba) * w
    raw = row * h
    idat = zlib.compress(raw, 9)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


# ---------- festival% goal scope

# Mirrors the apworld's festival pruning in __init__.py — keep in sync.
# Locations the apworld drops under the festival goal get $is_goal|0 ANDed
# onto their access rule so they evaluate as unreachable when slot_data.goal
# == 1 (festival), and behave normally when goal == 0 (mushroom kingdom).

FESTIVAL_REGIONS_HIDDEN = frozenset({
    "Post-Metro", "Snow Kingdom", "Post-Snow",
    "Seaside Kingdom", "Post-Seaside", "Very Early Luncheon",
    "Luncheon Kingdom", "Ruined Kingdom", "Pokino",
    "Bowser's Kingdom", "Moon Kingdom",
})


# Matches a MetroPeace() rule call in a `requires` string. Word boundary
# prevents a hypothetical future MetroPeaceful() rule from false-matching;
# the open paren ensures we're matching a call, not the bare word.
_METRO_PEACE_CALL_RE = re.compile(r"\bMetroPeace\s*\(")


def _needs_metro_peace(loc: dict) -> bool:
    """True if this Metro/Night Metro location is only reachable after
    Metro Peace (the post-festival "kingdom calmed" state). Mirrors
    __init__.py:_needs_metro_peace — two signals, locations carry one or
    both: "Metro Peace" category tag, or a MetroPeace() call in
    `requires`."""
    if "Metro Peace" in loc.get("category", []):
        return True
    requires = loc.get("requires", "")
    return isinstance(requires, str) and bool(_METRO_PEACE_CALL_RE.search(requires))

# Goal-option-value → victory-location-name. Mirror of the apworld's
# SMOWorld.GOAL_TO_VICTORY in __init__.py — values come from the static
# Goal(Choice) class in hooks/Options.py (option_mushroom_kingdom = 0,
# option_festival = 1). Cannot use the location's order in locations.json
# as the index because the festival moon happens to appear first there.
GOAL_OPTION_TO_VICTORY = {
    0: "Arrive in the Mushroom Kingdom",
    1: "Metro: A Traditional Festival!",
}
VICTORY_TO_GOAL_OPTION = {v: k for k, v in GOAL_OPTION_TO_VICTORY.items()}


def location_out_of_festival_scope(loc: dict) -> bool:
    """True if `loc` is dropped from the world under the festival goal."""
    region = loc.get("region", "")
    if region in FESTIVAL_REGIONS_HIDDEN:
        return True
    if region in ("Metro Kingdom", "Night Metro") and _needs_metro_peace(loc):
        return True
    return False


# ---------- talkatoo% gate

def location_blocked_by_talkatoo(loc: dict) -> bool:
    """True if Talkatoo% mode would prevent collection of this location.

    Mirrors MoonGetHook.cpp's `talkatoo-block` branch: a moon is blocked iff
    it's an AP-tracked moon that lacks `progression: true`. Captures use a
    different hook and aren't gated; victory locations fire via CreditsStartHook
    or are flagged progression and bypass the block."""
    name = loc.get("name", "")
    if name.startswith("Capture:"):
        return False
    if loc.get("victory"):
        return False
    if loc.get("progression"):
        return False
    return ":" in name  # any "<Kingdom>: <moon>" entry is a moon


# ---------- kingdom display grouping

# Order is the linear-chain order from regions.json.
KINGDOM_ORDER = [
    "Cap Kingdom",
    "Cascade Kingdom",
    "Sand Kingdom",
    "Lake Kingdom",
    "Wooded Kingdom",
    "Cloud Kingdom",
    "Lost Kingdom",
    "Night Metro",
    "Metro Kingdom",
    "Snow Kingdom",
    "Seaside Kingdom",
    "Very Early Luncheon",
    "Luncheon Kingdom",
    "Ruined Kingdom",
    "Pokino",
    "Bowser's Kingdom",
    "Moon Kingdom",
    "Mushroom Kingdom",
    "Post-Sand",
    "Post-Lake",
    "Post-Wooded",
    "Post-Metro",
    "Post-Snow",
    "Post-Seaside",
]


def kingdom_for(location: dict) -> str:
    """Pick the user-facing kingdom tree node for a location. Prefers the
    name-prefix (the colon split) since that's how players think of moons,
    falling back to the region field. Cap/Cascade/Sand/Lake/Wooded/Cloud/
    Lost/Metro/Snow/Seaside/Luncheon/Ruined/Bowser's/Moon/Mushroom."""
    name = location["name"]
    if ":" in name:
        prefix = name.split(":", 1)[0].strip()
        # Map short prefix to full kingdom node
        full = f"{prefix} Kingdom"
        if full in KINGDOM_ORDER:
            return full
        # Some location names start with "Capture: <hack>" — bucket under "Captures"
        if prefix == "Capture":
            return "Captures"
    return location.get("region", "Other")


# ---------- map pin layout (4×4 grid of 16 kingdom buckets)

# (col, row) into a 4x4 grid; col 0..3 left-to-right, row 0..3 top-to-bottom.
# Ordering loosely follows the linear-progression chain so neighbors on the
# map are also neighbors in the game's intended kingdom order.
KINGDOM_GRID: dict[str, tuple[int, int]] = {
    "Cap Kingdom":      (0, 0),
    "Cascade Kingdom":  (1, 0),
    "Sand Kingdom":     (2, 0),
    "Lake Kingdom":     (3, 0),
    "Wooded Kingdom":   (0, 1),
    "Cloud Kingdom":    (1, 1),
    "Lost Kingdom":     (2, 1),
    "Metro Kingdom":    (3, 1),
    "Snow Kingdom":     (0, 2),
    "Seaside Kingdom":  (1, 2),
    "Luncheon Kingdom": (2, 2),
    "Ruined Kingdom":   (3, 2),
    "Bowser's Kingdom": (0, 3),
    "Moon Kingdom":     (1, 3),
    "Mushroom Kingdom": (2, 3),
    "Captures":         (3, 3),
}

PIN_X_BASE = 100
PIN_Y_BASE = 100
PIN_X_STEP = 180
PIN_Y_STEP = 120
PIN_SIZE = 50
MAP_IMAGE_W = PIN_X_BASE * 2 + PIN_X_STEP * 3  # 740
MAP_IMAGE_H = PIN_Y_BASE * 2 + PIN_Y_STEP * 3  # 560


def kingdom_map_pin(kingdom: str) -> dict | None:
    """One pin per kingdom on the shared 'smo' map; clicking it opens
    the kingdom's section list (i.e., its moons). The grid is uniform —
    pins are labelled by the location name on hover."""
    cell = KINGDOM_GRID.get(kingdom)
    if cell is None:
        return None
    col, row = cell
    return {
        "map": "smo",
        "x": PIN_X_BASE + col * PIN_X_STEP,
        "y": PIN_Y_BASE + row * PIN_Y_STEP,
    }


def emit_maps_json() -> list[dict]:
    return [{
        "name": "smo",
        "img": "images/maps/smo.png",
        "location_size": PIN_SIZE,
        "location_shape": "rect",
    }]


# ---------- emitters

def emit_items(items: list[dict], filler_name: str) -> list[dict]:
    """PopTracker items entries. Moons → consumable with max_quantity=count.
    Captures → toggle."""
    out: list[dict] = []
    for it in items:
        name = it["name"]
        cat = it.get("category") or []
        count = it.get("count", 1)
        entry: dict[str, Any] = {
            "name": name,
            "codes": code_for(name),
        }
        if "Moon" in cat:
            entry["type"] = "consumable"
            entry["initial_quantity"] = 0
            entry["max_quantity"] = count
            entry["increment"] = 1
            entry["decrement"] = 1
            entry["allow_disabled"] = False
        else:
            entry["type"] = "toggle"
            entry["initial_active"] = False
            entry["allow_disabled"] = False
        out.append(entry)
    # Filler item (Coin) is appended in id-allocation; we don't emit it as a
    # tracker item since it carries no logic weight, but include it so the
    # mapping table can resolve its id without complaint.
    out.append({
        "name": filler_name,
        "codes": code_for(filler_name),
        "type": "consumable",
        "initial_quantity": 0,
        "max_quantity": 9999,
        "increment": 1,
        "decrement": 1,
    })
    return out


def emit_kingdom_credit_items(items: list[dict]) -> list[dict]:
    """Per-kingdom synthetic credit items computed by autotracking.lua as
    PowerMoon*1 + MultiMoon*3. Read by has_kingdom_moons() in logic.lua."""
    kingdoms: set[str] = set()
    for it in items:
        m = re.match(r"(.+) Kingdom (Power Moon|Multi-Moon)$", it["name"])
        if m:
            kingdoms.add(m.group(1))
    out: list[dict] = []
    for k in sorted(kingdoms):
        out.append({
            "name": f"{k} Kingdom Credits",
            "codes": code_for(k) + "_credits",
            "type": "consumable",
            "initial_quantity": 0,
            "max_quantity": 999,
            "increment": 1,
            "decrement": 1,
        })
    return out


# Option metadata: { yaml_key: (display_name, default_on) }
# Mirrors apworld/.../hooks/Options.py. Kept in sync by hand because
# parsing the Python class definitions is not robust; the consequence
# of getting this list wrong is a missing option toggle, surfaced
# loudly at pack-load time when slot_data references an unknown code.
OPTION_META = {
    "capturesanity":                ("Capturesanity",                          False),
    "include_cap_peace_moons":      ("Include Cap Kingdom Peace Moons",        True),
    "include_cascade_peace_moons":  ("Include Cascade Kingdom Peace Moons",    True),
    "include_sand_peace_moons":     ("Include Sand Kingdom Peace Moons",       True),
    "include_lake_peace_moons":     ("Include Lake Kingdom Peace Moons",       True),
    "include_wooded_peace_moons":   ("Include Wooded Kingdom Peace Moons",     True),
    "include_lost_peace_moons":     ("Include Lost Kingdom Peace Moons",       True),
    "include_metro_peace_moons":    ("Include Metro Kingdom Peace Moons",      True),
    "include_snow_peace_moons":     ("Include Snow Kingdom Peace Moons",       True),
    "include_seaside_peace_moons":  ("Include Seaside Kingdom Peace Moons",    True),
    "include_luncheon_peace_moons": ("Include Luncheon Kingdom Peace Moons",   True),
    "include_bowsers_peace_moons":  ("Include Bowser's Kingdom Peace Moons",   True),
    "include_cloud_peace_moons":    ("Include Cloud Kingdom Peace Moons",      True),
    "include_deep_woods_moons":     ("Include Deep Woods Moons",               True),
    "include_minigame_moons":       ("Include Minigame Moons",                 True),
    "include_hint_art_moons":       ("Include Hint Art Moons",                 True),
    "include_tourist_moons":        ("Include Tourist Moons",                  True),
    "include_long_course_moons":    ("Include Long Course Moons",              True),
    "include_precision_capture_moons": ("Include Precision Capture Moons",     True),
}


def emit_option_items(victory_names: list[str]) -> list[dict]:
    """Synthetic toggle items for every yaml option + every victory variant."""
    out: list[dict] = []
    for key, (display, default_on) in OPTION_META.items():
        out.append({
            "name": display,
            "codes": f"opt_{key}",
            "type": "toggle",
            "initial_active": bool(default_on),
            "allow_disabled": False,
        })
    for i, vname in enumerate(victory_names):
        out.append({
            "name": f"Goal: {vname}",
            "codes": f"goal_{i}",
            "type": "toggle",
            # First victory is the default-selected goal (matches AP Choice default)
            "initial_active": (i == 0),
            "allow_disabled": False,
        })
    return out


def emit_locations(
    locations: list[dict],
    region_prereqs: dict[str, list[str]],
    cat_gates: dict[str, list[str]],
    victory_names: list[str],
) -> list[dict]:
    """Build the PopTracker locations.json.

    Flat structure: one top-level "location" per kingdom, with each moon
    appearing as a section of that kingdom. The deeper kingdom→child-location
    →section nesting we used initially isn't supported by PopTracker — the
    DBFZ reference pack uses this flat arc→sections form.

    PopTracker JSON shape (per kingdom):
        {
          "name": "Cap Kingdom",
          "sections": [
            {"name": "Cap: Frog-Jumping Above the Fog", "item_count": 1,
             "access_rules": ["$has_kingdom_moons|Cascade|5"]},
            ...
          ]
        }
    """
    by_kingdom: dict[str, list[dict]] = {}
    for loc in locations:
        kingdom = kingdom_for(loc)
        own_dnf = to_dnf(parse_requires(loc.get("requires", "")))
        # AND with region access
        region = loc.get("region", "SMO")
        region_rules = region_prereqs.get(region, [])
        if region_rules:
            region_dnf = [r.split(",") if r else [] for r in region_rules]
            own_dnf = _and_dnfs(region_dnf, own_dnf)
        # AND with category-option gates (location only exists when each
        # gating option is ON)
        opt_terms: list[str] = []
        for cat in loc.get("category", []):
            for opt in cat_gates.get(cat, []):
                term = f"$is_opt|{opt}"
                if term not in opt_terms:
                    opt_terms.append(term)
        if opt_terms:
            own_dnf = [clause + [t for t in opt_terms if t not in clause] for clause in own_dnf]
        def _and_term(dnf: list[list[str]], term: str) -> list[list[str]]:
            return [clause if term in clause else clause + [term] for clause in dnf]

        # AND victory locations with $is_goal|<option_value>. Uses the
        # explicit Goal-option mapping rather than victory_names.index() —
        # the location-table order in locations.json isn't guaranteed to
        # match Goal's option_* numeric values.
        if loc.get("victory"):
            gi = VICTORY_TO_GOAL_OPTION.get(loc["name"])
            if gi is None:
                try:
                    gi = victory_names.index(loc["name"])
                except ValueError:
                    gi = 0
            own_dnf = _and_term(own_dnf, f"$is_goal|{gi}")
        # Festival% scope: locations the apworld removes when goal=festival
        # get $is_goal|0 ANDed in, so they evaluate as unreachable when the
        # connected slot has goal=1. Offline (no slot_data) defaults to
        # OPTIONS.goal=0 so these stay visible as normal.
        if location_out_of_festival_scope(loc):
            own_dnf = _and_term(own_dnf, "$is_goal|0")
        # Talkatoo% gate: non-progression moons become collectible only when
        # Talkatoo names them (3 per kingdom, rotating). The pack doesn't
        # track Talkatoo's current window, so we hide all blockable moons
        # when talkatoo_mode is on — story moons and captures stay visible.
        if location_blocked_by_talkatoo(loc):
            own_dnf = _and_term(own_dnf, "$is_opt_off|talkatoo_mode")
        rules = dnf_to_access_rules(own_dnf)
        section: dict[str, Any] = {"name": loc["name"], "item_count": 1}
        if rules:
            section["access_rules"] = rules
        by_kingdom.setdefault(kingdom, []).append(section)
    # Order kingdoms by KINGDOM_ORDER, append unknowns at the end
    ordered_kingdoms = [k for k in KINGDOM_ORDER if k in by_kingdom]
    extra = [k for k in by_kingdom if k not in ordered_kingdoms]
    for k in sorted(extra):
        ordered_kingdoms.append(k)
    out: list[dict] = []
    for k in ordered_kingdoms:
        entry: dict[str, Any] = {"name": k, "sections": by_kingdom[k]}
        pin = kingdom_map_pin(k)
        if pin:
            entry["map_locations"] = [pin]
        out.append(entry)
    return out


def _and_dnfs(a: list[list[str]], b: list[list[str]]) -> list[list[str]]:
    if not a:
        return list(b)
    if not b:
        return list(a)
    result: list[list[str]] = []
    for ac in a:
        for bc in b:
            merged = list(ac)
            for term in bc:
                if term not in merged:
                    merged.append(term)
            if merged not in result:
                result.append(merged)
    return result


def emit_mappings_lua(
    item_ids: list[tuple[int, dict]],
    location_ids: list[tuple[int, dict]],
) -> str:
    """Write LOCATION_MAPPING + ITEM_MAPPING tables. AP-server-issued ids
    are the keys; tracker codes (or section refs) are the values."""
    lines = ["-- AUTO-GENERATED by scripts/build_poptracker_pack.py — DO NOT EDIT.", ""]
    lines.append("ITEM_MAPPING = {")
    for ap_id, it in item_ids:
        c = code_for(it["name"])
        cat = it.get("category") or []
        kind = "consumable" if "Moon" in cat else "toggle"
        # Filler 'Coin' (last entry, no category) → consumable
        if not cat:
            kind = "consumable"
        lines.append(f'  [{ap_id}] = {{"{c}", "{kind}"}},')
    lines.append("}")
    lines.append("")
    lines.append("LOCATION_MAPPING = {")
    for ap_id, loc in location_ids:
        ref = section_for(kingdom_for(loc), loc["name"])
        # Escape any internal quotes
        ref_lit = ref.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'  [{ap_id}] = "{ref_lit}",')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


# ---------- main

def build(root: Path, version: str, out_dir: Path) -> dict[str, Any]:
    """Build the pack into out_dir. Returns a summary dict."""
    apw = root / "apworld" / "smo_archipelago"
    pack_src = root / "poptracker" / "pack-src"
    data_dir = apw / "data"

    items = json.loads((data_dir / "items.json").read_text(encoding="utf-8"))
    locations = json.loads((data_dir / "locations.json").read_text(encoding="utf-8"))
    regions = json.loads((data_dir / "regions.json").read_text(encoding="utf-8"))
    categories = json.loads((data_dir / "categories.json").read_text(encoding="utf-8"))

    # Seed the pool-item set before any requires string is translated (both
    # build_region_prereqs() below and location emission consult it via
    # _item_rule/_func_to_dnf). See POOL_ITEM_NAMES comment for why.
    POOL_ITEM_NAMES.clear()
    POOL_ITEM_NAMES.update(i["name"] for i in items)

    # Mirrors the inlined `game_table` in apworld/smo_archipelago/Data.py.
    # Update both if either changes — the AP id allocation in starting_index()
    # is seeded from these and must agree with the apworld at runtime.
    game_short = "MEATBALLS"
    creator = "maxdietz"
    filler_name = "Coin"
    start = starting_index(game_short, creator)
    item_ids = allocate_item_ids(items, filler_name, start)
    location_ids = allocate_location_ids(locations, start)
    victory_names = [l["name"] for l in locations if l.get("victory")]

    region_prereqs = build_region_prereqs(regions)
    cat_gates = category_option_gates(categories)

    # Clear and recreate out_dir
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # Copy pack-src verbatim
    if not pack_src.exists():
        raise SystemExit(f"pack-src missing: {pack_src}")
    for src in pack_src.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(pack_src)
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # Stamp version into manifest.json
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["package_version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Write generated items
    items_dir = out_dir / "items"
    items_dir.mkdir(exist_ok=True)
    (items_dir / "items.json").write_text(
        json.dumps(emit_items(items, filler_name), indent=2), encoding="utf-8"
    )
    (items_dir / "credits.json").write_text(
        json.dumps(emit_kingdom_credit_items(items), indent=2), encoding="utf-8"
    )
    # Option toggles + goal selection are NOT emitted as Tracker items —
    # they live in a Lua OPTIONS table (scripts/logic.lua) populated by
    # autotracking.lua from slot_data on connect. This avoids the
    # untoggleable-without-images problem and keeps the layout minimal.

    # Write generated locations
    locs_dir = out_dir / "locations"
    locs_dir.mkdir(exist_ok=True)
    (locs_dir / "locations.json").write_text(
        json.dumps(emit_locations(locations, region_prereqs, cat_gates, victory_names), indent=2),
        encoding="utf-8",
    )

    # Write the maps.json + placeholder PNG. PopTracker has no built-in
    # location-list widget — without a map widget showing pins, locations
    # are invisible. We render a 4x4 grid of kingdom pins on a solid-color
    # PNG; clicking a pin opens that kingdom's moon sections.
    maps_dir = out_dir / "maps"
    maps_dir.mkdir(exist_ok=True)
    (maps_dir / "maps.json").write_text(
        json.dumps(emit_maps_json(), indent=2), encoding="utf-8"
    )
    images_maps_dir = out_dir / "images" / "maps"
    images_maps_dir.mkdir(parents=True, exist_ok=True)
    (images_maps_dir / "smo.png").write_bytes(
        make_solid_png(MAP_IMAGE_W, MAP_IMAGE_H, (32, 32, 32, 255))
    )

    # Write generated Lua mappings
    scripts_dir = out_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "mappings.lua").write_text(
        emit_mappings_lua(item_ids, location_ids), encoding="utf-8"
    )

    return {
        "items": len(items),
        "locations": len(locations),
        "regions": len(regions),
        "kingdoms": len({kingdom_for(l) for l in locations}),
        "victory_names": victory_names,
        "starting_index": start,
        "version": version,
    }


def make_zip(out_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(out_dir.parent))


# ---------- self-test (sanity checks; no external deps)

def self_test() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name}: {detail}")

    # starting_index reproduces the known MEATBALLS/maxdietz value derived
    # from apworld/smo_archipelago/Game.py. Pre-2026-05-20 the seed was
    # ("SMO", "archipelago") with a starting_index of 14_481_151_000; the
    # 2026-05-20 rename to ("MEATBALLS", "maxdietz") shifted every AP id.
    s = starting_index("MEATBALLS", "maxdietz")
    check("starting_index MEATBALLS/maxdietz", s == 13_404_070_000,
          f"got {s}, want 13404070000")

    # tokenize: simple
    toks = tokenize("|A| and |B|")
    check("tokenize trivial",
          toks == [("ITEM", "A"), ("AND", "and"), ("ITEM", "B")], str(toks))

    # parse: trivial
    ast = parse_requires("|A| and |B|")
    check("parse and", ast == ("and", [("item", "A", 1), ("item", "B", 1)]), str(ast))

    # parse: nested
    ast = parse_requires("(|A| and |B|) or |C|")
    expected = ("or", [("and", [("item", "A", 1), ("item", "B", 1)]), ("item", "C", 1)])
    check("parse paren+or", ast == expected, str(ast))

    # parse: function
    ast = parse_requires("{SandPeace()}")
    check("parse zero-arg func", ast == ("func", "SandPeace", []), str(ast))

    ast = parse_requires("{KingdomMoons(Cascade,5)}")
    check("parse KingdomMoons", ast == ("func", "KingdomMoons", ["Cascade", "5"]), str(ast))

    # to_dnf: empty → one always-true clause
    check("dnf true", to_dnf(("true",)) == [[]], str(to_dnf(("true",))))

    # to_dnf: |A| → [["a"]]
    check("dnf single item", to_dnf(("item", "A", 1)) == [["a"]],
          str(to_dnf(("item", "A", 1))))

    # to_dnf: AND of two items
    dnf = to_dnf(parse_requires("|A| and |B|"))
    check("dnf and", dnf == [["a", "b"]], str(dnf))

    # to_dnf: OR of two items
    dnf = to_dnf(parse_requires("|A| or |B|"))
    check("dnf or", dnf == [["a"], ["b"]], str(dnf))

    # to_dnf: (A AND B) OR C
    dnf = to_dnf(parse_requires("(|A| and |B|) or |C|"))
    check("dnf and+or", dnf == [["a", "b"], ["c"]], str(dnf))

    # OptOne expansion
    dnf = to_dnf(parse_requires("{OptOne(T-Rex)}"))
    check("dnf optone", dnf == [["$capturesanity_off"], ["t_rex"]], str(dnf))

    # OptOne over a name that never enters the pool (base move / excluded
    # capture) → trivially true, NOT has(<nonexistent>). Guard is active only
    # when POOL_ITEM_NAMES is populated, so seed it for these cases.
    POOL_ITEM_NAMES.clear()
    POOL_ITEM_NAMES.update({"T-Rex", "Pokio"})
    dnf = to_dnf(parse_requires("{OptOne(Ground Pound)}"))
    check("dnf optone non-pool → true", dnf == [[]], str(dnf))
    dnf = to_dnf(parse_requires("{OptOne(T-Rex)}"))
    check("dnf optone pool item unchanged",
          dnf == [["$capturesanity_off"], ["t_rex"]], str(dnf))
    # bare reference to a non-pool name is also trivially true
    dnf = to_dnf(parse_requires("|Ground Pound|"))
    check("dnf bare non-pool → true", dnf == [[]], str(dnf))
    POOL_ITEM_NAMES.clear()

    # OptAll expansion
    dnf = to_dnf(parse_requires("{OptAll(|X| and |Y|)}"))
    check("dnf optall and", dnf == [["$capturesanity_off"], ["x", "y"]], str(dnf))

    dnf = to_dnf(parse_requires("{OptAll(|X| or |Y|)}"))
    check("dnf optall or",
          dnf == [["$capturesanity_off"], ["x"], ["y"]], str(dnf))

    # KingdomMoons
    dnf = to_dnf(parse_requires("{KingdomMoons(Cascade,5)}"))
    check("dnf kingdommoons",
          dnf == [["$has_kingdom_moons|Cascade|5"]], str(dnf))

    # YamlDisabled OR item
    dnf = to_dnf(parse_requires("{YamlDisabled(capturesanity)} or |Pokio|"))
    check("dnf yamldisabled",
          dnf == [["$is_opt_off|capturesanity"], ["pokio"]], str(dnf))

    # |Name:N| → has_count helper
    dnf = to_dnf(parse_requires("|Cap Kingdom Power Moon:5|"))
    check("dnf has_count",
          dnf == [["$has_count|cap_kingdom_power_moon|5"]], str(dnf))

    # access_rules: empty inner clause → omit access_rules
    check("rules omit always-true", dnf_to_access_rules([[]]) == [],
          str(dnf_to_access_rules([[]])))

    # access_rules: dedupe
    check("rules dedupe",
          dnf_to_access_rules([["a", "b"], ["b", "a"]]) == ["a,b"],
          str(dnf_to_access_rules([["a", "b"], ["b", "a"]])))

    # region prereqs: Sand Kingdom requires {KingdomMoons(Cascade,5)}
    regions = {
        "Cascade Kingdom": {"requires": [], "connects_to": ["Sand Kingdom"], "starting": True},
        "Sand Kingdom": {"requires": "{KingdomMoons(Cascade,5)}", "connects_to": ["Post-Sand"]},
        "Post-Sand": {"requires": "{SandPeace()}", "connects_to": []},
    }
    pre = build_region_prereqs(regions)
    check("region cascade is always-true", pre["Cascade Kingdom"] == [], str(pre["Cascade Kingdom"]))
    check("region sand chains",
          pre["Sand Kingdom"] == ["$has_kingdom_moons|Cascade|5"], str(pre["Sand Kingdom"]))
    check("region post-sand chains both",
          pre["Post-Sand"] == ["$has_kingdom_moons|Cascade|5,$sand_peace"], str(pre["Post-Sand"]))

    # kingdom_for: prefix match
    check("kingdom_for Cap", kingdom_for({"name": "Cap: X", "region": "Cap Kingdom"}) == "Cap Kingdom")
    check("kingdom_for Bowser's apostrophe",
          kingdom_for({"name": "Bowser's: Y", "region": "Bowser's Kingdom"}) == "Bowser's Kingdom")

    # code_for
    check("code apostrophe",
          code_for("Knucklotec's Fist") == "knucklotec_s_fist",
          code_for("Knucklotec's Fist"))
    check("code multimoon",
          code_for("Sand Kingdom Multi-Moon") == "sand_kingdom_multi_moon",
          code_for("Sand Kingdom Multi-Moon"))

    # festival% scope: out-of-scope regions
    check("festival hides Snow",
          location_out_of_festival_scope({"name": "Snow: X", "region": "Snow Kingdom"}))
    check("festival hides Bowser's",
          location_out_of_festival_scope({"name": "Bowser's: Y", "region": "Bowser's Kingdom"}))
    check("festival hides Post-Metro",
          location_out_of_festival_scope({"name": "Anything", "region": "Post-Metro"}))
    # festival% scope: pre-Metro-Peace Metro/Night Metro moons stay
    check("festival keeps Mechawiggler",
          not location_out_of_festival_scope(
              {"name": "Metro: New Donk City's Pest Problem", "region": "Metro Kingdom",
               "category": ["Metro Kingdom"], "requires": "{OptOne(Sherm)}"}))
    check("festival keeps Drummer",
          not location_out_of_festival_scope(
              {"name": "Metro: Drummer on Board!", "region": "Metro Kingdom",
               "category": ["Metro Kingdom"], "requires": []}))
    check("festival keeps the goal moon",
          not location_out_of_festival_scope(
              {"name": "Metro: A Traditional Festival!", "region": "Metro Kingdom",
               "category": ["Metro Kingdom"],
               "requires": "{PostTrumpeter()} and {OptOne(Manhole)}"}))
    check("festival keeps non-peace Metro moon",
          not location_out_of_festival_scope(
              {"name": "Metro: Inside the Rotating Maze", "region": "Metro Kingdom",
               "category": ["Metro Kingdom"], "requires": "{OptOne(Manhole)}"}))
    check("festival keeps Night Metro moons",
          not location_out_of_festival_scope(
              {"name": "Metro: Inside an Iron Girder", "region": "Night Metro",
               "category": ["Metro Kingdom"], "requires": []}))
    # festival% scope: Metro Peace moons hidden — by category tag, by requires, or both
    check("festival hides via category tag",
          location_out_of_festival_scope(
              {"name": "Metro: Hidden in the Scrap", "region": "Metro Kingdom",
               "category": ["Metro Kingdom", "Metro Peace"], "requires": []}))
    check("festival hides via MetroPeace requires",
          location_out_of_festival_scope(
              {"name": "Metro: Sewer Treasure", "region": "Metro Kingdom",
               "category": ["Metro Kingdom"], "requires": "{MetroPeace()}"}))
    # regex must require a CALL (open paren) and word boundary — guards
    # against a hypothetical future MetroPeaceful()/PostMetroPeace()/etc.
    check("festival does NOT false-match MetroPeaceful",
          not _needs_metro_peace(
              {"category": [], "requires": "{MetroPeaceful()}"}))
    check("festival does NOT false-match PreMetroPeace string",
          not _needs_metro_peace(
              {"category": [], "requires": "PreMetroPeacefully"}))
    check("festival matches MetroPeace with whitespace",
          _needs_metro_peace(
              {"category": [], "requires": "{MetroPeace ()}"}))
    # festival% scope: pre-Metro untouched
    check("festival keeps Cascade",
          not location_out_of_festival_scope(
              {"name": "Cascade: Our First Power Moon", "region": "Cascade Kingdom",
               "category": ["Cascade Kingdom"], "requires": []}))

    # Goal-option mapping: must match apworld __init__.py's GOAL_TO_VICTORY.
    check("goal mapping mushroom=0",
          VICTORY_TO_GOAL_OPTION["Arrive in the Mushroom Kingdom"] == 0)
    check("goal mapping festival=1",
          VICTORY_TO_GOAL_OPTION["Metro: A Traditional Festival!"] == 1)

    # Talkatoo% gate: only non-progression, non-victory, non-capture moons.
    check("talkatoo blocks plain moon",
          location_blocked_by_talkatoo(
              {"name": "Sand: Inside the Stone Cage", "region": "Sand Kingdom"}))
    check("talkatoo skips story moon",
          not location_blocked_by_talkatoo(
              {"name": "Cascade: Our First Power Moon",
               "region": "Cascade Kingdom", "progression": True}))
    check("talkatoo skips capture",
          not location_blocked_by_talkatoo(
              {"name": "Capture: Bullet Bill", "region": "Sand Kingdom"}))
    check("talkatoo skips victory moon",
          not location_blocked_by_talkatoo(
              {"name": "Metro: A Traditional Festival!",
               "region": "Metro Kingdom",
               "victory": True, "progression": True}))
    check("talkatoo skips credits goal",
          not location_blocked_by_talkatoo(
              {"name": "Arrive in the Mushroom Kingdom",
               "region": "Moon Kingdom", "victory": True}))

    if failures:
        print("FAIL:", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)
        return 1
    print(f"OK ({len(failures)} failures)")
    return 0


def detect_version(root: Path) -> str:
    """Best-effort: read world_version from apworld/__init__.py; fallback to git short sha; else 0.0.0+dev."""
    init = root / "apworld" / "smo_archipelago" / "__init__.py"
    if init.exists():
        for line in init.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*world_version\s*=\s*[\"']([^\"']+)[\"']", line)
            if m:
                return m.group(1)
    # Try git short sha
    try:
        import subprocess
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, text=True,
            stderr=subprocess.DEVNULL).strip()
        if sha:
            return f"0.0.0+g{sha}"
    except Exception:
        pass
    return "0.0.0+dev"


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--version", default=None, help="package_version (default: detected)")
    ap.add_argument("--out", type=Path, default=root / "poptracker" / "build" / "smo-poptracker",
                    help="output pack directory")
    ap.add_argument("--zip", action="store_true", help="also produce a release zip")
    ap.add_argument("--self-test", action="store_true", help="run internal parser/translator tests and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    version = args.version or detect_version(root)
    print(f"building pack v{version} from {root / 'apworld' / 'smo_archipelago'}")
    summary = build(root, version, args.out)
    print(f"  items: {summary['items']} + 1 filler + "
          f"{len(emit_kingdom_credit_items(json.loads((root / 'apworld/smo_archipelago/data/items.json').read_text())))} credit composites "
          f"(option/goal state via Lua OPTIONS table populated from slot_data)")
    print(f"  locations: {summary['locations']} across {summary['kingdoms']} kingdom nodes")
    print(f"  regions: {summary['regions']}")
    print(f"  starting_index: {summary['starting_index']}")
    print(f"  victories: {summary['victory_names']}")
    print(f"  output: {args.out}")
    if args.zip:
        zip_path = args.out.parent / f"smo-poptracker-v{version}.zip"
        make_zip(args.out, zip_path)
        size = zip_path.stat().st_size
        print(f"  zipped: {zip_path} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
