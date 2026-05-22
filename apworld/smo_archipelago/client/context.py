"""SMOContext — CommonContext subclass that owns both the AP-side websocket
connection (via CommonClient's inherited machinery) and the SwitchServer
(asyncio TCP server the Switch mod connects to over LAN).

Replaces the bridge's `SmoApBridgeContext` composition wrapper. Methods that
were previously bound to the wrapper now live directly on the context.

The merge collapses one process boundary: where the bridge used to be a
standalone `python -m smo_ap_bridge` script that connected to AP on one end
and the Switch on the other, this lives inside the apworld and ships with
the .apworld zip. Launched via the Archipelago Launcher's "SMO Client"
button.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from CommonClient import CommonContext, ClientCommandProcessor
from NetUtils import ClientStatus
from Utils import async_start

from .commands import parse_command
from .config import ColorsConfig
from .datapackage import DataPackage
from .display import format_moon_label
from .maps import CaptureMap, ShineMap
from .protocol import (
    ItemKind,
    ItemMsg,
    KillMsg,
    OutstandingEntry,
    OutstandingMsg,
    classification_from_flags,
)
from .scout_cache import ScoutCache, request_scout
from .state import BridgeState, ItemEvent

if TYPE_CHECKING:  # pragma: no cover
    from .switch_server import SwitchServer

log = logging.getLogger(__name__)


GAME_NAME = "Spicy Meatball Overdrive"


# Mirror of the apworld's SMOWorld.GOAL_TO_VICTORY (defined in
# apworld/smo_archipelago/__init__.py). Used by `_handle_ap_package`
# (Connected) to derive the location name whose check should fire
# ClientGoal. Festival mode: the festival moon is a real in-game
# collectible but its AP address is nulled, so we tee report_goal off
# report_check on this name. Mushroom mode: there's no in-game moon
# for "Arrive in the Mushroom Kingdom" — leave entry out so the Switch's
# credits hook stays the sole producer.
_GOAL_LOC_BY_OPTION: dict[int, str] = {
    1: "Metro: A Traditional Festival!",  # Goal.option_festival
}


# Kingdoms whose outstanding-to-Switch counts get clamped to 0 under the
# festival goal — Metro itself plus every kingdom downstream of it in the
# linear-chain order. Mirrors gui._HIDDEN_KINGDOMS_FESTIVAL (display side).
# Keep both in sync: bridge clamps the wire-protocol number so the Switch's
# OutstandingMsg-consuming logic never thinks the player owns moons past
# Metro, and the UI hides the rows for those same kingdoms.
_FESTIVAL_ZEROED_KINGDOMS = frozenset({
    "Metro", "Snow", "Seaside", "Luncheon", "Ruined", "Bowser's", "Moon",
})


class SMOClientCommandProcessor(ClientCommandProcessor):
    """`/`-prefixed commands typed into the Kivy command bar.

    Item injection lives on the AP server console (`/send <slot> <item>`)
    not here — the AP-received path in `_handle_ap_package` is the sole
    producer of ItemMsgs. The commands surviving on this processor are
    debug utilities only: `/smo_status` (read-only tracker state) and
    `/inject_deathlink` (synthesize a KillMsg without a second slot).
    """

    def _result_to_output(self, result) -> None:
        """Echo the parser's text result into the command log."""
        if result.error:
            self.output(f"err: {result.error}")
        if result.info:
            for line in result.info.splitlines():
                self.output(line)

    def _cmd_smo_status(self) -> bool:
        """Show SMOClient tracker state + connection / datapackage debug info.

        Tracker state (received items, checks, captures, kingdoms, last
        item) comes from the pure `parse_command("status")` for unit-test
        coverage. The extra connection / data-package / scout-cache lines
        are debug info that doesn't deserve a permanent UI surface but is
        still useful to dump on demand.
        """
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        result = parse_command("status", ctx.state)
        self._result_to_output(result)
        # Connection + infra summary.
        snap = ctx.state.snapshot()
        self.output(f"ap_conn={snap.get('ap_conn', '?')} server={ctx.server_address or '—'}")
        if ctx.switch is not None:
            sw_state = "connected" if ctx.switch.is_connected() else "listening"
            self.output(
                f"switch={sw_state} host={getattr(ctx.switch, '_host', '?')}:"
                f"{getattr(ctx.switch, '_port', '?')}"
            )
        else:
            self.output("switch=not_started")
        self.output(
            f"datapackage: items={len(ctx.dp.item_id_to_name)} "
            f"locations={len(ctx.dp.location_id_to_name)} "
            f"scout_cache={len(ctx.scout_cache)} entries"
        )
        self.output(f"deathlink={'on' if ctx.deathlink_enabled else 'off'} "
                    f"deaths_observed={snap.get('death_count', 0)}")
        return True

    def _cmd_inject_deathlink(self, source: str = "TestRig", cause: str = "manual injection") -> bool:
        """Synthesize a fake inbound KillMsg directly to the Switch (debug).

        Bypasses AP entirely — useful for exercising the Switch's inbound
        DeathLink apply path without a second slot.
        """
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        if ctx.switch is None:
            self.output("(no Switch connected — KillMsg discarded)")
            return False
        msg = KillMsg(source=source, cause=cause)
        async_start(ctx.switch.send_kill(msg), name="cmd inject_deathlink")
        self.output(f"sent inbound KillMsg source={source!r} cause={cause!r}")
        return True

    def _cmd_setup(self) -> bool:
        """Open the setup wizard in a new window.

        Covers first-time setup and re-runs alike: when the bridge PC's
        LAN IP changes (Switch mod has the old one baked in), when you've
        updated to a newer apworld and need to rebuild + redeploy the
        Switch mod to match, when the apworld's capture list is edited,
        or when you want to switch deploy target between real Switch and
        Ryujinx. Spawns the wizard as a subprocess so SMOClient stays
        open while it runs.
        """
        from worlds.LauncherComponents import launch_subprocess
        # Defer to a module-level callable that the subprocess machinery
        # can pickle by qualified name. `_run_setup_wizard_no_smoap` is
        # the only sanctioned entry point exported by the apworld root
        # __init__ for the wizard.
        from .. import _run_setup_wizard_no_smoap
        launch_subprocess(_run_setup_wizard_no_smoap, name="SMOSetup")
        self.output(
            "Launched setup wizard in a new window. SMOClient stays open; "
            "restart it after the wizard finishes if you're updating to a "
            "newer apworld or the bridge PC IP changed (the Switch mod "
            "needs a re-deploy + reboot in either case)."
        )
        return True


