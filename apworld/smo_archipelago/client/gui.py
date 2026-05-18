"""Kivy UI for SMOClient.

THIS MODULE PULLS KIVY. Never import it from anywhere that runs at
apworld load time — generation hosts may not have a display server. Only
SMOContext.run_gui() reaches it, and run_gui is only called from
client/main.py inside the Launcher subprocess.

Subclasses CommonClient's GameManager, which provides:
  - top bar: server-address input + Connect button + thin progress bar
    bound to checked/missing AP locations
  - log tab: "Archipelago" (AP/Client-side logger output)
  - "Hints" tab (built-in)
  - bottom bar: Command: button + command prompt

We add ONE custom tab ("Odyssey") split 50/50 horizontally:
  * left  — at-a-glance SMO state (kingdoms unlocked, captures, moons by
            kingdom, DeathLink)
  * right — UILog tailing logger "SMO", which catches PC-side SMO
            diagnostics AND Switch-forwarded log lines (routed by
            switch_server.py for the "log" wire message type)

…plus ONE top-bar widget (a Switch status pill next to the AP Connect
button). Earlier iterations shipped a "Connections" tab and a fatter
"Tracker" tab; those were dropped because they duplicated info the
baseline UI already shows. A separate "Switch" log tab was also
dropped — its content lives in the right half of the Odyssey tab now.
"""

from __future__ import annotations

import logging
import typing

# IMPORTANT: kvui MUST be imported before any kivy.* module. kvui asserts
# `"kivy" not in sys.modules` at module top (for frozen-build compatibility),
# so any prior `from kivy.X import Y` here would trip the assert and prevent
# the GUI from starting. Same reason Wargroove imports kvui first.
from kvui import GameManager, UILog

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

if typing.TYPE_CHECKING:  # pragma: no cover
    from .context import SMOContext


# Polling interval for tab + Switch-pill refresh. State changes drive at
# human speed (moon collects, item arrivals, save loads) so 1.5s mirrors
# the old web tracker's setInterval and keeps Kivy's frame budget free.
_REFRESH_INTERVAL = 1.5


class _LiveLabel(Label):
    """Plain Label sized to its text content; pinned top-left in a ScrollView.

    Kivy's default Label is fixed-height and clips text; this binds height
    to texture_size so multi-line text grows the scrollable region.
    """

    def __init__(self, **kwargs):
        super().__init__(
            markup=True,
            valign="top",
            halign="left",
            size_hint_y=None,
            padding=(dp(10), dp(10)),
            **kwargs,
        )
        self.bind(width=self._refit, texture_size=self._refit)

    def _refit(self, *_):
        self.text_size = (self.width - dp(20), None)
        self.height = max(self.texture_size[1] + dp(20), dp(60))


