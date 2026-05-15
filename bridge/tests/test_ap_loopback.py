"""Live AP loopback test.

Spawns a real MultiServer subprocess with a freshly-generated seed, brings up
a SmoApBridgeContext against it, drives a location check, and asserts an item
arrives back at the (mocked) Switch socket. This is the pytest version of the
M5.5 manual smoke test — it pins the AP-side wiring against regression.

Skipped by default. Enable with `SMOAP_LIVE_AP=1` to opt in:
  SMOAP_LIVE_AP=1 bridge/.venv/Scripts/python -m pytest -v bridge/tests/test_ap_loopback.py

Requires: Archipelago checkout at vendor/Archipelago (with deps installed),
the forked apworld zip in vendor/Archipelago/custom_worlds/ (run
scripts/install_apworld.py), and bridge venv with PyYAML + setuptools<81 +
websockets==13.1.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from smo_ap_bridge.ap_client import SmoApBridgeContext
from smo_ap_bridge.datapackage import DataPackage
from smo_ap_bridge.maps import CaptureMap, ShineMap
from smo_ap_bridge.protocol import ItemMsg, KillMsg
from smo_ap_bridge.state import BridgeState

REPO = Path(__file__).resolve().parent.parent.parent
AP_ROOT = REPO / "vendor" / "Archipelago"
SEEDS_DIR = REPO / "bridge" / "test_seeds"
SEEDS_OUT = SEEDS_DIR / "out"
APWORLD_DATA = REPO / "apworld" / "smo_archipelago" / "data"


pytestmark = pytest.mark.skipif(
    os.environ.get("SMOAP_LIVE_AP") != "1",
    reason="set SMOAP_LIVE_AP=1 to run the live AP loopback test "
           "(spawns MultiServer subprocess; requires Archipelago deps installed)",
)


def _free_port() -> int:
    """Pick a free TCP port for the local MultiServer to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_seed_file() -> Path:
    """Return the .archipelago seed file, generating one on demand."""
    SEEDS_OUT.mkdir(parents=True, exist_ok=True)
    existing = sorted(SEEDS_OUT.glob("AP_*.archipelago"))
    if existing:
        return existing[-1]

    # No seed; generate one. Ensures the apworld is installed too.
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "install_apworld.py")],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "ap_generate.py"),
            "--player_files_path", str(SEEDS_DIR),
            "--outputpath", str(SEEDS_OUT),
        ],
        check=True,
    )
    # ap_generate.py produces a .zip; extract the .archipelago inside.
    import zipfile
    for z in SEEDS_OUT.glob("AP_*.zip"):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(SEEDS_OUT)
    return next(SEEDS_OUT.glob("AP_*.archipelago"))


@pytest.fixture(scope="session")
def multiserver():
    """Spawn a MultiServer for the session; tear down on exit."""
    seed = _find_seed_file()
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "scripts" / "ap_server.py"),
            "--port", str(port),
            "--loglevel", "info",
            str(seed),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # Wait until the server logs `server listening`.
        deadline = time.time() + 20
        ready = False
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if "server listening" in line:
                ready = True
                break
            if proc.poll() is not None:
                raise RuntimeError(f"MultiServer exited early: rc={proc.returncode}")
        if not ready:
            raise RuntimeError("MultiServer did not log `server listening` within 20s")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def test_loopback_check_returns_item(multiserver):
    port = multiserver

    items_received: list[ItemMsg] = []
    ap_state_buffer: list[str] = []

    async def stub_send_item(m: ItemMsg) -> None:
        items_received.append(m)

    async def noop(*_args, **_kwargs) -> None:
        pass

    async def stub_send_ap_state(s: str) -> None:
        ap_state_buffer.append(s)

    state = BridgeState()
    dp = DataPackage(apworld_data_dir=APWORLD_DATA)

    ctx = SmoApBridgeContext(
        server_addr=f"localhost:{port}",
        slot="Mario",
        password="",
        items_handling=7,
        switch_send_item=stub_send_item,
        switch_send_print=noop,
        switch_send_ap_state=stub_send_ap_state,
        switch_send_kill=noop,
        state=state,
        datapackage=dp,
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )

    await ctx.start()
    try:
        # Wait for the AP context to reach `ready`.
        for _ in range(60):
            if state.ap_conn == "ready":
                break
            await asyncio.sleep(0.1)
        assert state.ap_conn == "ready", \
            f"bridge never reached ap_conn=ready (got {state.ap_conn!r})"
        assert "ready" in ap_state_buffer

        # Datapackage should be hydrated from CommonContext.
        assert "Cap: Frog-Jumping Above the Fog" in dp.location_name_to_id

        # Drive a check. With items_handling=7 + single-slot the server
        # immediately mirrors the placed item back to us.
        await ctx.report_check(
            kind="moon",
            kingdom="Cap",
            shine_id="Frog-Jumping Above the Fog",
        )

        # Wait for the item to arrive on the Switch side.
        for _ in range(30):
            if items_received:
                break
            await asyncio.sleep(0.1)
        assert items_received, "no ItemMsg arrived at the Switch within 3s"
        first = items_received[0]
        assert first.from_ == "Mario"
        # The placed item is some moon variant in this single-slot seed; both
        # ItemKind.MOON and ItemKind.OTHER are acceptable depending on whether
        # the apworld classifies it as a kingdom-specific or generic moon.
        assert first.kind in ("moon", "other"), f"unexpected kind {first.kind!r}"
    finally:
        await ctx.stop()
