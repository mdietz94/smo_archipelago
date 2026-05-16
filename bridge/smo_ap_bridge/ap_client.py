"""Archipelago client.

Subclasses CommonContext from a local Archipelago checkout so we get the full
client framework (websocket, deflate, reconnect, slot data, deathlink).

Archipelago refuses `pip install` (its setup.py blocks it) and vendors its own
deps via ModuleUpdate. Rather than fight that, we treat Archipelago as a git
submodule under `vendor/Archipelago/` and add it to sys.path at startup. The
import is deferred so unit tests can run without it being present.

The bridge __main__ wraps `SmoApBridgeContext.start()` and routes:
  - ReceivedItems  -> SwitchServer.send_item + BridgeState.add_received_item
  - PrintJSON      -> SwitchServer.send_print + BridgeState.add_log
  - server conn    -> SwitchServer.send_ap_state + BridgeState.set_ap_conn
  - Switch checks  -> CommonContext.send_msgs([{"cmd":"LocationChecks","locations":[id...]}])
  - Switch goal    -> CommonContext.send_msgs([{"cmd":"StatusUpdate","status":CLIENT_GOAL}])
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .datapackage import DataPackage
from .display import format_moon_label
from .maps import CaptureMap, ShineMap
from .protocol import ItemMsg, KillMsg, MoonLabelMsg
from .scout_cache import ScoutCache, request_scout
from .state import BridgeState, ItemEvent

if TYPE_CHECKING:  # pragma: no cover
    pass

log = logging.getLogger(__name__)


def _resolve_archipelago_path(explicit: str | None = None) -> Path | None:
    """Find a usable Archipelago checkout.

    Resolution order:
      1. explicit argument (from config.bridge.archipelago_path or --archipelago)
      2. SMOAP_AP_PATH environment variable
      3. <repo>/vendor/Archipelago (the default submodule location)
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.environ.get("SMOAP_AP_PATH")
    if env_path:
        candidates.append(Path(env_path))
    repo_root = Path(__file__).resolve().parent.parent.parent
    candidates.append(repo_root / "vendor" / "Archipelago")
    for c in candidates:
        if (c / "CommonClient.py").is_file():
            return c
    return None


def _import_common_context(ap_path_hint: str | None = None):
    """Late import so the bridge module is usable without Archipelago present."""
    ap_path = _resolve_archipelago_path(ap_path_hint)
    if ap_path is None:
        raise RuntimeError(
            "Archipelago checkout not found. Add it as a submodule:\n"
            "  cd C:\\Users\\maxwe\\Documents\\smo_archipelago\n"
            "  git submodule add https://github.com/ArchipelagoMW/Archipelago.git vendor/Archipelago\n"
            "  git submodule update --init --recursive\n"
            "Or set archipelago_path in config.toml or the SMOAP_AP_PATH env var."
        )

    ap_str = str(ap_path)
    if ap_str not in sys.path:
        sys.path.insert(0, ap_str)
    log.info("using Archipelago checkout at %s", ap_path)

    # Suppress Archipelago's auto-pip step. ModuleUpdate.update() runs at the
    # top of CommonClient and tries to install missing wheels. We already pip-
    # installed our own dependency set; let it skip.
    try:
        import ModuleUpdate  # type: ignore[import-not-found]
        ModuleUpdate.update_ran = True
    except ImportError:
        pass  # newer Archipelago may have refactored this

    try:
        from CommonClient import CommonContext, server_loop  # type: ignore[import-not-found]
        from NetUtils import ClientStatus  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            f"Found Archipelago at {ap_path} but the import failed.\n"
            f"Most likely cause: a missing dependency Archipelago expected to "
            f"auto-install. Install manually with the wheel matching the import "
            f"that failed.\nOriginal error: {e}"
        ) from e
    return CommonContext, server_loop, ClientStatus


