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


def _moon_grant_amount(shine_id: str | None) -> int:
    """Mirror of the Switch's moonGrantAmount: Multi-Moons grant 3, all
    other moon items grant 1. Case-sensitive substring match per the
    apworld's exact item naming ("X Kingdom Multi-Moon")."""
    if shine_id and "Multi-Moon" in shine_id:
        return 3
    return 1


class SMOClientCommandProcessor(ClientCommandProcessor):
    """`/`-prefixed commands typed into the Kivy command bar.

    Item injection lives on the AP server console (`/send <slot> <item>`)
    not here — the AP-received path in `_handle_ap_package` is the sole
    producer of ItemMsgs. The commands surviving on this processor are
    debug utilities only: `/label` (visual test of the Channel-A cutscene
    hook), `/smo_status` (read-only tracker state), `/inject_deathlink`
    (synthesize a KillMsg without a second slot).
    """

    def _result_to_output(self, result) -> None:
        """Echo the parser's text result into the command log."""
        if result.error:
            self.output(f"err: {result.error}")
        if result.info:
            for line in result.info.splitlines():
                self.output(line)

    def _cmd_label(self, *args: str) -> bool:
        """Push a Channel-A moon-label string straight to the Switch.

        Visual test for the cutscene-label hook. Collect any moon in
        Ryujinx within ~4s and the cutscene shows your text.
        Example: /label Sent Cap Power Moon -> P3
        """
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        result = parse_command("label " + " ".join(args), ctx.state)
        self._result_to_output(result)
        if result.label is not None and ctx.switch is not None:
            async_start(ctx.switch.send_moon_label(result.label), name="cmd send_moon_label")
            self.output(f"sent moon_label text={result.label.text!r} seq={result.label.seq}")
        elif result.label is not None:
            self.output("(no Switch connected — label discarded)")
        return True

    def _cmd_smo_status(self) -> bool:
        """Show SMOClient tracker state + connection / datapackage debug info.

        Tracker state (received items, checks, captures, kingdoms, last
        item) comes from the pure `parse_command("status")` for unit-test
        coverage. The extra connection / data-package / scout-cache lines
        replace what used to live in the "Connections" tab — they're
        debug info that doesn't deserve a permanent UI surface but is
        still useful to dump on demand.
        """
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        result = parse_command("status", ctx.state)
        self._result_to_output(result)
        # Connection + infra summary (formerly the Connections tab).
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
        DeathLink apply path without a second slot. Replaces the
        `POST /api/test/inject-deathlink` Flask endpoint the web tracker
        had in Phase 2-and-earlier.
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
        """Re-run the first-time-setup wizard.

        Use this when the bridge PC's LAN IP changes (Switch mod has the
        old one baked in), when the apworld's capture list is edited, or
        when you want to switch deploy target between real Switch and
        Ryujinx. Spawns the wizard as a subprocess so SMOClient stays up;
        the wizard's "Done" page does NOT re-launch SMOClient in this
        codepath (the user already has one running).
        """
        from worlds.LauncherComponents import launch_subprocess
        # Defer to a module-level callable that the subprocess machinery
        # can pickle by qualified name. `_run_setup_wizard_no_smoap` is
        # the entry point exported by the apworld root __init__ for
        # exactly this purpose (also used by the .smoap → launch routing
        # when setup hasn't completed yet).
        from .. import _run_setup_wizard_no_smoap
        launch_subprocess(_run_setup_wizard_no_smoap, name="SMOSetup")
        self.output(
            "Launched setup wizard in a new window. SMOClient stays open; "
            "restart it after the wizard finishes if the bridge PC IP "
            "changed (the Switch mod needs a re-deploy + reboot)."
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

    # ----------------------------------------------------------- AP overrides

    async def connect(self, address: str | None = None) -> None:
        """Two-stage gated connect.

        Behaves like CommonContext.connect when the Switch is already up.
        Otherwise stashes the requested address as `_pending_ap_address`,
        logs a "waiting for Switch" line, and defers the websocket dial
        until the Switch HELLOs (see `_on_switch_ready`).

        We still set `self.server_address` synchronously so the GUI's
        Connect bar / Connections tab show the user's chosen target.
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
        """
        if self._pending_ap_address is not None:
            log.info("cancelling pending AP connect (was waiting for Switch)")
            self._pending_ap_address = None
            self.state.set_ap_conn("disconnected")
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

    # M6 phase D — AP data store key for our per-slot outstanding-moon
    # balance. Follows the convention established by every other apworld
    # using set_notify (pokemon_emerald, cvcotm, mlss, pokemon_rb):
    # `{game_short}_{purpose}_{team}_{slot}`.
    def _outstanding_key(self) -> str | None:
        if self.team is None or self.slot is None:
            return None
        return f"smo_outstanding_{self.team}_{self.slot}"

    def _persist_outstanding(self) -> None:
        """Write the current outstanding_by_kingdom to the AP data store.

        Fire-and-forget Set (`want_reply: False`). The AP server's Set
        handler is single-coroutine async (MultiServer.py:2176-2195), so
        back-to-back Sets are linearized — last writer wins, no
        read-modify-write race for a single bridge. Defaults the key to {}
        so a never-before-seen slot doesn't 404.
        """
        key = self._outstanding_key()
        if key is None:
            return
        value = self.state.get_outstanding()
        # Cast keys/values to plain JSON-serializable types (the AP server
        # round-trips through json.dumps).
        payload = {str(k): int(v) for k, v in value.items()}
        asyncio.create_task(self.send_msgs([{
            "cmd": "Set",
            "key": key,
            "default": {},
            "want_reply": False,
            "operations": [{"operation": "replace", "value": payload}],
        }]), name="smo_outstanding_persist")

    def _outstanding_entries_for_switch(self) -> list[OutstandingEntry]:
        """Snapshot of current outstanding balance as wire entries."""
        return [
            OutstandingEntry(kingdom=k, count=int(v))
            for k, v in sorted(self.state.get_outstanding().items())
        ]

    async def _push_outstanding_to_switch(self) -> None:
        """Send the current OutstandingMsg to the Switch (no-op if no switch).

        Called whenever outstanding_by_kingdom changes (grant arrival,
        AP-store retrieval, deposit applied). The Switch overwrites
        ap_moons_kingdom[bit] for each entry.
        """
        if self.switch is None:
            return
        await self.switch.send_outstanding(OutstandingMsg(
            entries=self._outstanding_entries_for_switch(),
        ))

    async def _hydrate_outstanding_from_ap(self) -> None:
        """Pull the current outstanding from `ctx.stored_data` (populated by
        CommonClient's Retrieved/SetReply handler) into BridgeState, then
        push to Switch.

        Handles the initial Connected -> Retrieved cycle and subsequent
        SetReply notifications. None-valued stored_data entries (server has
        no entry yet) hydrate as an empty dict.
        """
        key = self._outstanding_key()
        if key is None:
            return
        raw = self.stored_data.get(key) or {}
        if not isinstance(raw, dict):
            log.warning("AP store entry %r has unexpected type %s; resetting",
                        key, type(raw).__name__)
            raw = {}
        # Coerce values to ints defensively (AP store round-trips through
        # JSON; serialized values come back as whatever JSON typed them).
        coerced: dict[str, int] = {}
        for k, v in raw.items():
            try:
                coerced[str(k)] = int(v)
            except (TypeError, ValueError):
                log.warning("AP store outstanding[%r] = %r is not coercible to int", k, v)
        self.state.replace_outstanding(coerced)
        log.info("[m6-deposit] hydrated outstanding from AP store: %r", coerced)
        await self._push_outstanding_to_switch()

    async def apply_deposit_from_switch(
        self, *, seq: int, kingdom: str, amount: int
    ) -> bool:
        """Handler called by SwitchServer when a DepositMsg lands.

        Returns True if this seq was newly applied (caller can log it as a
        fresh deposit), False if it was idempotent-skipped (re-ack only).
        Either way, the caller MUST send a DepositAckMsg back to the Switch
        so its pending-deposit ring drops the entry.
        """
        if self.state.should_skip_deposit(seq):
            log.info("[m6-deposit] re-ack seq=%d (already applied this session)", seq)
            return False
        new = self.state.apply_deposit(kingdom, amount)
        log.info(
            "[m6-deposit] applied seq=%d kingdom=%s amount=%d new_balance=%d",
            seq, kingdom, amount, new,
        )
        self._persist_outstanding()
        await self._push_outstanding_to_switch()
        return True

    async def _handle_ap_package(self, cmd: str, args: dict) -> None:
        if cmd == "Connected":
            self._populate_datapackage_from_self()
            self.state.set_ap_conn("ready")
            self.state.slot = self.auth or ""
            if self.switch is not None:
                await self.switch.send_ap_state("ready")
            # M6 phase D — subscribe to the outstanding-moon-balance key in
            # the AP data store. `set_notify` sends Get + SetNotify in one
            # batch; the Retrieved reply lands in `ctx.stored_data` BEFORE
            # our on_package override sees the cmd, so the Retrieved arm
            # below can hydrate from `stored_data` directly.
            key = self._outstanding_key()
            if key is not None:
                self.set_notify(key)
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
        elif cmd == "ReceivedItems":
            # M6 phase D — track whether any Moon item was applied this
            # batch so we only do one Set + one OutstandingMsg push at the
            # end (debounces Multi-Moon arrivals + multi-item ReceivedItems
            # packets).
            moon_granted_this_batch = False
            for ni in args.get("items", []):
                item_id = ni.get("item") if isinstance(ni, dict) else getattr(ni, "item", None)
                sender_idx = ni.get("player") if isinstance(ni, dict) else getattr(ni, "player", None)
                flags = ni.get("flags", 0) if isinstance(ni, dict) else getattr(ni, "flags", 0)
                if item_id is None:
                    continue
                name = self.dp.item_id_to_name.get(item_id, f"<unknown:{item_id}>")
                ci = self.dp.classify_item(name)
                ref = ci.to_ref()
                # M6 phase B: stamp hack_name onto the ItemRef now so reconnect
                # replay carries it through SwitchServer without re-resolving.
                if ref.kind == "capture" and ref.cap:
                    ref.hack_name = self.capture_map.cap_to_hack(ref.cap)
                # M-color: thread AP item classification flags through so log
                # lines + future post-collection effects can branch on
                # progression/useful/trap/filler. Stored on ItemRef so the
                # reconnect-replay carries it without recomputation.
                classification = classification_from_flags(int(flags or 0)).value
                ref.classification = classification
                sender_name = self._sender_name(sender_idx)
                self.state.add_received_item(ItemEvent(item=ref, sender=sender_name))
                # M6 phase D — Moon grants bump the per-kingdom outstanding
                # balance. The Switch's ItemMsg path is now a no-op for
                # moons (the per-kingdom counter is driven by OutstandingMsg
                # from the bridge instead), but we still send ItemMsg so the
                # mod's logging, ApState bookkeeping, and Cappy speech
                # filter still fire.
                if ref.kind == ItemKind.MOON.value and ref.kingdom:
                    amount = _moon_grant_amount(ref.shine_id)
                    new = self.state.apply_grant(ref.kingdom, amount)
                    log.info(
                        "[m6-deposit] grant kingdom=%s +%d new_balance=%d (sender=%s)",
                        ref.kingdom, amount, new, sender_name,
                    )
                    moon_granted_this_batch = True
                if self.switch is not None:
                    await self.switch.send_item(ItemMsg(
                        kind=ref.kind,
                        kingdom=ref.kingdom,
                        shine_id=ref.shine_id,
                        cap=ref.cap,
                        name=ref.name,
                        from_=sender_name,
                        hack_name=ref.hack_name,
                        classification=classification,
                    ))
            if moon_granted_this_batch:
                # One Set + one OutstandingMsg covers all Moon items in the
                # batch (debounces ReceivedItems packets that arrive with
                # many items at once — common during reconnect / bulk
                # grants).
                self._persist_outstanding()
                await self._push_outstanding_to_switch()
        elif cmd in ("Retrieved", "SetReply"):
            # M6 phase D — CommonContext's default handler has already
            # written into ctx.stored_data before our on_package runs
            # (CommonClient.py:1099+). Hydrate from there and push to Switch
            # IFF this update was for our outstanding key.
            key = self._outstanding_key()
            if key is None:
                return
            if cmd == "Retrieved":
                if key in args.get("keys", {}):
                    await self._hydrate_outstanding_from_ap()
            else:  # SetReply
                if args.get("key") == key:
                    await self._hydrate_outstanding_from_ap()
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
        # Bounced/DeathLink is handled via on_deathlink (CommonContext routes
        # for us; on_package needn't double-handle).

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

    async def report_goal(self) -> None:
        await self.send_msgs([
            {"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}
        ])

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
        """
        for game in (GAME_NAME, "Archipelago"):
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
