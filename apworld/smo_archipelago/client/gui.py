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
  * left  — at-a-glance SMO state (moons by kingdom, captures, DeathLink)
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

import os.path

from kivy import kivy_data_dir
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView

from .net_util import detect_lan_ip

if typing.TYPE_CHECKING:  # pragma: no cover
    from .context import SMOContext


# Polling interval for tab + Switch-pill refresh. State changes drive at
# human speed (moon collects, item arrivals, save loads) so 1.5s mirrors
# the old web tracker's setInterval and keeps Kivy's frame budget free.
_REFRESH_INTERVAL = 1.5


# Vanilla SMO main-story visit order. Used to sort the Odyssey-tab
# kingdom-moon table so the rows read top-to-bottom in the order the
# player will traverse them, not alphabetically. Names mirror the
# short-form used by KingdomMoons() clauses in regions.json — same
# keyspace as ctx.dp.kingdom_exit_thresholds and the snapshot's
# moons_received_by_kingdom dict. Unknown kingdoms (a future post-game
# addition we haven't listed) fall after the canonical entries,
# alphabetically.
_KINGDOM_VISIT_ORDER = (
    "Cap",
    "Cascade",
    "Sand",
    "Lake",
    "Wooded",
    "Lost",
    "Metro",
    "Snow",
    "Seaside",
    "Luncheon",
    "Ruined",
    "Bowser's",
    "Moon",
    "Mushroom",
    "Dark",
    "Darker",
)
_KINGDOM_ORDER_INDEX = {k: i for i, k in enumerate(_KINGDOM_VISIT_ORDER)}


def _kingdom_sort_key(k: str) -> tuple[int, str]:
    return (_KINGDOM_ORDER_INDEX.get(k, len(_KINGDOM_VISIT_ORDER)), k)


# Kingdoms hidden from the Odyssey tab under the festival goal — Metro
# itself plus every kingdom downstream of it in the linear-chain order.
# The festival% player completes their run inside Metro and shouldn't be
# leaving via the Odyssey; suppressing the rows keeps the UI from
# advertising that progression. AP items are unaffected — this is purely
# a display filter (the bridge still tracks moons_received_by_kingdom for
# these kingdoms in case the player ever re-runs against a non-festival
# seed without restarting the client).
#
# Keep in sync with context._FESTIVAL_ZEROED_KINGDOMS (which clamps the
# wire-protocol outstanding count to 0 for the same set so the Switch's
# M7 Path A gate stays closed).
_HIDDEN_KINGDOMS_FESTIVAL = frozenset({
    "Metro", "Snow", "Seaside", "Luncheon", "Ruined", "Bowser's", "Moon",
})


# Register Kivy's bundled monospace font under a short alias so the
# Odyssey tab can use [font=RobotoMono] markup to line up the per-kingdom
# moon-count table. We resolve the .ttf via kivy_data_dir directly
# rather than kivy.resources.resource_find — the font search path is
# populated by LabelBase.get_system_fonts_dir which only runs the first
# time a Label is instantiated, so at module-import time resource_find
# returns None and the alias never gets registered. When the markup
# tag later tries to resolve "RobotoMono" Kivy falls back to
# "RobotoMono.ttf" (per its endswith('.ttf') fallback) and raises
# OSError, killing the UI thread. _MONO_OK gates the [font=...] markup
# so a custom Kivy build that strips the bundled fonts degrades to
# the proportional default instead of crashing.
_MONO_FONT_PATH = os.path.join(kivy_data_dir, "fonts", "RobotoMono-Regular.ttf")
_MONO_OK = os.path.isfile(_MONO_FONT_PATH)
if _MONO_OK:
    LabelBase.register(name="RobotoMono", fn_regular=_MONO_FONT_PATH)


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