class SmoApBridgeContext:
    """Adapter wrapping CommonContext so our wiring is in one place.

    We compose rather than subclass at module-import time so tests can stub
    this out. The real CommonContext subclass is built on demand inside
    start().
    """

    GAME_NAME = "Manual_SMO_archipelago"

    def __init__(
        self,
        server_addr: str,
        slot: str,
        password: str,
        items_handling: int,
        switch_send_item: callable,
        switch_send_print: callable,
        switch_send_ap_state: callable,
        switch_send_kill: callable,
        state: BridgeState,
        datapackage: DataPackage,
        shine_map: ShineMap | None = None,
        capture_map: CaptureMap | None = None,
        archipelago_path: str | None = None,
        deathlink_enabled: bool = False,
        display_enabled: bool = True,
        switch_send_moon_label: callable | None = None,
    ):
        self.server_addr = server_addr
        self.slot = slot
        self.password = password
        self.items_handling = items_handling
        self._send_item = switch_send_item
        self._send_print = switch_send_print
        self._send_ap_state = switch_send_ap_state
        self._send_kill = switch_send_kill
        # Optional: callers that don't ship MoonLabelMsg pass None and we
        # silently drop label sends. ap_client itself never calls this — the
        # actual send site is in SwitchServer._dispatch_check via the
        # compose_moon_label callback wiring. Field is held for symmetry +
        # future use (e.g., reconnect re-label, M6.6 Channel B).
        self._send_moon_label = switch_send_moon_label
        self._state = state
        self._dp = datapackage
        self._shine_map = shine_map or ShineMap()
        self._capture_map = capture_map or CaptureMap()
        self._ap_path_hint = archipelago_path
        self._deathlink_enabled = deathlink_enabled
        self._display_enabled = display_enabled
        self._ctx = None  # CommonContext instance, built in start()
        self._server_loop_task: asyncio.Task | None = None
        # M6 phase A.5 — populated on Connected, queried on each Check.
        self._scout_cache = ScoutCache()

    async def start(self) -> None:
        CommonContext, server_loop, ClientStatus = _import_common_context(self._ap_path_hint)
        bridge = self  # capture for closures

        # Tag set for the AP slot. We add "DeathLink" when enabled so the AP
        # server routes DeathLink Bounce packets to us.
        tags: set[str] = {"AP"}
        if bridge._deathlink_enabled:
            tags.add("DeathLink")

        class _SmoCtx(CommonContext):
            game = SmoApBridgeContext.GAME_NAME
            items_handling = bridge.items_handling

            def __init__(self):
                super().__init__(bridge.server_addr, bridge.password)
                self.auth = bridge.slot
                self.tags = set(tags)

            async def server_auth(self, password_requested: bool = False):
                if password_requested and not self.password:
                    log.warning("AP server requested a password but none configured")
                await self.get_username()
                await self.send_connect()

            def on_package(self, cmd: str, args: dict):
                # Schedule on event loop without blocking.
                asyncio.create_task(bridge._handle_ap_package(cmd, args, self))

            def on_print_json(self, args: dict):
                # Forward chat/print to Switch + tracker.
                text = args.get("text") or _flatten_print_json(args.get("data", []))
                if text:
                    bridge._state.add_log(text)
                    asyncio.create_task(bridge._send_print(text))

        self._ctx = _SmoCtx()
        self._state.set_ap_conn("connecting")
        await self._send_ap_state("connecting")
        # CommonContext's server_loop spins up the websocket and reconnect logic.
        self._server_loop_task = asyncio.create_task(server_loop(self._ctx))

    async def stop(self) -> None:
        if self._ctx is not None:
            await self._ctx.shutdown()
        if self._server_loop_task is not None:
            self._server_loop_task.cancel()
            try:
                await self._server_loop_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ---- inbound from Switch -> AP ----

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
        MoonLabelMsg from it), or None when the check couldn't be
        resolved / forwarded."""
        if self._ctx is None:
            log.warning("report_check before AP context started; dropping")
            return None

        # Resolve raw IDs from the Switch into (kingdom, shine_id) / cap. Raw
        # fields take precedence over legacy.
        if kind == "moon" and (stage_name or object_id):
            res = self._shine_map.resolve(stage_name, object_id, shine_uid)
            if res is None:
                log.warning(
                    "no shine_map entry for stage=%r object=%r uid=%r — "
                    "add an entry to bridge/smo_ap_bridge/data/shine_map.json",
                    stage_name, object_id, shine_uid,
                )
                self._state.add_log(
                    f"[unknown moon] stage={stage_name} object={object_id} uid={shine_uid}"
                )
                return None
            kingdom = res.kingdom
            shine_id = res.shine_id
        elif kind == "capture" and hack_name:
            cap = self._capture_map.resolve(hack_name) or hack_name

        loc_name = self._reconstruct_location_name(kind, kingdom, shine_id, cap, slot)
        loc_id = self._dp.location_name_to_id.get(loc_name)
        if loc_id is None:
            log.warning("no AP id for location %r (kind=%s)", loc_name, kind)
            return None
        if loc_id in self._ctx.locations_checked:
            log.info("check %r (id=%d) already in locations_checked; skipping LocationChecks send",
                     loc_name, loc_id)
            # Still return the loc_id — Channel A's MoonLabelMsg compose path
            # only reads from the scout cache, so it's safe (and friendly) to
            # surface a label for a re-collected moon. The actual LocationCheck
            # send is correctly suppressed by the dedup above.
            return loc_id
        log.info("forwarding LocationCheck %r (id=%d) to AP", loc_name, loc_id)
        await self._ctx.send_msgs([{"cmd": "LocationChecks", "locations": [loc_id]}])
        self._ctx.locations_checked.add(loc_id)
        return loc_id

    def compose_moon_label_for_location(self, loc_id: int) -> str | None:
        """Channel A: look up what the scouted location maps to and format the
        in-game cutscene text. Returns None when:
          * Channel A is disabled in config
          * AP not connected yet
          * The scout cache hasn't seen this location (warmup race, or the
            location isn't ours)
          * The classified item is something we don't know how to label
        Caller (SwitchServer._dispatch_check) sends MoonLabelMsg when non-None.

        Synchronous, no I/O — safe to call from the dispatch hot path.
        """
        if not self._display_enabled or self._ctx is None:
            return None
        scout = self._scout_cache.lookup(loc_id)
        if scout is None:
            return None
        item_name = self._dp.item_id_to_name.get(scout.item_id)
        if not item_name:
            return None
        ci = self._dp.classify_item(item_name)
        recipient = self._sender_name(self._ctx, scout.recipient)
        me_slot = self._ctx.auth or self.slot
        try:
            return format_moon_label(ci, recipient, me_slot)
        except Exception:
            log.exception("format_moon_label failed for loc_id=%d", loc_id)
            return None

    async def report_goal(self) -> None:
        if self._ctx is None:
            return
        from NetUtils import ClientStatus  # type: ignore[import-not-found]
        await self._ctx.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])

    async def report_death(self, ts_ms: int = 0) -> None:
        """Mario died on the Switch. If DeathLink is enabled, send a Bounce
        so other DeathLink-tagged slots take damage too. State tally bumps
        regardless of whether we forward to AP."""
        self._state.bump_death_count()
        if not self._deathlink_enabled:
            return
        if self._ctx is None:
            log.warning("report_death before AP context started; dropping")
            return
        import time
        wall_time = (ts_ms / 1000.0) if ts_ms else time.time()
        await self._ctx.send_msgs([{
            "cmd": "Bounce",
            "tags": ["DeathLink"],
            "data": {
                "time": wall_time,
                "source": self._ctx.auth or self.slot,
                "cause": "Mario died.",
            },
        }])

    # ---- internal: AP -> Switch ----

    async def _handle_ap_package(self, cmd: str, args: dict, ctx: Any) -> None:
        if cmd == "Connected":
            # CommonContext maintains its own item_names / location_names cache,
            # populated either from `DataPackage` packets or from Archipelago's
            # shipped `network_data_package.json`. The latter satisfies the
            # client without ever sending a `DataPackage` packet — which means
            # our `_handle_ap_package("DataPackage", ...)` never fires and our
            # `self._dp` stays empty. Copy from CommonContext on Connected so
            # location-id lookup in `report_check` works regardless of how the
            # data arrived.
            self._populate_datapackage_from_ctx(ctx)
            self._state.set_ap_conn("ready")
            self._state.slot = ctx.auth or self.slot
            await self._send_ap_state("ready")
            # M6 phase A.5 — warm the scout cache so the next moon Mario
            # collects has its label ready before the cutscene fires. Scope
            # to *our* locations only (the bridge's datapackage covers our
            # game). Reset first so reconnect picks up any seed changes.
            if self._display_enabled:
                self._scout_cache.clear()
                # Scope to *our slot's* locations (per AP server). Using the
                # full datapackage instead would request location ids not
                # owned by this slot, which the AP server's LocationScouts
                # handler treats as a hard error (KeyError on the missing
                # entry → drops the websocket connection → bridge reconnects
                # → same scout → same kill → boot loop). missing | checked
                # covers every location the AP server is willing to scout
                # for us.
                loc_ids = list((ctx.missing_locations or set()) |
                               (ctx.checked_locations or set()))
                n = await request_scout(ctx, loc_ids, self._scout_cache)
                if n:
                    log.info("scout: requested %d locations for Channel A warmup", n)
        elif cmd == "RoomInfo":
            seed = args.get("seed_name") or args.get("seed")
            if seed:
                self._state.seed = seed
        elif cmd == "ReceivedItems":
            for ni in args.get("items", []):
                # NetworkItem fields: item, location, player, flags
                item_id = ni.get("item") if isinstance(ni, dict) else getattr(ni, "item", None)
                sender_idx = ni.get("player") if isinstance(ni, dict) else getattr(ni, "player", None)
                if item_id is None:
                    continue
                name = self._dp.item_id_to_name.get(item_id, f"<unknown:{item_id}>")
                ci = self._dp.classify_item(name)
                ref = ci.to_ref()
                # M6 phase B: resolve cap -> hack_name once here so the mod
                # gets the raw SMO identifier ready for addHackDictionary.
                # Stamp onto ItemRef BEFORE add_received_item so reconnect-
                # replay carries the resolved hack_name without re-resolving.
                # Reverse map falls back to identity when absent (works for
                # 1:1 capture names like Goomba/Goomba).
                if ref.kind == "capture" and ref.cap:
                    ref.hack_name = self._capture_map.cap_to_hack(ref.cap)
                sender_name = self._sender_name(ctx, sender_idx)
                evt = ItemEvent(item=ref, sender=sender_name)
                self._state.add_received_item(evt)
                await self._send_item(ItemMsg(
                    kind=ref.kind,
                    kingdom=ref.kingdom,
                    shine_id=ref.shine_id,
                    cap=ref.cap,
                    slot=ref.slot,
                    name=ref.name,
                    from_=sender_name,
                    hack_name=ref.hack_name,
                ))
        elif cmd == "DataPackage":
            data = args.get("data", {}).get("games", {})
            for game_name, package in data.items():
                self._dp.update_from_ap(game_name, package)
        elif cmd == "LocationInfo":
            # M6 phase A.5 — scout cache absorption. Replies come back
            # piecemeal for very large requests, so we accumulate.
            n = self._scout_cache.absorb_location_info(args)
            if n:
                log.debug("scout: absorbed %d location_info entries (cache size=%d)",
                          n, len(self._scout_cache))
        elif cmd == "Bounce":
            # DeathLink (and possibly other bounce-tagged) traffic. Forward to
            # the Switch if it's a DeathLink we didn't originate ourselves.
            tags = args.get("tags") or []
            if "DeathLink" in tags and self._deathlink_enabled:
                data = args.get("data") or {}
                source = str(data.get("source") or "")
                cause = str(data.get("cause") or "")
                own_slot = self._ctx.auth if self._ctx else self.slot
                if source and source == own_slot:
                    return  # don't echo our own death back to ourselves
                self._state.add_log(
                    f"[deathlink in] source={source or '?'} cause={cause or '?'}"
                )
                await self._send_kill(KillMsg(source=source, cause=cause))

    def _populate_datapackage_from_ctx(self, ctx: Any) -> None:
        """Pull item/location name<->id from CommonContext into self._dp."""
        # ctx.item_names / ctx.location_names are NameLookupDicts keyed by
        # game name; each entry is {id: name}.
        for game in (self.GAME_NAME, "Archipelago"):
            try:
                loc_map = ctx.location_names[game]  # {id: name}
                item_map = ctx.item_names[game]
            except (KeyError, TypeError):
                continue
            n_loc = n_item = 0
            for loc_id, loc_name in loc_map.items():
                if isinstance(loc_id, int) and loc_id > 0:
                    self._dp.location_name_to_id[loc_name] = loc_id
                    self._dp.location_id_to_name[loc_id] = loc_name
                    n_loc += 1
            for item_id, item_name in item_map.items():
                if isinstance(item_id, int) and item_id > 0:
                    self._dp.item_name_to_id[item_name] = item_id
                    self._dp.item_id_to_name[item_id] = item_name
                    n_item += 1
            if n_loc or n_item:
                log.info("populated datapackage from ctx for %s: %d items, %d locations",
                         game, n_item, n_loc)

    @staticmethod
    def _sender_name(ctx: Any, player_idx: int | None) -> str:
        if player_idx is None:
            return "self"
        try:
            return ctx.player_names.get(player_idx, str(player_idx))
        except Exception:
            return str(player_idx)

    def _reconstruct_location_name(
        self,
        kind: str,
        kingdom: str | None,
        shine_id: str | None,
        cap: str | None,
        slot: int | None,
        name: str | None = None,
    ) -> str:
        # The Switch sends canonical strings from data/items.json + locations.json.
        # Rebuild the AP location name (e.g. "Cap: Frog-Jumping Above the Fog",
        # "Capture: Goomba", "Shop: Black Top Hat").
        if kind == "moon" and kingdom and shine_id:
            return f"{kingdom}: {shine_id}"
        if kind == "capture" and cap:
            return f"Capture: {cap}"
        if kind == "shop":
            if name:
                return f"Shop: {name}"
            if shine_id:  # alternative carrier
                return f"Shop: {shine_id}"
        if name:
            return name
        return f"{kingdom or ''}: {shine_id or cap or slot or ''}".strip(": ")


def _flatten_print_json(data: list) -> str:
    """Concatenate AP PrintJSON 'data' segments into a plain string."""
    out = []
    for seg in data:
        if isinstance(seg, dict):
            out.append(seg.get("text", ""))
        else:
            out.append(str(seg))
    return "".join(out)
