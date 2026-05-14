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
from .state import BridgeState
from .switch_server import SwitchServer
from .tracker_web import serve_in_thread

log = logging.getLogger("smo_ap_bridge")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="smo-ap-bridge",
                                description="SMO Archipelago bridge")
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

    async def on_check(msg: dict) -> None:
        ap = ap_ctx_holder.get("ctx")
        if ap is not None:
            await ap.report_check(
                kind=msg.get("kind", "moon"),
                kingdom=msg.get("kingdom"),
                shine_id=msg.get("shine_id"),
                cap=msg.get("cap"),
                slot=msg.get("slot"),
            )

    async def on_goal() -> None:
        ap = ap_ctx_holder.get("ctx")
        if ap is not None:
            await ap.report_goal()

    sw = SwitchServer(
        host=cfg.switch.listen_host,
        port=cfg.switch.listen_port,
        state=state,
        on_check=on_check,
        on_goal=on_goal,
    )
    await sw.start()

    if web_tracker:
        serve_in_thread(state, host="0.0.0.0", port=cfg.bridge.web_port)

    ap = SmoApBridgeContext(
        server_addr=f"{cfg.ap.host}:{cfg.ap.port}",
        slot=cfg.ap.slot,
        password=cfg.ap.password,
        items_handling=cfg.ap.items_handling,
        switch_send_item=sw.send_item,
        switch_send_print=sw.send_print,
        switch_send_ap_state=sw.send_ap_state,
        state=state,
        datapackage=dp,
        archipelago_path=cfg.bridge.archipelago_path or None,
    )
    ap_ctx_holder["ctx"] = ap

    try:
        await ap.start()
    except RuntimeError as e:
        log.error("failed to start AP client: %s", e)
        log.error("(bridge will keep the Switch TCP server up; install Archipelago to enable AP)")

    try:
        await sw.serve_forever()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutdown requested")
    finally:
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