def _bind_switch_pill_layout(pill: Label) -> None:
    """Wire the auto-width + vertical-center bindings on the Switch pill.

    Two axes need different treatment:

    * **Width** auto-fits to the text texture (`width = texture_size[0] +
      dp(12)`) so the pill can't overflow the top bar — the AP server
      input absorbs whatever's left. This is what keeps the input usable
      at narrow window widths.

    * **Vertical centering** works only when `text_size[1]` is set, so
      Label's `valign='middle'` has a box height to center within. We
      bind that to widget height, keeping `text_size[0] = None`.

    Why we **do not** bind `text_size = widget_size` (both axes):
    setting `text_size[0]` to the widget width makes the texture render
    out to that width too, which feeds the texture_size→width binding,
    which grows the widget, which grows text_size[0]… The pill either
    runaway-grows until it eats every other top-bar widget (real
    `connect_layout` height) or collapses to width≈2 (zero-height
    layout). Either way, the user can't type a server address. See
    `test_switch_pill_layout.py` for the regression case.
    """
    pill.bind(
        texture_size=lambda lbl, sz: setattr(lbl, "width", sz[0] + dp(12)),
        height=lambda lbl, h: setattr(lbl, "text_size", (None, h)),
    )


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
        self._switch_pill: Button | None = None
        self._smo_log: UILog | None = None
        self._switches_popup: "SwitchesPopup | None" = None

    def build(self):
        container = super().build()
        # Odyssey tab: horizontal 50/50 split.
        #   Left  — at-a-glance SMO state (per-kingdom moon progress,
        #           captures, DeathLink). Refreshed every 1.5s.
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
        # Width auto-fits the text (texture_size[0] + a small pad) so the
        # pill can't overflow the top bar at narrow window widths — the
        # connect_layout's text input absorbs whatever's left over.
        # Height + pos_hint mirror the Connect button (kvui sets
        # server_connect_button.height = server_connect_bar.height and
        # pos_hint={"center_y": 0.55}) so the pill sits at the same
        # vertical position as the button instead of taking the full
        # dp(40) layout height (which made the text float to the very top
        # of the strip — valign='middle' alone wasn't enough).
        pill_h = self.server_connect_bar.height
        self._switch_pill = Button(
            text="Off",
            markup=True,
            size_hint_x=None,
            size_hint_y=None,
            width=dp(60),
            height=pill_h,
            halign="center",
            valign="middle",
            padding=(dp(6), 0),
            pos_hint={"center_y": 0.55},
            # Bound only on the height axis so valign='middle' centers the
            # texture vertically; width is left None so the texture_size
            # binding below can keep auto-fitting to the natural text width.
            # See _bind_switch_pill_layout for why both axes can't be bound.
            text_size=(None, pill_h),
            # Flatten the default Button background so the pill keeps the
            # old Label look — color carries the state, not a chrome
            # button outline.
            background_normal="",
            background_down="",
            background_color=(0, 0, 0, 0),
        )
        _bind_switch_pill_layout(self._switch_pill)
        self._switch_pill.bind(on_release=self._open_switches_popup)
        self.connect_layout.add_widget(self._switch_pill)

        # Event-driven Switches-changed refresh, so toggling active in
        # the popup repaints immediately instead of waiting up to 1.5s
        # for the next polling tick. The SwitchServer callback fires
        # synchronously from the asyncio thread; we Clock.schedule_once
        # so the actual widget mutations land on Kivy's thread.
        if self.ctx.switch is not None:
            self.ctx.switch.set_on_switches_changed(
                self._on_switches_changed_async,
            )

        Clock.schedule_interval(self._refresh_panels, _REFRESH_INTERVAL)
        return container

    def _on_switches_changed_async(self) -> None:
        """Asyncio-thread callback from SwitchServer. Hops to the Kivy
        thread to repaint the pill + popup. Kivy's Clock is thread-safe
        for schedule_once."""
        Clock.schedule_once(lambda _dt: self._refresh_panels(0), 0)

    # ------------------------------------------------------------ panel refresh

    def _refresh_panels(self, _dt) -> None:
        try:
            if self._odyssey_label is not None:
                self._odyssey_label.text = _format_odyssey(self.ctx)
            if self._switch_pill is not None:
                self._switch_pill.text = _format_switch_pill(self.ctx)
            # Repaint the Switches popup if it's open so a fresh
            # connection / active toggle shows up without the user having
            # to close + reopen. Synchronous polling is good enough — the
            # popup is small and refresh is bounded by _REFRESH_INTERVAL.
            if self._switches_popup is not None and self._switches_popup.is_open:
                self._switches_popup.refresh()
        except Exception:
            # Don't let a transient render error kill the scheduled refresh;
            # Clock.schedule_interval cancels on exception.
            logging.getLogger("SMO").exception("panel refresh failed")

    # ------------------------------------------------------------ popup
    def _open_switches_popup(self, _button) -> None:
        if self._switches_popup is None:
            self._switches_popup = SwitchesPopup(self.ctx)
        self._switches_popup.refresh()
        self._switches_popup.open()


