"""SMOClient entry point.

Invoked by the Archipelago Launcher's "SMO Client" button via the
Component registration in `apworld/smo_archipelago/__init__.py`. Also
runnable standalone from inside the Archipelago checkout:

    python vendor/Archipelago/Launcher.py "SMO Client" \\
        --connect localhost:38281 --name Mario

Three pieces share the event loop:
  - `server_loop(ctx)` — AP websocket (inherited from CommonClient).
  - The Switch TCP listener (started by `SwitchServer.start()` on port 17777;
    asyncio dispatches `_handle_client` per inbound connection).
  - `ctx.ui.async_run()` — Kivy main loop (when gui_enabled).

All three terminate when `ctx.exit_event` fires (Kivy close, /exit, Ctrl-C).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import Utils
from CommonClient import gui_enabled, get_base_parser, server_loop

from . import __version__, logging_setup
from .config import Config
from .context import SMOContext
from .datapackage import DataPackage
from .maps import CaptureMap, ShineMap
from .protocol import KillMsg
from .state import BridgeState
from .switch_server import SwitchServer

log = logging.getLogger("SMO")


def _resolve_apworld_data() -> Path:
    """apworld data dir holds items.json / locations.json — used by
    DataPackage's classifier. Lives next to client/, which is one up
    from this file."""
    return Path(__file__).resolve().parent.parent / "data"


# Map-resolution helper lives in client/setup_state.py so it can be
# unit-tested without importing CommonClient / Utils (which the test
# fixture intentionally excludes — see conftest.py).
from .setup_state import _resolve_map_path


def _load_settings():
    """Read the SMOSettings group from `~/.archipelago/host.yaml`.

    Falls back to dataclass defaults when the apworld isn't importable
    (offline dev, missing custom_worlds install — neither expected in
    normal Launcher flow but the fallback keeps headless smoke tests
    happy)."""
    try:
        from .. import SMOWorld  # type: ignore[attr-defined]
        return SMOWorld.settings
    except Exception:
        log.warning("could not load SMOSettings; using built-in defaults")

        class _Defaults:
            switch_listen_host = "0.0.0.0"
            switch_listen_port = 17777
            shine_map_path = ""
            capture_map_path = ""
            deathlink_default = False

        return _Defaults()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = get_base_parser(description="Spicy Meatball Overdrive — SMO Archipelago Client")
    p.prog = "SMOClient"
    p.add_argument("--name", default=None, help="AP slot name to connect as")
    p.add_argument("--config", type=Path, default=None,
                   help="Optional TOML config (legacy bridge format)")
    p.add_argument("--switch-host", default=None,
                   help="Bind address for the Switch TCP server (default 0.0.0.0)")
    p.add_argument("--switch-port", type=int, default=None,
                   help="Port for the Switch TCP server (default 17777)")
    p.add_argument("--shine-map", default=None,
                   help="Path to shine_map.json (default: client/data/shine_map.json)")
    p.add_argument("--capture-map", default=None,
                   help="Path to capture_map.json (default: client/data/capture_map.json)")
    p.add_argument("--deathlink", action="store_true", default=False,
                   help="Enable DeathLink")
    p.add_argument("--log-level", default=None, help="DEBUG|INFO|WARNING|ERROR")
    p.add_argument("--version", action="version", version=__version__)
    return p.parse_args(argv)


async def main(args: argparse.Namespace) -> None:
    # Layered configuration: defaults < host.yaml SMOSettings < legacy
    # TOML config (--config, kept for backward compat) < CLI args.
    smo_settings = _load_settings()
    cfg = Config.load(args.config)
    # host.yaml settings overlay onto the defaults BEFORE TOML/CLI so
    # the user-set switch port / deathlink default are honored unless
    # explicitly overridden later in the chain.
    cfg.switch.listen_host = str(smo_settings.switch_listen_host)
    cfg.switch.listen_port = int(smo_settings.switch_listen_port)
    # UserFilePath stringifies to cwd when unset — treat anything pointing
    # at a directory or the AP root as unset so the client/data/ default
    # wins instead of trying to read a json file out of a directory.
    for attr, cfg_field in (("shine_map_path", "shine_map_path"),
                            ("capture_map_path", "capture_map_path")):
        raw = str(getattr(smo_settings, attr, "")) or ""
        if raw and Path(raw).is_file():
            setattr(cfg.bridge, cfg_field, raw)
    if bool(smo_settings.deathlink_default):
        cfg.deathlink.enabled = True

    cfg.apply_overrides(
        ap_addr=args.connect,
        slot=args.name,
        log_level=args.log_level,
    )
    if args.switch_host:
        cfg.switch.listen_host = args.switch_host
    if args.switch_port:
        cfg.switch.listen_port = args.switch_port
    if args.deathlink:
        cfg.deathlink.enabled = True
    # CLI --password beats env beats config.
    if args.password:
        cfg.ap.password = args.password

    logging_setup.setup(cfg.bridge.log_level)
    log.info("SMOClient %s starting", __version__)
    log.info(
        "AP target: %s:%d slot=%r",
        cfg.ap.host, cfg.ap.port, cfg.ap.slot,
    )
    log.info(
        "Switch listen: %s:%d",
        cfg.switch.listen_host, cfg.switch.listen_port,
    )
    log.info("DeathLink: %s", "ENABLED" if cfg.deathlink.enabled else "disabled")

    # ----- Shared services
    state = BridgeState()
    state.slot = cfg.ap.slot

    # DataPackage loads items.json + locations.json category metadata so
    # `classify_item("Cascade Kingdom Power Moon")` returns MOON instead of
    # OTHER. Filesystem path works for loose-source dev; for the Launcher
    # case the apworld is loaded from a .apworld zip whose internal paths
    # don't resolve via Path.exists(), so we fall back to package-based
    # loading via importlib.resources.
    apworld_data = _resolve_apworld_data()
    if apworld_data.exists():
        dp = DataPackage(apworld_data_dir=apworld_data)
    else:
        # __package__ is "worlds.meatballs.client" (zip — Archipelago imports
        # the apworld as `worlds.<zip_stem>`, and our zip is
        # `meatballs.apworld`) or "smo_archipelago.client" (loose source on
        # sys.path — the in-repo folder kept its historical name). Either way
        # the parent is the apworld root that holds data/items.json +
        # data/locations.json.
        apworld_pkg = (__package__ or "client").rsplit(".", 1)[0] or "meatballs"
        log.info(
            "apworld data dir %s not on filesystem; loading from package %r",
            apworld_data, apworld_pkg,
        )
        dp = DataPackage(apworld_package=apworld_pkg)

    # Shine + Capture maps: same loose/zip split as DataPackage above.
    # The zip-shipped versions are loaded via importlib.resources; the
    # filesystem path takes precedence when present (and honors any
    # host.yaml override via cfg.bridge.shine_map_path).
    apworld_pkg = (__package__ or "client").rsplit(".", 1)[0] or "smo"
    shine_fs = _resolve_map_path(cfg.bridge.shine_map_path, "shine_map.json")
    if shine_fs is not None:
        shine_map = ShineMap(shine_fs)
    else:
        shine_map = ShineMap.from_package(apworld_pkg, "shine_map.json")
    capture_fs = _resolve_map_path(cfg.bridge.capture_map_path, "capture_map.json")
    if capture_fs is not None:
        capture_map = CaptureMap(capture_fs)
    else:
        capture_map = CaptureMap.from_package(apworld_pkg, "capture_map.json")

    # ----- Context
    # `server_addr` is the prefill for the GUI's Connect bar (via
    # CommonContext.suggested_address → kvui's connect_layout). When
    # cfg.ap.host is empty (no TOML, no --connect), we pass None so
    # suggested_address falls through to CommonClient's persistent
    # `last_server_address` — i.e. the bar pre-fills with the last server
    # the user successfully connected to, matching every other AP client.
    # We do NOT use it to auto-dial on launch — that's gated below on an
    # explicit `--connect`. Without that gate, every launch would hammer
    # whatever default the user has configured before they've touched
    # anything, which surfaces as "Connection refused" against any server
    # that isn't actually up.
    server_addr = f"{cfg.ap.host}:{cfg.ap.port}" if cfg.ap.host else None
    ctx = SMOContext(
        server_addr,
        cfg.ap.password or None,
        state=state,
        datapackage=dp,
        shine_map=shine_map,
        capture_map=capture_map,
        deathlink_enabled=cfg.deathlink.enabled,
        # M-color: thread the ColorsConfig in so LocationInfo handling can
        # derive per-shine palette indices and push them to the Switch.
        colors_config=cfg.colors,
    )
    ctx.auth = cfg.ap.slot or None

    # ----- SwitchServer
    sw = SwitchServer(
        host=cfg.switch.listen_host,
        port=cfg.switch.listen_port,
        state=state,
        on_check=lambda msg: ctx.report_check(
            kind=msg.get("kind", "moon"),
            kingdom=msg.get("kingdom"),
            shine_id=msg.get("shine_id"),
            cap=msg.get("cap"),
            stage_name=msg.get("stage_name"),
            object_id=msg.get("object_id"),
            shine_uid=msg.get("shine_uid"),
            hack_name=msg.get("hack_name"),
        ),
        on_goal=ctx.report_goal,
        on_death=ctx.report_death,
        deathlink_enabled=cfg.deathlink.enabled,
        compose_moon_label=ctx.compose_moon_label_for_location,
        # SNI-style two-stage gate: SMOContext.connect() defers AP dial
        # until the Switch is up; this callback promotes the pending
        # request the moment HELLO arrives.
        on_switch_ready=ctx._on_switch_ready,
        # M6 phase D: route incoming PaySnapshotMsg through ctx. The
        # handler folds per-kingdom PayShineNum into BridgeState and
        # re-derives outstanding via compute_outstanding(); ctx pushes
        # the resulting OutstandingMsg back to the Switch. The HELLO
        # handler also snapshots current outstanding via
        # get_outstanding_entries, BUT defers when compute_outstanding
        # returns None (no PaySnapshotMsg has arrived yet).
        on_pay_snapshot=ctx.apply_pay_snapshot_from_switch,
        get_outstanding_entries=ctx._outstanding_entries_for_switch,
        # Capturesanity replay: when slot_data.capturesanity == 0 the AP
        # server never sends Capture items, so SwitchServer synthesizes
        # one per known cap at HELLO time to unlock the bitset. Default
        # capturesanity_enabled=True keeps current behavior until
        # SMOContext flips it via set_capturesanity_enabled() on Connected.
        get_all_captures=capture_map.iter_all,
        # M6 phase C reconcile — gate snapshot dispatch on AP datapackage
        # availability so a Switch HELLO that races the AP handshake doesn't
        # drop every snapshot entry as "no AP id for location".
        is_ap_ready=ctx.is_ap_ready,
        # M6 phase C reconcile — synthesize a Cappy bubble for each moon
        # collected during the bridge-offline window so the player learns
        # what AP credited them with (normal cutscene-label substitution
        # missed its window while disconnected).
        build_reconcile_cappy_item=ctx.build_reconcile_cappy_item,
        # M6 phase C reconcile — snapshot of currently-checked AP loc_ids,
        # captured once before each drain to gate Cappy-bubble synthesis on
        # "wasn't there already". Re-replays of known checks skip Cappy.
        get_already_checked_loc_ids=lambda: set(ctx.locations_checked),
        # Version policing: compared against the Switch mod's HELLO mod_ver.
        # Mismatch refuses the connection with a clear hello_ack(ok=false)
        # + an [version mismatch] log line surfaced in the Kivy UI naming
        # both versions, so the user knows which side to update.
        client_ver=__version__,
    )
    ctx.switch = sw
    # M-color: ApClient → SwitchServer palette callback. Wired post-
    # construction so SMOContext doesn't need a reference at __init__.
    ctx.send_shine_scouts = sw.send_shine_scouts

    # ----- Async tasks
    # start() returns once the listening socket is bound; asyncio drives
    # _handle_client in the background per-connection. No separate
    # serve_forever task needed (and having one creates a shutdown race:
    # cancelling it triggers Server.__aexit__ -> wait_closed(), which on
    # Python 3.12+ blocks until every active client connection drops).
    await sw.start()
    # AP connection is opt-in. A Launcher click (which passes no args)
    # leaves AP disconnected — the user clicks Connect / types /connect
    # when ready, and SMOContext.connect() then runs the SNI-style
    # two-stage gate that defers the websocket dial until the Switch is
    # up. The Connect bar is prefilled from server_addr via
    # CommonContext.suggested_address so the user just has to confirm.
    #
    # An explicit `--connect addr` is routed through the same gate so the
    # headless / scripted flow behaves identically — boot the Switch
    # (real or fake) and the queued dial fires.
    if args.connect:
        asyncio.create_task(ctx.connect(), name="initial-connect")

    if gui_enabled:
        ctx.run_gui()
    ctx.run_cli()

    try:
        await ctx.exit_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutdown requested")
    finally:
        # sw.stop() closes the active Switch writer first (so wait_closed
        # can return) and then closes the listener. Order matters: do this
        # before ctx.shutdown() so any tasks still in flight on the asyncio
        # loop have a chance to wind down cleanly.
        await sw.stop()
        await ctx.shutdown()


def launch(*launch_args: str) -> None:
    """Launcher entry point. Called from the Component's `launch_client`."""
    args = parse_args(list(launch_args) or None)
    # Utils.init_logging is the standard hook other in-AP clients call so
    # log files land in the standard place under Archipelago/logs/.
    Utils.init_logging("SMOClient", exception_logger="Client")
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    launch(*sys.argv[1:])
