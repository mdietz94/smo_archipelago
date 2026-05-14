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
from .protocol import ItemMsg
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
            "  cd C:\\Users\\maxwe\\SMOArchipelago\n"
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
        state: BridgeState,
        datapackage: DataPackage,
        archipelago_path: str | None = None,
    ):
        self.server_addr = server_addr
        self.slot = slot
        self.password = password
        self.items_handling = items_handling
        self._send_item = switch_send_item
        self._send_print = switch_send_print
        self._send_ap_state = switch_send_ap_state
        self._state = state
        self._dp = datapackage
        self._ap_path_hint = archipelago_path
        self._ctx = None  # CommonContext instance, built in start()
        self._server_loop_task: asyncio.Task | None = None

    async def start(self) -> None:
        CommonContext, server_loop, ClientStatus = _import_common_context(self._ap_path_hint)
        bridge = self  # capture for closures

        class _SmoCtx(CommonContext):
            game = SmoApBridgeContext.GAME_NAME
            items_handling = bridge.items_handling

            def __init__(self):
                super().__init__(bridge.server_addr, bridge.password)
                self.auth = bridge.slot

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

    async def report_check(self, kind: str, kingdom: str | None, shine_id: str | None,
                           cap: str | None, slot: int | None) -> None:
        if self._ctx is None:
            log.warning("report_check before AP context started; dropping")
            return
        loc_name = self._reconstruct_location_name(kind, kingdom, shine_id, cap, slot)
        loc_id = self._dp.location_name_to_id.get(loc_name)
        if loc_id is None:
            log.warning("no AP id for location %r (kind=%s)", loc_name, kind)
            return
        if loc_id in self._ctx.locations_checked:
            return
        await self._ctx.send_msgs([{"cmd": "LocationChecks", "locations": [loc_id]}])
        self._ctx.locations_checked.add(loc_id)

    async def report_goal(self) -> None:
        if self._ctx is None:
            return
        from NetUtils import ClientStatus  # type: ignore[import-not-found]
        await self._ctx.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])

    # ---- internal: AP -> Switch ----

    async def _handle_ap_package(self, cmd: str, args: dict, ctx: Any) -> None:
        if cmd == "Connected":
            self._state.set_ap_conn("ready")
            self._state.slot = ctx.auth or self.slot
            await self._send_ap_state("ready")
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
                ))
        elif cmd == "DataPackage":
            data = args.get("data", {}).get("games", {})
            for game_name, package in data.items():
                self._dp.update_from_ap(game_name, package)

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