class SMOContext(CommonContext):
    """AP-side context for the SMO client.

    Owns:
      - The AP websocket connection (inherited from CommonContext).
      - The SwitchServer (asyncio TCP) running on port 17777 by default.
      - The shared BridgeState (mirror of game progress for the tracker tab).
      - The datapackage + scout cache + raw-ID resolution maps.

    Attached, not owned, by main(): `self.switch` — the running SwitchServer
    instance. Set after construction so the server can call back into
    `self.report_check` etc.
    """

    game = GAME_NAME
    items_handling = 0b111  # full handling
    command_processor = SMOClientCommandProcessor

    def __init__(
        self,
        server_address: str | None,
        password: str | None,
        *,
        state: BridgeState,
        datapackage: DataPackage,
        shine_map: ShineMap,
        capture_map: CaptureMap,
        deathlink_enabled: bool = False,
        display_enabled: bool = True,
        colors_config: ColorsConfig | None = None,
    ):
        super().__init__(server_address, password)
        self.tags = {"AP"} | ({"DeathLink"} if deathlink_enabled else set())
        self.state = state
        self.dp = datapackage
        self.shine_map = shine_map
        self.capture_map = capture_map
        self.deathlink_enabled = deathlink_enabled
        # Default True (= captures are AP-gated, current behavior) until
        # the AP Connected handler flips it from slot_data. UI uses this
        # to hide the "Captures unlocked" section when capturesanity is
        # off — listing all 50 synthetic unlocks is noise, not signal.
        self.capturesanity_enabled = True
        # Talkatoo% mode flag, populated from slot_data on AP Connected. When
        # True, the Switch's TalkatooSpeechHook substitutes Talkatoo's speech
        # bubble with up to 3 uncollected AP-pool moons from the current
        # kingdom; SaveLoadHook also pre-marks non-AP moons as collected so
        # they don't spawn. False (default) leaves Talkatoo and the world
        # vanilla.
        self.talkatoo_mode = False
        # Phase 5 (Gap #3): per-kingdom sphere-safe ordered list of moon
        # shine_ids from the apworld. Empty means "no Phase 5 order shipped"
        # — bridge falls back to shipping the full filtered pool (old
        # behavior, can still soft-lock on fresh starts; only kept so an
        # older apworld build that doesn't ship `talkatoo_order` still
        # works). Keyed by AP-form kingdom name ("Cascade", "Bowser's").
        self.talkatoo_order: dict[str, list[str]] = {}
        self.display_enabled = display_enabled
        # M-color: AP-classification -> palette index for in-world moon
        # coloring. Defaults give each non-filler classification a unique
        # palette; the LocationInfo handler derives per-shine palette
        # entries and pushes them to the Switch.
        self.colors = colors_config or ColorsConfig()
        # Channel A scout cache + M-color palette derivation share a single
        # LocationScouts request issued on Connected. The cache holds
        # per-location NetworkItem.flags so both consumers can read them.
        self.scout_cache = ScoutCache()
        # loc_ids the Switch has reported via report_check (natural in-game
        # checks + snapshot replays). Used by _parse_received_item to
        # distinguish gameplay self-finds (suppress Cappy — the in-game
        # moon-get cutscene or capture animation already gave feedback) from
        # user-issued `/send_location` self-finds (bubble — no in-game event
        # ever fired). In-memory only; cross-restart misclassifications are
        # acceptable (at most a few extra bubbles during HELLO replay).
        self._switch_reported_loc_ids: set[int] = set()
        # Wired by main() after SwitchServer construction. Optional because
        # tests construct SMOContext without one.
        self.switch: "SwitchServer | None" = None
        # Optional palette-push callback wired by main() (SwitchServer.
        # send_shine_scouts). None disables the wire push; LocationInfo
        # still updates BridgeState so HELLO replays carry the cache.
        self.send_shine_scouts = None
        # SNI-style two-stage gate: when the user clicks Connect (or types
        # /connect addr) before the Switch has HELLO'd, we stash the
        # requested address here, log a "waiting for Switch" notice, and
        # defer the actual websocket dial until on_switch_ready fires.
        # None means "no pending request" (either nothing requested yet, or
        # already promoted to a real connection).
        self._pending_ap_address: str | None = None
        # Seed's games list from the last RoomInfo we received. Stashed by
        # prepare_data_package (CommonContext calls it with the games set
        # from RoomInfo before server_auth). Used by server_auth to
        # short-circuit when the multiworld doesn't include SMO at all —
        # the AP server would reject our Connect with InvalidGame anyway,
        # but the proactive check gives a clearer error.
        self._roominfo_games: set[str] | None = None
        # Goal-once latch. Set to True the first time `report_goal()` ships
        # a ClientGoal StatusUpdate. The Switch's goal wire message (sent
        # from WorldMapSelectHook on first arrival in Mushroom Kingdom) is
        # the only producer; the latch keeps `goal_sent` snapshot replays
        # from re-firing on every Switch reconnect. AP server is idempotent
        # on ClientGoal anyway — this just keeps logs clean.
        self._goal_reported: bool = False
        # Name of the location whose check should trigger goal. Set on
        # Connected from slot_data["goal"] via GOAL_LOC_BY_OPTION. None
        # means goal is fired exclusively by the Switch's `goal` wire
        # message (mushroom mode: there's no real in-game moon for
        # "Arrive in the Mushroom Kingdom", the credits hook fires it).
        # Festival mode: the festival moon IS a real check, but the
        # apworld nulls its `address` so AP-server-side goal detection
        # via location check doesn't fire — the bridge tees a
        # report_goal() off report_check() instead.
        self._goal_location_name: str | None = None

    # ----------------------------------------------------------- AP overrides

    async def connect(self, address: str | None = None) -> None:
        """Two-stage gated connect.

        Behaves like CommonContext.connect when the Switch is already up.
        Otherwise stashes the requested address as `_pending_ap_address`,
        logs a "waiting for Switch" line, and defers the websocket dial
        until the Switch HELLOs (see `_on_switch_ready`).

        We still set `self.server_address` synchronously so the GUI's
        Connect bar shows the user's chosen target.
        """
        if address is None:
            address = self.server_address
        # Mirror CommonContext.connect's semantics: the user wants this to
        # become the live target. Persist it for the GUI prefill and clear
        # the "user explicitly disconnected" flag so reconnect-loops behave.
        self.server_address = address
        self.disconnected_intentionally = False
        if self.switch is not None and self.switch.is_connected():
            self._pending_ap_address = None
            await super().connect(address)
            return
        # Switch not up — defer the dial. If we already had an AP socket
        # (e.g. user clicked Connect again after the Switch dropped out),
        # tear it down first so the connection state matches what the user
        # actually sees ("waiting for Switch" with no live AP).
        if self.server is not None:
            await super().disconnect(allow_autoreconnect=False)
        self._pending_ap_address = address
        self.state.set_ap_conn("waiting_for_switch")
        log.info(
            "Waiting for Switch connection to connect to the multiworld "
            "at %s (boot Ryujinx / your Switch — the dial fires when the "
            "mod HELLOs).", address or "(no address set)",
        )

    async def disconnect(self, allow_autoreconnect: bool = False) -> None:
        """Clear any pending two-stage gate, then disconnect normally.

        Without this clear, a /disconnect issued while waiting for the
        Switch would leave the pending address armed — the next Switch
        HELLO would then fire an AP dial the user thought they cancelled.

        Also broadcasts the "disconnected" state to the Switch so the
        in-game CappyMessenger fires a "Disconnected from Archipelago"
        bubble on the ready -> disconnected transition. Idempotency-guarded:
        a no-op disconnect (already in "disconnected") does NOT re-emit,
        keeping reconnect-loop churn off the bubble queue.
        """
        if self._pending_ap_address is not None:
            log.info("cancelling pending AP connect (was waiting for Switch)")
            self._pending_ap_address = None
        prev_ap_conn = self.state.ap_conn
        if prev_ap_conn != "disconnected":
            self.state.set_ap_conn("disconnected")
            if self.switch is not None:
                await self.switch.send_ap_state("disconnected")
        await super().disconnect(allow_autoreconnect=allow_autoreconnect)

    async def _on_switch_ready(self) -> None:
        """SwitchServer post-HELLO callback. Promotes a pending AP connect.

        No-op when:
          - no AP connect was queued (user hasn't clicked Connect yet, or
            already connected), or
          - the AP socket is already up (e.g. Switch reconnected mid-session).
        """
        if self._pending_ap_address is None:
            return
        if self.server is not None:
            # Already connected — Switch just reconnected. Clear the pending
            # slot defensively so a future disconnect/reconnect doesn't see
            # stale state.
            self._pending_ap_address = None
            return
        address = self._pending_ap_address
        self._pending_ap_address = None
        log.info("Switch connected; promoting deferred AP connect to %s", address)
        await super().connect(address)

    async def prepare_data_package(self, relevant_games, remote_data_package_checksums):
        """Stash the seed's games list so server_auth can validate against it.

        CommonClient's RoomInfo handler calls this with `set(args["games"])`
        right before server_auth — it's the only place we get a clean view
        of which games the seed actually contains. We hold onto the set so
        the next server_auth can refuse cleanly when SMO isn't one of them.
        """
        self._roominfo_games = set(relevant_games)
        return await super().prepare_data_package(relevant_games, remote_data_package_checksums)

    async def server_auth(self, password_requested: bool = False):
        if password_requested and not self.password:
            log.warning("AP server requested a password but none configured")
        # Proactive game-name guard. The AP server itself rejects mismatched
        # game/slot combos with ConnectionRefused([InvalidGame]) — but the
        # server's error message is generic and only fires after the auth
        # round-trip. If RoomInfo already told us this multiworld doesn't
        # include SMO at all, short-circuit with a clearer message. Slot
        # name typos that DO hit a real SMO slot still go through; the
        # InvalidGame / InvalidSlot overrides below handle those.
        if self._roominfo_games is not None and GAME_NAME not in self._roominfo_games:
            self.disconnected_intentionally = True
            present = ", ".join(sorted(self._roominfo_games)) or "(none)"
            raise Exception(
                f"This multiworld does not include {GAME_NAME!r}. "
                f"Games present: {present}. "
                "SMO Client only works with seeds that contain a "
                f"{GAME_NAME!r} slot — verify the server address."
            )
        await self.get_username()
        await self.send_connect()

    def event_invalid_game(self):
        """Override CommonContext's generic 'Invalid Game' message with one
        that names SMO + the slot we tried, so the user knows which knob to
        turn. Reached when the slot name we sent (`self.auth`) DOES exist in
        the seed but belongs to a different game."""
        raise Exception(
            f"AP server rejected our Connect: a slot named {self.auth!r} "
            f"exists in this multiworld but is for a different game than "
            f"{GAME_NAME!r}. Verify your slot name matches the SMO slot in "
            "your YAML (slot names are case-sensitive)."
        )

    def event_invalid_slot(self):
        """Override CommonContext's generic 'Invalid Slot' message with one
        that names the slot we tried. Reached when no slot by that name
        exists in the seed at all."""
        raise Exception(
            f"AP server rejected our Connect: no slot named {self.auth!r} "
            "exists in this multiworld. Verify your name matches the SMO "
            "slot in your YAML."
        )

    def on_package(self, cmd: str, args: dict) -> None:
        """Schedule SMO-specific package handling.

        `process_server_cmd` already updated CommonContext's internal state
        (item_names, location_names, checked_locations, items_received, etc.)
        BEFORE calling on_package — so by the time we run, the framework
        bookkeeping is done and we can read from self.* freely.

        Most SMO-specific work is async (forwarding to the Switch), so we
        spawn a task rather than blocking the dispatch loop.
        """
        super().on_package(cmd, args)
        asyncio.create_task(self._handle_ap_package(cmd, args))

    def on_print_json(self, args: dict) -> None:
        super().on_print_json(args)
        text = args.get("text") or _flatten_print_json(args.get("data", []))
        if not text:
            return
        self.state.add_log(text)
        if self.switch is not None:
            asyncio.create_task(self.switch.send_print(text))

    def on_deathlink(self, data: dict[str, Any]) -> None:
        """CommonContext invokes this when a DeathLink bounce we're tagged
        for arrives. Forward to the Switch unless we sourced it ourselves.

        Note: CommonContext already filters out our own deaths by comparing
        `data["time"]` against `ctx.last_death_link` in process_server_cmd
        before calling us — so this is belt-and-braces.
        """
        super().on_deathlink(data)
        if not self.deathlink_enabled or self.switch is None:
            return
        source = str(data.get("source") or "")
        cause = str(data.get("cause") or "")
        if source and source == (self.auth or ""):
            return
        self.state.add_log(
            f"[deathlink in] source={source or '?'} cause={cause or '?'}"
        )
        asyncio.create_task(self.switch.send_kill(KillMsg(source=source, cause=cause)))

    def run_gui(self) -> None:
        """Lazy-import the Kivy UI so generation-time imports never pull it.

        Generation imports the apworld's __init__.py on headless hosts that
        have no display server. Kivy at import time on such a host crashes
        long before our code gets a chance to skip it. Defer until run_gui
        is actually called — which only happens inside `launch()` from the
        Launcher subprocess.
        """
        from .gui import SmoManager
        self.ui = SmoManager(self)
        self.ui_task = asyncio.create_task(self.ui.async_run(), name="SmoUI")

    # ----------------------------------------------------------- AP -> Switch

    # M6 phase D — derived per-kingdom outstanding.
    #
    # outstanding[K] = lifetime_received_AP[K] - PayShineNum[K]
    #
    # Lifetime receipts come from BridgeState.moons_received_by_kingdom
    # (rebuilt on every Connected from state.all_received_items, so it
    # survives bridge restart without explicit persistence). PayShineNum
    # comes from the Switch's save via PaySnapshotMsg (shipped on every
    # HELLO + every Odyssey toss). Neither side requires the bridge to
    # persist anything — the deposit-then-crash data-loss class is
    # eliminated because outstanding can't drift from PayShineNum.
    #
    # Until the first PaySnapshotMsg arrives, compute_outstanding returns
    # None and _push_outstanding_to_switch early-returns. The Switch sees
    # its first OutstandingMsg right after its first PaySnapshotMsg the
    # same connection cycle.

    def _outstanding_entries_for_switch(self) -> list[OutstandingEntry]:
        """Snapshot of derived outstanding as wire entries.

        Under the festival goal, Metro and every downstream kingdom are
        clamped to 0 regardless of what the bridge actually received —
        the player is meant to win inside Metro Kingdom and must not
        accumulate enough outstanding for the Switch's M7 Path A gate to
        unlock the Odyssey to Snow. Bridge-side `state.moons_received_by_kingdom`
        keeps tracking real counts in case the user reconnects to a
        non-festival seed without restarting the client.
        """
        outstanding = self.state.compute_outstanding() or {}
        festival = self.is_festival_goal()
        return [
            OutstandingEntry(
                kingdom=k,
                count=0 if festival and k in _FESTIVAL_ZEROED_KINGDOMS else int(v),
            )
            for k, v in sorted(outstanding.items())
        ]

    async def _push_outstanding_to_switch(self) -> None:
        """Send the current OutstandingMsg to the Switch (no-op if no switch
        OR no PaySnapshotMsg has arrived yet).

        Called whenever the inputs to compute_outstanding change: a moon
        item arrives from AP (lifetime_received bumps) or a PaySnapshotMsg
        lands from the Switch. Early-return when compute_outstanding
        returns None — the Switch is on title screen / has no save loaded,
        and pushing `outstanding = lifetime − 0` would credit AP moons
        before the Switch has any opinion about PayShineNum.
        """
        if self.switch is None:
            return
        if self.state.compute_outstanding() is None:
            log.debug(
                "[m6-deposit] suppressing OutstandingMsg (no PaySnapshotMsg "
                "from Switch yet — deferring until save loads)"
            )
            return
        await self.switch.send_outstanding(OutstandingMsg(
            entries=self._outstanding_entries_for_switch(),
        ))

    async def apply_pay_snapshot_from_switch(
        self, totals: dict[str, int]
    ) -> None:
        """Handler called by SwitchServer when a PaySnapshotMsg lands.

        `totals` is keyed by AP-form kingdom name (e.g. "Bowser's"); the
        SwitchServer dispatcher does the Switch→AP translation before
        calling us. Wholesale-replaces the per-kingdom PayShineNum reading
        and re-derives outstanding, then pushes the new OutstandingMsg.
        """
        self.state.apply_pay_snapshot(totals)
        out = self.state.compute_outstanding() or {}
        log.info(
            "[m6-deposit] PaySnapshot applied (%d kingdoms); "
            "derived outstanding=%r",
            len(totals), {k: v for k, v in sorted(out.items()) if v},
        )
        await self._push_outstanding_to_switch()

    async def _process_received_items(self, args: dict) -> None:
        """Handle a ReceivedItems packet.

        Two jobs (M6 phase D simplification — outstanding is derived, so
        there's no rii dedup, no v1 migration, no apply_grant, no persist):
          1. Mirror every item into ``state.received_items`` (the canonical
             history; rebuilds moons_received_by_kingdom on every Connected
             via add_received_item). Mirror dedup by current length so AP's
             reconnect-blip replays don't double-bump lifetime counts.
          2. Forward each NEW item to the Switch via ItemMsg so the mod can
             log arrival, run the Cappy speech filter, and (for captures)
             write into the HackDictionary. Items already in our in-memory
             history are skipped — they would have been forwarded the first
             time they arrived this session.

        Outstanding mutates implicitly: every Moon item bumps
        moons_received_by_kingdom (in add_received_item), and the resulting
        OutstandingMsg is pushed at the end so the Switch sees the updated
        derived value. The push is gated on the bridge having received at
        least one PaySnapshotMsg (otherwise the Switch is on title screen
        and can't act on outstanding anyway).
        """
        items = args.get("items") or []
        ap_index = int(args.get("index", 0) or 0)

        initial_mirror_len = len(self.state.received_items)
        moon_received_this_batch = False

        for i, ni in enumerate(items):
            pos = ap_index + i
            ref, classification, sender_name, cappy_from = self._parse_received_item(ni)
            if ref is None:
                continue
            if pos < initial_mirror_len:
                # Already mirrored earlier in this session (AP re-pushed
                # the full history on a reconnect blip). Skip both mirror
                # and side-effect to avoid double-counting + double-Cappy.
                continue
            self.state.add_received_item(
                ItemEvent(item=ref, sender=sender_name, cappy_from=cappy_from)
            )
            if ref.kind == ItemKind.MOON.value and ref.kingdom:
                moon_received_this_batch = True
            if self.switch is not None:
                await self.switch.send_item(ItemMsg(
                    kind=ref.kind,
                    kingdom=ref.kingdom,
                    shine_id=ref.shine_id,
                    cap=ref.cap,
                    name=ref.name,
                    from_=cappy_from,
                    hack_name=ref.hack_name,
                    classification=classification,
                ))

        if moon_received_this_batch:
            # lifetime_received bumped; re-derive outstanding and push.
            # No-op when no PaySnapshotMsg has arrived yet (Switch on
            # title screen).
            await self._push_outstanding_to_switch()

    def _parse_received_item(self, ni):
        """Decode a NetworkItem (dict or namedtuple) into
        (ItemRef, classification_str, sender_name, cappy_from). Returns
        (None, None, None, None) on a malformed entry.

        ``sender_name`` is the real player name for logging + ItemEvent
        tracking. ``cappy_from`` is the value to pass as ItemMsg.from_:

          * gameplay self-find (sender_idx == self.slot AND the source
            loc_id is in ``self._switch_reported_loc_ids``): empty string.
            The in-game moon-get cutscene or capture animation already gave
            feedback — a Cappy bubble would double up.
          * user-issued `/send_location` self-find (sender_idx ==
            self.slot AND loc_id NOT in the Switch-reported set): the
            ``(self)`` sentinel. CappyMessenger renders it as "Got X!"
            without a from-clause. No in-game event ever fired, so the
            bubble is the only feedback.
          * everyone else (other real players, server-injected /send,
            releases, collects with sender_idx == 0): the real sender_name.

        Switch-side filter treats empty ``from`` as "do not speak".
        """
        item_id = ni.get("item") if isinstance(ni, dict) else getattr(ni, "item", None)
        sender_idx = ni.get("player") if isinstance(ni, dict) else getattr(ni, "player", None)
        location_id = ni.get("location") if isinstance(ni, dict) else getattr(ni, "location", None)
        flags = ni.get("flags", 0) if isinstance(ni, dict) else getattr(ni, "flags", 0)
        if item_id is None:
            return None, None, None, None
        name = self.dp.item_id_to_name.get(item_id, f"<unknown:{item_id}>")
        ci = self.dp.classify_item(name)
        ref = ci.to_ref()
        # M6 phase B: stamp hack_name onto the ItemRef now so reconnect
        # replay carries it through SwitchServer without re-resolving.
        if ref.kind == "capture" and ref.cap:
            ref.hack_name = self.capture_map.cap_to_hack(ref.cap)
        # M-color: thread AP item classification flags through so log lines
        # + future post-collection effects can branch on
        # progression/useful/trap/filler.
        classification = classification_from_flags(int(flags or 0)).value
        ref.classification = classification
        sender_name = self._sender_name(sender_idx)
        if sender_idx is not None and sender_idx == self.slot:
            # Self-routed item. Distinguish gameplay self-find from manual
            # /send_location by checking whether the Switch reported this
            # location. report_check populates the set synchronously before
            # forwarding LocationChecks to AP, so the echo can't race ahead.
            if location_id is not None and location_id in self._switch_reported_loc_ids:
                cappy_from = ""
            else:
                cappy_from = "(self)"
        else:
            cappy_from = sender_name
        return ref, classification, sender_name, cappy_from

    async def _handle_ap_package(self, cmd: str, args: dict) -> None:
        if cmd == "Connected":
            self._populate_datapackage_from_self()
            self.state.set_ap_conn("ready")
            self.state.slot = self.auth or ""
            # Note: no pre-arm for `_goal_reported`. The latch lives only in
            # `report_goal()` and only matters within a single SMOClient
            # process — across reconnects the worst case is one duplicate
            # ClientGoal StatusUpdate from a Switch goal-snapshot replay,
            # which the AP server treats idempotently.
            if self.switch is not None:
                # Push the capturesanity flag BEFORE send_ap_state — the
                # next Switch HELLO will use it to decide whether to
                # synthesize all-captures-unlocked ItemMsgs (default for
                # capturesanity OFF; otherwise AP-granted captures only).
                # slot_data isn't auto-stashed by CommonContext (unlike
                # stored_data, which is); read it straight off the
                # Connected args dict.
                slot_data = args.get("slot_data") or {}
                # Goal-trigger location: when the apworld's victory location
                # is a real in-game moon (festival% mode), checking it on
                # the Switch should fire ClientGoal. The apworld nulls the
                # location's AP address so an AP-server-side detector won't
                # fire — handle it bridge-side instead. Mushroom mode has
                # no in-game moon to collect; the Switch's credits hook
                # fires the goal via its own wire message.
                self._goal_location_name = _GOAL_LOC_BY_OPTION.get(
                    slot_data.get("goal"))
                capturesanity = bool(slot_data.get("capturesanity", 0))
                self.capturesanity_enabled = capturesanity
                self.switch.set_capturesanity_enabled(capturesanity)
                # DeathLink is per-slot: each player opts in via their own
                # YAML `death_link` setting, and the AP server only bounces
                # deaths among slots tagged "DeathLink". In a five-player
                # seed where three opt in, those three share deaths and the
                # other two are inert in both directions. slot_data carries
                # this player's YAML choice, so it's the canonical value —
                # the launch-time knobs (host.yaml deathlink_default /
                # --deathlink / TOML, set in main.py before Connected) are
                # legacy/dev overrides that get superseded the moment the
                # server tells us what this slot actually opted into.
                # Absent key (older apworld build that didn't ship
                # death_link in slot_data) → leave whatever launch picked.
                dl_from_slot = slot_data.get("death_link")
                if dl_from_slot is not None:
                    dl_enabled = bool(dl_from_slot)
                    if dl_enabled != self.deathlink_enabled:
                        log.info("DeathLink: %s (per slot_data)",
                                 "ENABLED" if dl_enabled else "disabled")
                    self.deathlink_enabled = dl_enabled
                    # update_death_link toggles "DeathLink" in self.tags and
                    # sends ConnectUpdate if we're already authed — without
                    # that, the AP server wouldn't bounce DeathLinks to us
                    # even after we flipped our local flag. Conversely,
                    # removing the tag stops the server from sending us
                    # other slots' deaths (which we'd otherwise apply even
                    # though our YAML opted out).
                    await self.update_death_link(dl_enabled)
                    # Mirror to SwitchServer so its HelloAck reflects the new
                    # value on the next HELLO, and push immediately so the
                    # currently-attached Switch doesn't keep dropping inbound
                    # kills (ApState::maybeApplyInboundKill gates on the
                    # value set by hello_ack).
                    self.switch.set_deathlink_enabled(dl_enabled)
                    await self.switch.push_deathlink_helloack()
                # Flush synthetic unlocks NOW for an already-running
                # Switch — the SNI-style two-stage gate means the Switch
                # HELLO usually fires BEFORE this Connected handler, so
                # its initial replay ran with the default (locked) flag
                # and missed the unlocks. push_capturesanity_replay is a
                # no-op when capturesanity is on or no Switch is
                # connected.
                await self.switch.push_capturesanity_replay()
                # Talkatoo% mode: stash the slot flag and ship the per-
                # kingdom AP-pool to the Switch. Same SNI-style gate as
                # capturesanity — the HELLO usually fires before Connected,
                # so the initial replay carried no pool. push_talkatoo_pool
                # is a no-op when the payload isn't set yet.
                self.talkatoo_mode = bool(slot_data.get("talkatoo_mode", 0))
                # Phase 5 (Gap #3): apworld ships a per-kingdom sphere-safe
                # order so Talkatoo never names a moon the player can't
                # reach. When absent (older apworld builds), the bridge
                # falls back to the full filtered pool — see
                # _derive_and_push_talkatoo_pool for both paths.
                raw_order = slot_data.get("talkatoo_order") or {}
                self.talkatoo_order = {
                    str(k): [str(s) for s in (v or [])]
                    for k, v in raw_order.items()
                }
                await self._derive_and_push_talkatoo_pool()
                await self.switch.send_ap_state("ready")
                # M6 phase C — datapackage is now hot. If the Switch's
                # state-snapshot landed during the AP handshake window, its
                # entries were buffered (report_check couldn't resolve loc_ids
                # without dp.location_name_to_id). Drain now so AP learns
                # about anything collected during the disconnect.
                try:
                    await self.switch.drain_pending_snapshot()
                except Exception:
                    log.exception("drain_pending_snapshot failed")
            # M6 phase D — no AP data-store key to subscribe to: outstanding
            # is now derived from (lifetime_received_AP − PayShineNum).
            # lifetime_received comes from state.received_items on every
            # Connected; PayShineNum comes from the Switch's PaySnapshotMsg.
            # The legacy `smo_outstanding_<team>_<slot>` key is abandoned in
            # place — no read, no write, no migration.
            if self.display_enabled or self.colors.enabled:
                # Warm the scout cache so (a) Channel A's moon-get cutscene
                # label is ready before the cutscene fires, and (b) M-color
                # has per-location flags to derive a palette index from.
                # Same scout request serves both consumers. Scope to OUR
                # slot's locations only — the AP server's LocationScouts
                # handler hard-errors on ids it doesn't own (boot-loop
                # trap). missing | checked covers every location AP is
                # willing to scout for us.
                self.scout_cache.clear()
                loc_ids = list(
                    (self.missing_locations or set())
                    | (self.checked_locations or set())
                )
                n = await request_scout(self, loc_ids, self.scout_cache)
                if n:
                    log.info("scout: requested %d locations for warmup", n)
        elif cmd == "RoomInfo":
            seed = args.get("seed_name") or args.get("seed")
            if seed:
                self.state.seed = seed
        elif cmd == "RoomUpdate":
            # Phase 5 (Gap #3): checked_locations may have grown — either
            # because we just sent a LocationChecks for an in-game moon
            # collection or because another player /collect'd or released
            # one of our locations. Recompute cursor windows and re-ship.
            # No-op when Phase 5 isn't active (talkatoo_order empty).
            if self.talkatoo_mode and self.talkatoo_order and self.switch is not None:
                # `args.get("checked_locations")` is a delta list. We only
                # re-ship if at least one of the new checks is in any
                # kingdom's talkatoo_order — otherwise nothing changed.
                # CommonContext has already merged the delta into
                # self.checked_locations by now (super().on_package runs
                # before _handle_ap_package).
                new_checks = set(args.get("checked_locations") or [])
                if not new_checks or self._any_check_in_talkatoo_order(new_checks):
                    await self._derive_and_push_talkatoo_pool()
        elif cmd == "ReceivedItems":
            await self._process_received_items(args)
        elif cmd == "DataPackage":
            data = args.get("data", {}).get("games", {})
            for game_name, package in data.items():
                self.dp.update_from_ap(game_name, package)
        elif cmd == "LocationInfo":
            # Channel A scout cache + M-color palette derivation share a
            # single scout request (see Connected handler). Replies come
            # back piecemeal for very large requests, so we accumulate.
            n = self.scout_cache.absorb_location_info(args)
            if n:
                log.debug(
                    "scout: absorbed %d location_info entries (cache size=%d)",
                    n, len(self.scout_cache),
                )
            # M-color: derive per-shine palette from THIS batch's NetworkItem
            # flags and push to the Switch (idempotent merge on the mod side).
            if self.colors.enabled and self.send_shine_scouts is not None:
                await self._push_palette_for_scout_batch(args)
            # M6 phase C reconcile — snapshot drain queues loc_ids for Cappy
            # bubbles; scouts arrive piecemeal so retry on every batch until
            # the cache catches up (no-op when the pending set is empty).
            if self.switch is not None:
                try:
                    await self.switch.try_fire_reconcile_cappy()
                except Exception:
                    log.exception("try_fire_reconcile_cappy failed")
        # Bounced/DeathLink is handled via on_deathlink (CommonContext routes
        # for us; on_package needn't double-handle).

    # Phase 5 (Gap #3): cursor window size. Mirror of switch-mod's
    # pickThreeUncollectedFromKingdom 3-slot output; capped here so we don't
    # ship surplus to the Switch (TalkatooKingdomPool::kMaxMoons is 96, but
    # the Switch picker only uses up to 3). One slot beyond what the picker
    # consumes would still work, but keeping it at 3 is the contract.
    _TALKATOO_WINDOW = 3

    def _any_check_in_talkatoo_order(self, loc_ids: set[int]) -> bool:
        """True if any of `loc_ids` is one of the moons in
        `self.talkatoo_order`. Used by the RoomUpdate handler to short-
        circuit re-shipping when the delta is unrelated to Talkatoo (e.g.
        a capture-location check, or a /collect for a non-moon)."""
        for kingdom, order in self.talkatoo_order.items():
            for shine_id in order:
                loc_name = f"{kingdom}: {shine_id}"
                loc_id = self.dp.location_name_to_id.get(loc_name)
                if loc_id is not None and loc_id in loc_ids:
                    return True
        return False

    def _compute_talkatoo_cursor(self, kingdom: str) -> int:
        """Phase 5: position in `talkatoo_order[kingdom]` of the next
        uncollected moon, derived from `self.checked_locations`.

        Cursor = smallest index i such that `f"{kingdom}: {order[i]}"` is
        NOT in `self.checked_locations`. Skips already-collected entries
        at the front so the window slides forward as the player collects.
        Robust to out-of-order collection (player collects order[i+2]
        first → cursor stays at i; next visit names order[i] or order[i+1]).
        """
        order = self.talkatoo_order.get(kingdom, [])
        checked = self.checked_locations or set()
        for i, shine_id in enumerate(order):
            loc_name = f"{kingdom}: {shine_id}"
            loc_id = self.dp.location_name_to_id.get(loc_name)
            if loc_id is None or loc_id not in checked:
                return i
        return len(order)

    def _build_talkatoo_pool_phase5(self) -> dict[str, list[str]]:
        """Build the per-kingdom window-of-3 from `self.talkatoo_order`.

        Walks order from `cursor` (smallest-uncollected index) and takes
        the next 3 entries that are NOT in checked_locations. Filtering
        mid-window matters because the player can collect out-of-order:
        Talkatoo names order[cursor+2] in some visit, player collects
        it, cursor stays at cursor (front entry still uncollected) — but
        the collected entry must drop out of the window or Talkatoo will
        keep re-suggesting it on subsequent visits (observed regression
        2026-05-21: re-named 'Chomp Through the Rocks' immediately after
        collection because the slice [cursor:cursor+3] didn't filter).

        Sphere-safety still holds: cursor advancing past front-collected
        entries is monotonic state growth, and skipping mid-window
        collected entries means the player has at least as many items as
        the validator's 'collected order[0..cursor-1]' baseline assumed.
        """
        kingdoms: dict[str, list[str]] = {}
        checked = self.checked_locations or set()
        for kingdom, order in self.talkatoo_order.items():
            cursor = self._compute_talkatoo_cursor(kingdom)
            window: list[str] = []
            for shine_id in order[cursor:]:
                loc_name = f"{kingdom}: {shine_id}"
                loc_id = self.dp.location_name_to_id.get(loc_name)
                if loc_id is not None and loc_id in checked:
                    continue
                window.append(shine_id)
                if len(window) >= self._TALKATOO_WINDOW:
                    break
            if window:
                kingdoms[kingdom] = window
        return kingdoms

    def _build_talkatoo_pool_fallback(self) -> tuple[dict[str, list[str]], int]:
        """Pre-Phase-5 fallback: derive the full filtered pool from
        `missing_locations | checked_locations`. Only reached when the
        apworld didn't ship `talkatoo_order` (older builds). Returns the
        pool and the count of progression-flagged moons dropped."""
        kingdoms: dict[str, list[str]] = {}
        loc_ids = (self.missing_locations or set()) | (self.checked_locations or set())
        progression_filtered = 0
        for loc_id in loc_ids:
            name = self.dp.location_id_to_name.get(loc_id)
            if not name:
                continue
            cl = self.dp.classify_location(name)
            if cl.kind != ItemKind.MOON or not cl.kingdom or not cl.shine_id:
                continue
            # Gap #1: progression-flagged moons (Multi Moons, scenario-advance
            # bosses, Seaside seals, Bowser's chain) bypass the Talkatoo block
            # via isProgressionShine on the Switch side — naming them in
            # Talkatoo's bubble would waste a hint slot on a moon the player
            # gets free anyway.
            if self.dp.is_progression_location(name):
                progression_filtered += 1
                continue
            kingdoms.setdefault(cl.kingdom, []).append(cl.shine_id)
        # Sort each kingdom's list deterministically so the Switch sees a
        # stable order across reconnects.
        for k in kingdoms:
            kingdoms[k].sort()
        return kingdoms, progression_filtered

    async def _derive_and_push_talkatoo_pool(self) -> None:
        """Derive the per-kingdom AP-pool to ship to the Switch.

        Two paths:
          * Phase 5 (preferred): slot_data["talkatoo_order"] gave us a
            per-kingdom sphere-safe ordering. Ship the cursor-window of 3
            for each kingdom (cursor = position of next uncollected moon).
            The Switch's substitute hook picks one from the 3.
          * Pre-Phase-5 fallback: ship the full filtered pool from
            missing+checked. Can soft-lock on fresh starts when 3 random
            unfiltered picks are all gated; Phase 5 fixes that.

        Called from the Connected handler AND from `_handle_ap_package`
        for RoomUpdate (so the window slides forward as the player and
        other players collect locations). Idempotent — each call replaces
        the previous Switch-side pool. No-op when no Switch is attached.
        """
        if self.switch is None:
            return
        if self.talkatoo_order:
            kingdoms = self._build_talkatoo_pool_phase5()
            log.info(
                "[talkatoo] mode=%s phase5 pool=%s",
                self.talkatoo_mode,
                {k: len(v) for k, v in sorted(kingdoms.items())},
            )
        else:
            kingdoms, progression_filtered = self._build_talkatoo_pool_fallback()
            log.info(
                "[talkatoo] mode=%s fallback pool=%s progression_filtered=%d",
                self.talkatoo_mode,
                {k: len(v) for k, v in sorted(kingdoms.items())},
                progression_filtered,
            )
        self.switch.set_talkatoo_pool(self.talkatoo_mode, kingdoms)
        await self.switch.push_talkatoo_pool()

    async def _push_palette_for_scout_batch(self, args: dict) -> None:
        """Derive (shine_uid -> palette) from one LocationInfo packet's
        NetworkItem.flags and push to the Switch. Cumulative: each batch
        merges into BridgeState.shine_palette so HELLO replays carry the
        full picture even when scout replies arrived as several packets.

        AP returns one NetworkItem per scouted location; flags is the
        ItemClassification.as_flag() bits (progression=1, useful=2, trap=4).
        Resolution: location_id -> (kingdom, shine_id) via datapackage,
        then -> shine_uid via the inverse ShineMap. Captures/kingdoms
        in the same batch are skipped (no in-world shine to color).
        """
        locations = args.get("locations") or []
        if not locations:
            return

        batch_palette: dict[int, int] = {}
        unknown_shine = 0
        for ni in locations:
            loc_id = ni.get("location") if isinstance(ni, dict) else getattr(ni, "location", None)
            flags = ni.get("flags", 0) if isinstance(ni, dict) else getattr(ni, "flags", 0)
            if loc_id is None:
                continue
            loc_name = self.dp.location_id_to_name.get(loc_id)
            if not loc_name:
                continue
            cl = self.dp.classify_location(loc_name)
            if cl.kind != ItemKind.MOON:
                continue
            uid = self.shine_map.resolve_uid_by_location(cl.kingdom, cl.shine_id)
            if uid is None:
                unknown_shine += 1
                continue
            classification = classification_from_flags(int(flags or 0))
            batch_palette[uid] = self.colors.for_classification(classification.value)

        if unknown_shine:
            log.warning(
                "[shine-color] %d moon locations had no shine_uid in shine_map "
                "(regenerate via scripts/extract_shine_map.py?)",
                unknown_shine,
            )
        if not batch_palette:
            return

        # Merge into the authoritative bridge-side cache so HELLO replay
        # carries every chunk, then push only this batch to the Switch
        # (the mod merges by shine_uid overwrite — chunk order doesn't matter).
        merged = self.state.all_shine_palette()
        merged.update(batch_palette)
        self.state.set_shine_palette(merged)
        log.info("[shine-color] colored %d moons in this batch (cache=%d)",
                 len(batch_palette), len(merged))
        try:
            await self.send_shine_scouts(batch_palette)
        except Exception:
            log.exception("send_shine_scouts failed for batch of %d entries",
                          len(batch_palette))

    # ----------------------------------------------------------- Switch -> AP

    async def report_check(
        self,
        kind: str,
        kingdom: str | None = None,
        shine_id: str | None = None,
        cap: str | None = None,
        stage_name: str | None = None,
        object_id: str | None = None,
        shine_uid: int | None = None,
        hack_name: str | None = None,
    ) -> int | None:
        """Forward a Switch-side check to AP. Returns the resolved AP
        location_id on success (so SwitchServer can synthesize a
        MoonLabelMsg from it), or None when unresolvable / unforwardable.
        """
        # Resolve raw IDs into canonical names. Raw fields take precedence
        # over legacy canonical fields (the Switch mod sends raw for
        # everything since M4).
        if kind == "moon" and (stage_name or object_id):
            res = self.shine_map.resolve(stage_name, object_id, shine_uid)
            if res is None:
                log.warning(
                    "no shine_map entry for stage=%r object=%r uid=%r — "
                    "add an entry to apworld/smo_archipelago/client/data/shine_map.json",
                    stage_name, object_id, shine_uid,
                )
                self.state.add_log(
                    f"[unknown moon] stage={stage_name} object={object_id} uid={shine_uid}"
                )
                return None
            kingdom = res.kingdom
            shine_id = res.shine_id
        elif kind == "capture" and hack_name:
            cap = self.capture_map.resolve(hack_name) or hack_name

        loc_name = self._reconstruct_location_name(kind, kingdom, shine_id, cap)
        loc_id = self.dp.location_name_to_id.get(loc_name)
        if loc_id is None:
            log.warning("no AP id for location %r (kind=%s)", loc_name, kind)
            return None
        # Mark as Switch-reported BEFORE the dedup early-return below so a
        # snapshot replay of an already-checked loc still counts. This set
        # gates the Cappy-suppression logic in _parse_received_item: the AP
        # echo of a check the Switch reported is a self-find (the in-game
        # event already gave feedback); the AP echo of a `/send_location`
        # the user typed isn't in the set and gets a bubble.
        self._switch_reported_loc_ids.add(loc_id)
        if loc_id in self.locations_checked:
            log.info(
                "check %r (id=%d) already in locations_checked; skipping LocationChecks send",
                loc_name, loc_id,
            )
            # Still return the loc_id so Channel A can label a re-collected
            # moon. send-dedup happens above.
            return loc_id
        log.info("forwarding LocationCheck %r (id=%d) to AP", loc_name, loc_id)
        await self.send_msgs([{"cmd": "LocationChecks", "locations": [loc_id]}])
        self.locations_checked.add(loc_id)
        # If this check IS the goal trigger (festival mode), fire ClientGoal
        # too — AP server-side detection can't run because the apworld nulls
        # the victory location's address, so the loc_id we just sent isn't
        # in our slot's missing_locations and the server won't follow up.
        if self._goal_location_name is not None and loc_name == self._goal_location_name:
            await self.report_goal()
        return loc_id

    def compose_moon_label_for_location(self, loc_id: int) -> str | None:
        """Channel A: synthesize the in-game cutscene label for `loc_id`.

        Synchronous, no I/O — safe to call from SwitchServer's dispatch
        hot path. Returns None when:
          * Channel A disabled
          * Scout cache miss (warmup race or location not ours)
          * Classified item we don't know how to label
        """
        if not self.display_enabled:
            return None
        scout = self.scout_cache.lookup(loc_id)
        if scout is None:
            return None
        item_name = self.dp.item_id_to_name.get(scout.item_id)
        if not item_name:
            return None
        ci = self.dp.classify_item(item_name)
        recipient = self._sender_name(scout.recipient)
        try:
            return format_moon_label(ci, recipient, self.auth)
        except Exception:
            log.exception("format_moon_label failed for loc_id=%d", loc_id)
            return None

    def is_festival_goal(self) -> bool:
        """True when slot_data.goal indicates festival mode. The bridge
        captures this on Connected to drive UI-only display filters (the
        Odyssey tab hides Metro+ kingdoms in festival mode) and the
        bridge-side ClientGoal trigger when the festival moon is checked.
        """
        return self._goal_location_name == "Metro: A Traditional Festival!"

    def is_ap_ready(self) -> bool:
        """True iff the AP datapackage has been loaded — the only state
        report_check needs to resolve loc_ids. Mirrors `state.ap_conn=='ready'`
        which is set in `_handle_ap_package(cmd='Connected')` right after the
        datapackage is hydrated from self.
        """
        return self.state.ap_conn == "ready"

    def build_reconcile_cappy_item(self, loc_id: int) -> "ItemMsg | None":
        """M6 phase C reconcile — build a Cappy-bubble ItemMsg for a moon or
        capture that was collected during a Switch-online / bridge-offline
        window.

        Returns None when:
          * scout for this loc_id hasn't been absorbed yet (caller retries
            via SwitchServer.try_fire_reconcile_cappy on each LocationInfo)
          * the item at this location is Kingdom/Other (no useful surface)
          * the item is routed to another player (their bridge will print
            its own notification; we'd be double-announcing)

        Both moons and captures route to the speech bubble because the
        in-game cutscene-label / Capture-List notification missed its
        window while the bridge was offline. The Switch-side formatter
        treats `from_ == "(offline)"` as a sentinel and produces
        "Got <name>!" with no "from <sender>" clause — same form for both
        item kinds.

        For captures we also populate hack_name (via the reverse CaptureMap)
        — matches the live ReceivedItems path so the Switch can re-apply
        idempotently if needed. Synthetic Item-apply is harmless: moons are
        observation-only (OutstandingMsg is authoritative for balance),
        captures probe isExistInHackDictionary — so the duplicate vs the
        natural ReceivedItems-driven ItemMsg cannot double-count.
        """
        scout = self.scout_cache.lookup(loc_id)
        if scout is None:
            return None
        # Self-routed only: another player's bridge already prints/announces
        # the item for them; firing a Cappy bubble for us would be confusing.
        if self.slot is None or scout.recipient != self.slot:
            return None
        item_name = self.dp.item_id_to_name.get(scout.item_id)
        if not item_name:
            return None
        ci = self.dp.classify_item(item_name)
        if ci.kind not in (ItemKind.MOON, ItemKind.CAPTURE):
            return None
        ref = ci.to_ref()
        # Mirror _parse_received_item: for captures, resolve the SMO-internal
        # hack_name from the apworld cap name so the Switch's existing
        # add-to-hack-dictionary path can run without a separate lookup.
        hack_name: str | None = None
        if ref.kind == "capture" and ref.cap:
            hack_name = self.capture_map.cap_to_hack(ref.cap)
        return ItemMsg(
            kind=ref.kind,
            kingdom=ref.kingdom,
            shine_id=ref.shine_id,
            cap=ref.cap,
            name=ref.name,
            from_="(offline)",
            hack_name=hack_name,
            classification=ref.classification,
        )

    async def report_goal(self) -> None:
        """Mark this slot as goaled with the AP server.

        Wired from `SwitchServer._on_goal` (fires on a `goal` wire message
        from the Switch + on a snapshot's `goal_reached` meta flag). The
        Switch produces both signals on the same condition: Mario's first
        arrival in Mushroom Kingdom, captured in `ApState::goal_sent`.

        Latched via `_goal_reported` for log hygiene — snapshot replays
        across Switch reconnects would otherwise reprint "reporting goal"
        on every (re)connect. AP server is idempotent on ClientGoal
        regardless.
        """
        if self._goal_reported:
            return
        self._goal_reported = True
        log.info("reporting goal to AP")
        await self.send_msgs([
            {"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}
        ])

    def set_active_switch(self, device_id: str | None) -> bool:
        """Promote `device_id` to active (or unbind if None). Shim for
        GUI button handlers — schedules the actual work on the asyncio
        loop via `async_start` (same pattern as `_cmd_inject_deathlink`).

        Returns True when scheduling succeeded. The async task does the
        real work (Kick the old active, Activate + replay the new); the
        GUI repaints when SwitchServer's `set_on_switches_changed`
        callback fires after the swap completes.
        """
        if self.switch is None:
            return False
        async_start(
            self.switch.set_active(device_id), name="set_active_switch",
        )
        return True

    async def report_death(self, ts_ms: int = 0) -> None:
        """Mario died. State tally bumps unconditionally; DeathLink bounce
        only fires if enabled.
        """
        self.state.bump_death_count()
        if not self.deathlink_enabled:
            return
        import time
        wall_time = (ts_ms / 1000.0) if ts_ms else time.time()
        await self.send_msgs([{
            "cmd": "Bounce",
            "tags": ["DeathLink"],
            "data": {
                "time": wall_time,
                "source": self.auth or "",
                "cause": "Mario died.",
            },
        }])

    # -------------------------------------------------------------- helpers

    def _populate_datapackage_from_self(self) -> None:
        """Copy AP item/location name<->id into self.dp.

        CommonContext maintains item_names/location_names from its built-in
        DataPackage handling. Mirror those into our SMO-specific
        DataPackage so report_check can resolve canonical names to ids
        without re-implementing AP's lookups.

        Iterates every game CommonContext knows about (not just our own),
        because Channel A's `compose_moon_label_for_location` needs to
        resolve the *recipient's* item name when our location holds an
        item destined for another player's game — without the cross-game
        ids in `dp.item_id_to_name`, the cutscene label falls back to
        vanilla SMO text.
        """
        games = set(self.item_names) | set(self.location_names)
        for game in games:
            try:
                loc_map = self.location_names[game]  # {id: name}
                item_map = self.item_names[game]
            except (KeyError, TypeError):
                continue
            n_loc = n_item = 0
            for loc_id, loc_name in loc_map.items():
                if isinstance(loc_id, int) and loc_id > 0:
                    self.dp.location_name_to_id[loc_name] = loc_id
                    self.dp.location_id_to_name[loc_id] = loc_name
                    n_loc += 1
            for item_id, item_name in item_map.items():
                if isinstance(item_id, int) and item_id > 0:
                    self.dp.item_name_to_id[item_name] = item_id
                    self.dp.item_id_to_name[item_id] = item_name
                    n_item += 1
            if n_loc or n_item:
                log.info(
                    "populated datapackage for %s: %d items, %d locations",
                    game, n_item, n_loc,
                )

    def _sender_name(self, player_idx: int | None) -> str:
        if player_idx is None:
            return "self"
        try:
            return self.player_names.get(player_idx, str(player_idx))
        except Exception:
            return str(player_idx)

    @staticmethod
    def _reconstruct_location_name(
        kind: str,
        kingdom: str | None,
        shine_id: str | None,
        cap: str | None,
        name: str | None = None,
    ) -> str:
        if kind == "moon" and kingdom and shine_id:
            return f"{kingdom}: {shine_id}"
        if kind == "capture" and cap:
            return f"Capture: {cap}"
        if name:
            return name
        return f"{kingdom or ''}: {shine_id or cap or ''}".strip(": ")


def _flatten_print_json(data: list) -> str:
    """Concatenate AP PrintJSON 'data' segments into a plain string."""
    out: list[str] = []
    for seg in data:
        if isinstance(seg, dict):
            out.append(seg.get("text", ""))
        else:
            out.append(str(seg))
    return "".join(out)