class SmoManager(GameManager):
    """Window for the SMOClient.

    One AP-side log tab ("Archipelago") plus one custom tab ("Odyssey")
    that's a 50/50 horizontal split: at-a-glance SMO state on the left,
    live SMO + Switch-forwarded log tail on the right. The Switch-side
    log used to be its own tab but the left half of Odyssey was sparse
    and tab-hopping while debugging was annoying — co-locating them
    keeps state and diagnostics in one eye-line. One top-bar
    Switch-status pill next to the AP Connect button.
    """

    logging_pairs = [
        ("Client", "Archipelago"),
        # SMO logger ("SMO") is intentionally NOT a logging_pairs entry.
        # It's rendered in the right half of the Odyssey tab via a
        # manually-managed UILog (see build()). switch_server.py routes
        # every "log" wire message from the Switch into this same logger
        # with a "[switch:LEVEL] " prefix, so PC-side and device-side
        # diagnostics appear together — exactly what you want while
        # debugging a hardware-only behaviour.
    ]
    base_title = "Archipelago SMO Client"

    def __init__(self, ctx: "SMOContext"):
        super().__init__(ctx)
        self._odyssey_label: _LiveLabel | None = None
        self._switch_pill: Label | None = None
        self._smo_log: UILog | None = None

    def build(self):
        container = super().build()
        # Odyssey tab: horizontal 50/50 split.
        #   Left  — at-a-glance SMO state (kingdoms, captures, per-kingdom
        #           moon progress, DeathLink). Refreshed every 1.5s.
        #   Right — UILog tailing logger "SMO". Catches BOTH PC-side SMO
        #           diagnostics AND Switch-forwarded log lines (routed by
        #           switch_server.py for the "log" wire message type).
        #           UILog instantiation attaches a LogtoUI handler to the
        #           passed logger; records auto-tail and are capped at the
        #           kvui `messages` count (default 1000, client.kv).
        odyssey_split = BoxLayout(orientation="horizontal", spacing=dp(4))

        left_scroll = ScrollView(do_scroll_x=False, do_scroll_y=True,
                                 size_hint_x=0.5)
        self._odyssey_label = _LiveLabel(text="(connecting…)")
        left_scroll.add_widget(self._odyssey_label)
        odyssey_split.add_widget(left_scroll)

        self._smo_log = UILog(logging.getLogger("SMO"))
        self._smo_log.size_hint_x = 0.5
        odyssey_split.add_widget(self._smo_log)

        self.add_client_tab("Odyssey", odyssey_split)

        # Switch status pill, appended to the top connect_layout (which
        # already contains the AP server-address input + Connect button).
        # Mirrors LADX's "Open Tracker" button placement — the top bar is
        # where AP users expect to see connection state for ALL the wires
        # the client manages, not just AP.
        self._switch_pill = Label(
            text="Switch: idle",
            markup=True,
            size_hint_x=None,
            size_hint_y=None,
            width=dp(140),
            height=self.connect_layout.height,
            halign="center",
            valign="middle",
        )
        self._switch_pill.bind(size=self._switch_pill.setter("text_size"))
        self.connect_layout.add_widget(self._switch_pill)

        Clock.schedule_interval(self._refresh_panels, _REFRESH_INTERVAL)
        return container

    # ------------------------------------------------------------ panel refresh

    def _refresh_panels(self, _dt) -> None:
        try:
            if self._odyssey_label is not None:
                self._odyssey_label.text = _format_odyssey(self.ctx)
            if self._switch_pill is not None:
                self._switch_pill.text = _format_switch_pill(self.ctx)
        except Exception:
            # Don't let a transient render error kill the scheduled refresh;
            # Clock.schedule_interval cancels on exception.
            logging.getLogger("SMO").exception("panel refresh failed")


def _format_switch_pill(ctx: "SMOContext") -> str:
    """One-line Switch status for the top-bar pill, with markup color.

    Uses plain ASCII glyphs because Kivy's default Roboto subset doesn't
    include the geometric-shapes block (U+25CF / U+25CB rendered as tofu).
    Color already differentiates connected from listening.
    """
    sw = ctx.switch
    if sw is None:
        return "[color=#888888]Switch: idle[/color]"
    port = getattr(sw, "_port", "?")
    if sw.is_connected():
        return f"[color=#4caf50]Switch connected :{port}[/color]"
    return f"[color=#ff9800]Switch listening :{port}[/color]"


def _format_odyssey(ctx: "SMOContext") -> str:
    """Odyssey tab body — at-a-glance SMO progress, Kivy BBCode markup.

    Intentionally SKIPS:
      * slot / seed / items / checks / deaths — already in the window
        title (slot/seed on connect) and the top progress bar (checks).
      * recent items list — AP logs received items into the Archipelago
        tab with player + item names; duplicating was a hold-over from
        when the Flask web page was the only UI.
      * data-package / scout-cache debug counts — moved to /smo_status.
    """
    snap = ctx.state.snapshot()
    caps = snap.get("captures_unlocked") or []
    kingdoms = snap.get("kingdoms_unlocked") or []
    moons_chk = snap.get("moons_checked_by_kingdom") or {}
    moons_recv = snap.get("moons_received_by_kingdom") or {}
    pool_totals = ctx.dp.moon_pool_counts_by_kingdom()
    outstanding = ctx.state.get_outstanding()

    parts: list[str] = []
    parts.append("[b]Kingdoms unlocked[/b]")
    parts.append(", ".join(kingdoms) if kingdoms else "[i](none yet)[/i]")
    parts.append("")
    parts.append("[b]Moons by kingdom[/b]    [i]collected / received / pool — outstanding[/i]")
    all_k = sorted(set(moons_chk) | set(moons_recv) | set(pool_totals))
    if all_k:
        for k in all_k:
            chk = moons_chk.get(k, 0)
            recv = moons_recv.get(k, 0)
            pool = pool_totals.get(k, 0)
            out = outstanding.get(k, 0)
            parts.append(f"  {k}:    {chk} / {recv} / {pool}    ([b]{out}[/b] unspent)")
    else:
        parts.append("[i](nothing yet)[/i]")
    parts.append("")
    parts.append("[b]Captures unlocked[/b]")
    parts.append(", ".join(caps) if caps else "[i](none yet)[/i]")
    parts.append("")
    parts.append("[b]DeathLink[/b]: " + ("ENABLED" if ctx.deathlink_enabled else "disabled"))
    return "\n".join(parts)
