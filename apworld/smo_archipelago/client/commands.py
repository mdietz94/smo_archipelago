"""Pure command parsing for SMOClient's `/`-commands.

`parse_command()` is the load-bearing function — pure input string ->
ParseResult dataclass. The Kivy GUI's ClientCommandProcessor (in
context.py) calls each `_cmd_*` method, which delegates to this parser.

Item injection used to live here (`/grant`, `/capture`, `/kingdom`),
but those duplicated what the AP server's `/send` console already
does for every apworld. After they were removed, the AP-received
path in `context.py::_handle_ap_package` is the sole producer of
ItemMsgs. Use `/send <slot> <item name>` on the AP server console
to inject items during dev.

Surviving commands (`/label`, `/smo_status`, `/inject_deathlink`)
are debug utilities, not item sends. `/label` is parsed here;
`/smo_status` and `/inject_deathlink` are pure ClientCommandProcessor
methods in `context.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .protocol import MoonLabelMsg
from .state import BridgeState

log = logging.getLogger(__name__)


HELP_TEXT = """\
SMO Client commands (type with leading /):
  /label <text>                     send a MoonLabelMsg directly (Channel A visual
                                    test). seq is auto-assigned high (999999) so it
                                    beats any pending bridge-issued label.
  /smo_status                       show client-side tracker state
  /inject_deathlink [src] [cause]
                                    bypass AP entirely and synthesize a KillMsg
                                    straight to the Switch (debug)

To inject items, use the AP server console:
  /send <slot> <item name>          e.g. /send Mario Cascade Kingdom Power Moon
"""


@dataclass
class ParseResult:
    """Outcome of parsing a single command line.

    Exactly one (or none) of `label`, `info`, `error`, `quit` is set.
    """
    label: MoonLabelMsg | None = None
    info: str | None = None
    error: str | None = None
    quit: bool = False


def parse_command(line: str, state: BridgeState | None = None) -> ParseResult:
    """Pure parser — line -> action. Unit-testable without I/O."""
    s = line.strip()
    if not s:
        return ParseResult()  # silent no-op
    parts = s.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("quit", "exit", "q"):
        return ParseResult(quit=True)
    if cmd in ("help", "?", "h"):
        return ParseResult(info=HELP_TEXT)
    if cmd == "status":
        if state is None:
            return ParseResult(info="status unavailable (no client state attached)")
        n_items = len(state.received_items)
        n_checks = len(state.checked_locations)
        n_caps = len(state.captures_unlocked)
        moons_by_k = ", ".join(
            f"{k}={v}" for k, v in sorted(state.moons_received_by_kingdom.items())
        ) or "(none)"
        last = ""
        if n_items > 0:
            evt = state.received_items[-1]
            last = (f"  last item: kind={evt.item.kind} kingdom={evt.item.kingdom!r}"
                    f" shine_id={evt.item.shine_id!r} cap={evt.item.cap!r}"
                    f" from={evt.sender!r}\n")
        return ParseResult(info=(
            f"received_items={n_items} (by kingdom: {moons_by_k})\n"
            f"checked_locations={n_checks}\n"
            f"captures_unlocked={n_caps}\n"
            + last
        ))

    if cmd == "label":
        if not arg:
            return ParseResult(error="usage: label <text>")
        # 999999 sits well above any sane bridge-issued seq; useful for
        # standalone visual tests where you want to override a stale
        # pending label.
        return ParseResult(label=MoonLabelMsg(text=arg, seq=999999))

    return ParseResult(error=f"unknown command: {cmd!r}; type `help`")