class SwitchesPopup(Popup):
    """Modal popup that lists connected Switches and lets the user pick
    the active one. Opened by clicking the top-bar Switch pill.

    The active Switch is the one bound to the AP slot: its telemetry
    forwards to AP and it receives item replays. Other connected
    Switches are parked with a `KickMsg(reason="inactive")` and the
    user can promote any of them via the buttons below.

    The Advanced section at the bottom exposes the LAN-IP detection
    result and a button to re-run the setup wizard — useful when the
    PC's DHCP lease changed and the Switch mod's baked-in fallback IP
    no longer reaches us.
    """

    def __init__(self, ctx: "SMOContext"):
        self._ctx = ctx
        self._body: BoxLayout | None = None
        self.is_open = False
        super().__init__(
            title="Switches",
            size_hint=(0.9, 0.7),
            auto_dismiss=True,
        )
        self.bind(
            on_open=lambda *_: setattr(self, "is_open", True),
            on_dismiss=lambda *_: setattr(self, "is_open", False),
        )
        self._build()

    def _build(self) -> None:
        outer = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        scroll = ScrollView(do_scroll_x=False, do_scroll_y=True)
        self._body = BoxLayout(
            orientation="vertical",
            spacing=dp(4),
            size_hint_y=None,
        )
        self._body.bind(minimum_height=self._body.setter("height"))
        scroll.add_widget(self._body)
        outer.add_widget(scroll)
        self.content = outer

    def refresh(self) -> None:
        if self._body is None:
            return
        self._body.clear_widgets()
        switches = self._ctx.state.get_switches()
        if not switches:
            self._body.add_widget(Label(
                text=(
                    "[i]No Switches connected.[/i]\n\n"
                    "Boot SMO with the mod installed (Ryujinx or real "
                    "hardware). The mod broadcasts a UDP probe on startup; "
                    "the bridge replies with its LAN IP and the Switch then "
                    "TCP-connects."
                ),
                markup=True,
                halign="left",
                valign="top",
                size_hint_y=None,
                height=dp(120),
                text_size=(self.width - dp(40), None),
            ))
        else:
            for sw in switches:
                self._body.add_widget(_switch_row(sw, self._on_pick))
        # Divider + Advanced row.
        self._body.add_widget(Label(
            text="[b]Advanced[/b]",
            markup=True,
            size_hint_y=None,
            height=dp(24),
            halign="left",
            text_size=(self.width - dp(40), None),
        ))
        self._body.add_widget(Label(
            text=(
                f"[i]Detected LAN IP: {detect_lan_ip()}[/i]\n"
                f"This is what the bridge advertises to discovering Switches "
                f"and what the wizard bakes into the mod as the fallback IP. "
                f"If your DHCP lease changed and a real-Switch deploy can't "
                f"find the bridge after a reboot, re-run setup to rebuild "
                f"the mod with the current address."
            ),
            markup=True,
            halign="left",
            valign="top",
            size_hint_y=None,
            height=dp(80),
            text_size=(self.width - dp(40), None),
        ))
        rerun = Button(
            text="Re-run setup wizard",
            size_hint_y=None,
            height=dp(32),
        )
        rerun.bind(on_release=lambda *_: self._on_rerun_setup())
        self._body.add_widget(rerun)

    def _on_pick(self, device_id: str) -> None:
        ok = self._ctx.set_active_switch(device_id)
        if ok:
            logging.getLogger("SMO").info(
                "active Switch -> %r (selected via Switches popup)", device_id,
            )
        self.refresh()

    def _on_rerun_setup(self) -> None:
        try:
            from worlds.LauncherComponents import launch_subprocess
            from .. import _run_setup_wizard_no_smoap
            launch_subprocess(_run_setup_wizard_no_smoap, name="SMOSetup")
        except Exception:
            logging.getLogger("SMO").exception("failed to launch setup wizard")


def _switch_row(sw: dict, on_pick) -> BoxLayout:
    """One row in the SwitchesPopup body.

    Active Switch: shows "● Active" with a disabled button.
    Inactive Switch: shows a tappable "Make active" button that
    promotes the row via the on_pick(device_id) callback.
    """
    row = BoxLayout(
        orientation="horizontal",
        size_hint_y=None,
        height=dp(36),
        spacing=dp(8),
        padding=(dp(4), dp(2)),
    )
    is_active = bool(sw.get("active"))
    # ASCII-only markers — see _format_switch_pill docstring for why the
    # geometric-shapes block (●/○) renders as tofu under the default
    # Roboto subset Kivy ships. The button text + the bold color carry
    # active state without needing a glyph.
    color = "#4caf50" if is_active else "#888888"
    # Split the row into [prefix | info | button] each with a fixed slot
    # so the device_id stays anchored when the prefix toggles between
    # "Active" and "Idle" (different glyph widths in the proportional
    # default font would otherwise drift the column left/right).
    prefix_text = "[b]Active[/b]" if is_active else "Idle"
    prefix_label = Label(
        text=f"[color={color}]{prefix_text}[/color]",
        markup=True,
        size_hint_x=None,
        width=dp(70),
        halign="left",
        valign="middle",
        text_size=(dp(70), dp(36)),
    )
    info_label = Label(
        text=f"[b]{sw['device_id']}[/b]  [i]{sw['peer_ip']}[/i]",
        markup=True,
        halign="left",
        valign="middle",
        size_hint_x=1,
        text_size=(None, dp(36)),
    )
    row.add_widget(prefix_label)
    row.add_widget(info_label)
    btn = Button(
        text="Active" if is_active else "Make active",
        size_hint_x=None,
        width=dp(140),
        disabled=is_active,
    )
    if not is_active:
        device_id = sw["device_id"]
        btn.bind(on_release=lambda *_: on_pick(device_id))
    row.add_widget(btn)
    return row


