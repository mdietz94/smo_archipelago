"""Bridge entry point.

    python -m smo_ap_bridge --config config.toml
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import __version__, logging_setup
from .ap_client import SmoApBridgeContext
from .config import Config
from .datapackage import DataPackage
from .maps import CaptureMap, ShineMap
from .protocol import KillMsg
from .state import BridgeState
from .switch_server import SwitchServer
from .tracker_web import serve_in_thread

log = logging.getLogger("smo_ap_bridge")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="smo-ap-bridge",
                                description="Spicy Meatball Overdrive bridge")
    p.add_argument("--config", type=Path, default=None,
                   help="Path to config TOML (default: ./config.toml if it exists)")
    p.add_argument("--ap", dest="ap_addr", default=None,
                   help="Override AP server: host:port")
    p.add_argument("--slot", default=None, help="Override AP slot name")
    p.add_argument("--apworld-data", type=Path, default=None,
                   help="Path to apworld/smo_archipelago/data (for category info)")
    p.add_argument("--archipelago", default=None,
                   help="Path to a local Archipelago checkout (default: vendor/Archipelago, "
                        "also reads SMOAP_AP_PATH env var)")
    p.add_argument("--web-tracker", action="store_true", default=None,
                   help="Force-enable web tracker")
    p.add_argument("--no-web-tracker", action="store_true", default=False,
                   help="Force-disable web tracker")
    p.add_argument("--log-level", default=None,
                   help="DEBUG | INFO | WARNING | ERROR")
    p.add_argument("--repl", action="store_true", default=False,
                   help="Enable interactive command REPL on stdin (M6 playtest "
                        "iteration; lets you inject items directly without an AP server)")
    p.add_argument("--version", action="version", version=__version__)
    return p.parse_args(argv)


def _resolve_config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    here = Path("config.toml")
    return here if here.exists() else None


def _resolve_apworld_data(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    # default: ../apworld/smo_archipelago/data relative to this package
    candidate = Path(__file__).resolve().parent.parent.parent / "apworld" / "smo_archipelago" / "data"
    return candidate if candidate.exists() else None


def _resolve_map_path(explicit: str, filename: str) -> Path | None:
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve().parent / "data" / filename
    return here if here.exists() else None


async def run(args: argparse.Namespace) -> int:
    cfg_path = _resolve_config_path(args.config)
    cfg = Config.load(cfg_path)
    web_tracker = args.web_tracker if args.web_tracker is not None else cfg.bridge.web_tracker
    if args.no_web_tracker:
        web_tracker = False
    cfg.apply_overrides(
        ap_addr=args.ap_addr,
        slot=args.slot,
        web_tracker=web_tracker,
        log_level=args.log_level,
        archipelago_path=args.archipelago,
    )
    logging_setup.setup(cfg.bridge.log_level)

    log.info("smo-ap-bridge %s starting", __version__)
    log.info("AP target: %s:%d slot=%r", cfg.ap.host, cfg.ap.port, cfg.ap.slot)
    log.info("Switch listen: %s:%d", cfg.switch.listen_host, cfg.switch.listen_port)

    state = BridgeState()
    state.slot = cfg.ap.slot

    apworld_data = _resolve_apworld_data(args.apworld_data)
    if apworld_data is None:
        log.warning("no apworld data dir found; classification will be best-effort")
    else:
        log.info("loading apworld data from %s", apworld_data)
    dp = DataPackage(apworld_data_dir=apworld_data)

    # Wire Switch server first so it's ready when AP starts pumping items.
    ap_ctx_holder: dict = {}

    async def on_check(msg: dict):
        ap = ap_ctx_holder.get("ctx")
        if ap is None:
            return None
        return await ap.report_check(
            kind=msg.get("kind", "moon"),
            kingdom=msg.get("kingdom"),
            shine_id=msg.get("shine_id"),
            cap=msg.get("cap"),
            slot=msg.get("slot"),
            stage_name=msg.get("stage_name"),
            object_id=msg.get("object_id"),
            shine_uid=msg.get("shine_uid"),
            hack_name=msg.get("hack_name"),
        )

    def compose_label(loc_id: int) -> str | None:
        ap = ap_ctx_holder.get("ctx")
        if ap is None:
            return None
        return ap.compose_moon_label_for_location(loc_id)

    async def on_goal() -> None:
        ap = ap_ctx_holder.get("ctx")
        if ap is not None:
            await ap.report_goal()

    async def on_death(ts_ms: int) -> None:
        ap = ap_ctx_holder.get("ctx")
        if ap is not None:
            await ap.report_death(ts_ms)

    sw = SwitchServer(
        host=cfg.switch.listen_host,
        port=cfg.switch.listen_port,
        state=state,
        on_check=on_check,
        on_goal=on_goal,
        on_death=on_death,
        deathlink_enabled=cfg.deathlink.enabled,
        compose_moon_label=compose_label,
    )
    await sw.start()

    if web_tracker:
        # Cross-thread bridge from Flask (worker thread) to asyncio loop:
        # POST /api/test/inject-deathlink wakes this closure, which schedules
        # send_kill on the running event loop without blocking the request.
        loop = asyncio.get_running_loop()

        def inject_deathlink(source: str, cause: str) -> None:
            log.info("DEBUG injecting fake inbound DeathLink source=%r cause=%r", source, cause)
            asyncio.run_coroutine_threadsafe(
                sw.send_kill(KillMsg(source=source, cause=cause)),
                loop,
            )

        serve_in_thread(
            state,
            host="0.0.0.0",
            port=cfg.bridge.web_port,
            inject_deathlink=inject_deathlink,
        )

    shine_map_path = _resolve_map_path(cfg.bridge.shine_map_path, "shine_map.json")
    capture_map_path = _resolve_map_path(cfg.bridge.capture_map_path, "capture_map.json")
    shine_map = ShineMap(shine_map_path)
    capture_map = CaptureMap(capture_map_path)
    log.info("DeathLink %s", "ENABLED" if cfg.deathlink.enabled else "disabled")

    ap = SmoApBridgeContext(
        server_addr=f"{cfg.ap.host}:{cfg.ap.port}",
        slot=cfg.ap.slot,
        password=cfg.ap.password,
        items_handling=cfg.ap.items_handling,
        switch_send_item=sw.send_item,
        switch_send_print=sw.send_print,
        switch_send_ap_state=sw.send_ap_state,
        switch_send_kill=sw.send_kill,
        switch_send_moon_label=sw.send_moon_label,
        state=state,
        datapackage=dp,
        shine_map=shine_map,
        capture_map=capture_map,
        archipelago_path=cfg.bridge.archipelago_path or None,
        deathlink_enabled=cfg.deathlink.enabled,
    )
    ap_ctx_holder["ctx"] = ap

    try:
        await ap.start()
    except RuntimeError as e:
        log.error("failed to start AP client: %s", e)
        log.error("(bridge will keep the Switch TCP server up; install Archipelago to enable AP)")

    shutdown = asyncio.Event()
    serve_task = asyncio.create_task(sw.serve_forever(), name="switch-serve")
    repl_task: asyncio.Task | None = None
    if args.repl:
        from .repl import run_repl
        repl_task = asyncio.create_task(
            run_repl(sw.send_item, dp, state, shutdown,
                     capture_map=capture_map,
                     send_moon_label=sw.send_moon_label),
            name="repl",
        )

    try:
        if repl_task is None:
            await serve_task
        else:
            # Race: whichever finishes first wins. Normally REPL `quit` sets
            # shutdown; alternatively Ctrl-C raises in the main task.
            done, _ = await asyncio.wait(
                {serve_task, repl_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Surface any task exception immediately.
            for t in done:
                exc = t.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    raise exc
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutdown requested")
    finally:
        shutdown.set()
        for t in (serve_task, repl_task):
            if t is not None and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        await ap.stop()
        await sw.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
