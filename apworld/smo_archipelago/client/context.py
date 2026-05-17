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
from .protocol import ItemKind, ItemMsg, KillMsg, classification_from_flags
from .scout_cache import ScoutCache, request_scout
from .state import BridgeState, ItemEvent

if TYPE_CHECKING:  # pragma: no cover
    from .switch_server import SwitchServer

log = logging.getLogger(__name__)


GAME_NAME = "Spicy Meatball Overdrive"


class SMOClientCommandProcessor(ClientCommandProcessor):
    """`/`-prefixed commands typed into the Kivy command bar.

    The pure parsing for grant/capture/kingdom/label lives in
    `commands.parse_command()` so the wire payload matches what a real
    AP-issued item / Channel-A label would produce. Each `_cmd_*`
    method delegates to it, then schedules the Switch send.
    """

    def _result_to_output(self, result) -> None:
        """Echo the parser's text result into the command log."""
        if result.error:
            self.output(f"err: {result.error}")
        if result.info:
            for line in result.info.splitlines():
                self.output(line)

    def _send_item(self, msg: ItemMsg, sender: str = "command") -> None:
        """Persist into BridgeState (so reconnect-replay survives) and ship
        to the Switch. Mirrors what `_handle_ap_package` does on a real
        ReceivedItems."""
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        from .protocol import ItemRef
        ref = ItemRef(
            kind=msg.kind,
            kingdom=msg.kingdom,
            shine_id=msg.shine_id,
            cap=msg.cap,
            slot=msg.slot,
            name=msg.name,
            hack_name=msg.hack_name,
            # M-color: carry the wire classification onto the persisted
            # ItemRef so HELLO replays restore the same palette routing the
            # original send had.
            classification=msg.classification,
        )
        ctx.state.add_received_item(ItemEvent(item=ref, sender=sender))
        if ctx.switch is not None:
            async_start(ctx.switch.send_item(msg), name="cmd send_item")
            self.output(
                f"sent {msg.kind} kingdom={msg.kingdom!r} "
                f"shine_id={msg.shine_id!r} cap={msg.cap!r}"
            )
        else:
            self.output("(no Switch connected — item recorded but not sent)")

    def _cmd_grant(self, *args: str) -> bool:
        """Inject a kingdom-specific moon item directly to the Switch.

        Example: /grant Cascade Kingdom Power Moon
        """
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        result = parse_command("grant " + " ".join(args), ctx.dp, ctx.state, ctx.capture_map)
        self._result_to_output(result)
        if result.item is not None:
            self._send_item(result.item)
        return True

    def _cmd_capture(self, *args: str) -> bool:
        """Inject a capture-unlock item directly to the Switch.

        Example: /capture Goomba
        """
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        result = parse_command("capture " + " ".join(args), ctx.dp, ctx.state, ctx.capture_map)
        self._result_to_output(result)
        if result.item is not None:
            self._send_item(result.item)
        return True

    def _cmd_kingdom(self, *args: str) -> bool:
        """Inject a kingdom-unlock item directly to the Switch.

        Example: /kingdom Sand
        """
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        result = parse_command("kingdom " + " ".join(args), ctx.dp, ctx.state, ctx.capture_map)
        self._result_to_output(result)
        if result.item is not None:
            self._send_item(result.item)
        return True

    def _cmd_label(self, *args: str) -> bool:
        """Push a Channel-A moon-label string straight to the Switch.

        Visual test for the cutscene-label hook. Collect any moon in
        Ryujinx within ~4s and the cutscene shows your text.
        Example: /label Sent Cap Power Moon -> P3
        """
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        result = parse_command("label " + " ".join(args), ctx.dp, ctx.state, ctx.capture_map)
        self._result_to_output(result)
        if result.label is not None and ctx.switch is not None:
            async_start(ctx.switch.send_moon_label(result.label), name="cmd send_moon_label")
            self.output(f"sent moon_label text={result.label.text!r} seq={result.label.seq}")
        elif result.label is not None:
            self.output("(no Switch connected — label discarded)")
        return True

    def _cmd_smo_status(self) -> bool:
        """Show SMOClient tracker state (items received, checks, captures)."""
        ctx: SMOContext = self.ctx  # type: ignore[assignment]
        result = parse_command("status", ctx.dp, ctx.state, ctx.capture_map)
        self._result_to_output(result)
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

    # ----------------------------------------------------------- AP overrides

    async def server_auth(self, password_requested: bool = False):
        if password_requested and not self.password:
            log.warning("AP server requested a password but none configured")
        await self.get_username()
        await self.send_connect()

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

    async def _handle_ap_package(self, cmd: str, args: dict) -> None:
        if cmd == "Connected":
            self._populate_datapackage_from_self()
            self.state.set_ap_conn("ready")
            self.state.slot = self.auth or ""
            if self.switch is not None:
                await self.switch.send_ap_state("ready")
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
                if self.switch is not None:
                    await self.switch.send_item(ItemMsg(
                        kind=ref.kind,
                        kingdom=ref.kingdom,
                        shine_id=ref.shine_id,
                        cap=ref.cap,
                        slot=ref.slot,
                        name=ref.name,
                        from_=sender_name,
                        hack_name=ref.hack_name,
                        classification=classification,
                    ))
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
        then -> shine_uid via the inverse ShineMap. Captures/shops/kingdoms
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
        slot: int | None = None,
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

        loc_name = self._reconstruct_location_name(kind, kingdom, shine_id, cap, slot)
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
        slot: int | None,
        name: str | None = None,
    ) -> str:
        if kind == "moon" and kingdom and shine_id:
            return f"{kingdom}: {shine_id}"
        if kind == "capture" and cap:
            return f"Capture: {cap}"
        if kind == "shop":
            if name:
                return f"Shop: {name}"
            if shine_id:
                return f"Shop: {shine_id}"
        if name:
            return name
        return f"{kingdom or ''}: {shine_id or cap or slot or ''}".strip(": ")


def _flatten_print_json(data: list) -> str:
    """Concatenate AP PrintJSON 'data' segments into a plain string."""
    out: list[str] = []
    for seg in data:
        if isinstance(seg, dict):
            out.append(seg.get("text", ""))
        else:
            out.append(str(seg))
    return "".join(out)