def _format_switch_pill(ctx: "SMOContext") -> str:
    """One-line Switch status for the top-bar pill, with markup color.

    Multi-Switch aware: shows the active Switch's device_id and a "+N"
    badge when other Switches are connected as inactive. Click the
    pill to open the selector popup.

    Color carries the state — green = active Switch healthy, orange =
    waiting for HELLO, gray = nothing connected.

    Uses plain ASCII throughout because Kivy's default Roboto subset
    doesn't include the geometric-shapes block (U+25CF / U+25CB render
    as tofu boxes). Color carries the state; no glyph needed.
    """
    sw = ctx.switch
    if sw is None:
        return "[color=#888888]Off[/color]"
    switches = ctx.state.get_switches()
    if not switches:
        return "[color=#ff9800]No Switch[/color]"
    active_name = next(
        (s["device_id"] for s in switches if s.get("active")),
        None,
    )
    extras = sum(1 for s in switches if not s.get("active"))
    if active_name is None:
        # Connected but none active (transient: between disconnect of
        # active and auto-promotion). Show the first as "waiting".
        return f"[color=#ff9800]{switches[0]['device_id']}[/color]"
    badge = f"  [color=#888888](+{extras})[/color]" if extras else ""
    return f"[color=#4caf50]{active_name}[/color]{badge}"


def _format_odyssey(ctx: "SMOContext") -> str:
    """Odyssey tab body — at-a-glance SMO progress, Kivy BBCode markup.

    Intentionally SKIPS:
      * slot / seed / items / checks / deaths — already in the window
        title (slot/seed on connect) and the top progress bar (checks).
      * recent items list — AP already logs received items into the
        Archipelago tab with player + item names; duplicating would be
        noise.
      * data-package / scout-cache debug counts — moved to /smo_status.
    """
    snap = ctx.state.snapshot()
    caps = snap.get("captures_unlocked") or []
    moons_recv = snap.get("moons_received_by_kingdom") or {}
    exit_thresholds = ctx.dp.kingdom_exit_thresholds()

    parts: list[str] = []
    parts.append("[b]Moons by kingdom[/b]    [i]earned / needed to exit[/i]")
    all_k = sorted(set(moons_recv) | set(exit_thresholds), key=_kingdom_sort_key)
    if ctx.is_festival_goal():
        all_k = [k for k in all_k if k not in _HIDDEN_KINGDOMS_FESTIVAL]
    if all_k:
        # Render the table in RobotoMono with width-padded columns so the
        # name colons, earned counts, and exit thresholds line up under
        # Kivy's proportional default font. Width is computed from the
        # observed values so adding a longer-named kingdom doesn't break
        # alignment.
        name_w = max(len(k) for k in all_k)
        recv_w = max(len(str(moons_recv.get(k, 0))) for k in all_k)
        need_w = max(
            (len(str(exit_thresholds[k])) for k in all_k
             if exit_thresholds.get(k) is not None),
            default=1,
        )
        rows: list[str] = []
        for k in all_k:
            recv = moons_recv.get(k, 0)
            need = exit_thresholds.get(k)
            label = f"{k}:".ljust(name_w + 1)
            if need is not None:
                rows.append(f"  {label} {recv:>{recv_w}} / {need:>{need_w}}")
            else:
                rows.append(f"  {label} {recv:>{recv_w}}")
        table = "\n".join(rows)
        parts.append(f"[font=RobotoMono]{table}[/font]" if _MONO_OK else table)
    else:
        parts.append("[i](nothing yet)[/i]")
    parts.append("")
    # Capturesanity OFF: every capture is unlocked from the start, so
    # listing 50 of them is noise. Show a one-liner instead of the full
    # list (which would otherwise fill up with synthetic unlocks the
    # bridge pushes at Connected time).
    if ctx.capturesanity_enabled:
        parts.append("[b]Captures unlocked[/b]")
        parts.append(", ".join(caps) if caps else "[i](none yet)[/i]")
        parts.append("")
    parts.append("[b]DeathLink[/b]: " + ("ENABLED" if ctx.deathlink_enabled else "disabled"))
    return "\n".join(parts)
