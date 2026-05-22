"""Live AP loopback test.

Spawns a real MultiServer subprocess with a freshly-generated seed,
brings up an SMOContext against it, drives a location check, and
asserts an item arrives back at the (stubbed) Switch socket. This is
the pytest version of the M5.5 manual smoke test — it pins the AP-side
wiring against regression.

Skipped by default. Enable with `SMOAP_LIVE_AP=1` to opt in:
  SMOAP_LIVE_AP=1 .venv/Scripts/python -m pytest -v \\
      apworld/smo_archipelago/tests/test_ap_loopback.py

Requires: Archipelago checkout at vendor/Archipelago (with deps installed),
the forked apworld zip in vendor/Archipelago/custom_worlds/ (run
scripts/install_apworld.py), and the repo-root `.venv` with PyYAML +
setuptools<81 + websockets==13.1.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
AP_ROOT = REPO / "vendor" / "Archipelago"
# Loopback seed yaml ships next to this test; generated artifacts go in
# `seeds/out/` (gitignored).
SEEDS_DIR = Path(__file__).resolve().parent / "seeds"
SEEDS_OUT = SEEDS_DIR / "out"
APWORLD_DATA = REPO / "apworld" / "smo_archipelago" / "data"

# Phase 2: importing SMOContext requires CommonClient on sys.path. The
# live-AP gate already implies the user set up vendor/Archipelago + venv
# deps, so this is safe at module load.
if AP_ROOT.exists() and str(AP_ROOT) not in sys.path:
    sys.path.insert(0, str(AP_ROOT))

try:  # pragma: no cover
    import ModuleUpdate  # type: ignore[import-not-found]
    ModuleUpdate.update_ran = True
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    os.environ.get("SMOAP_LIVE_AP") != "1",
    reason="set SMOAP_LIVE_AP=1 to run the live AP loopback test "
           "(spawns MultiServer subprocess; requires Archipelago deps installed)",
)

# Imported inside the skipif gate via pytest.importorskip so collection
# doesn't fail on hosts without Archipelago.
CommonClient = pytest.importorskip("CommonClient")

from CommonClient import server_loop  # noqa: E402

from client.context import SMOContext  # noqa: E402
from client.datapackage import DataPackage  # noqa: E402
from client.maps import CaptureMap, ShineMap  # noqa: E402
from client.protocol import ItemMsg, KillMsg  # noqa: E402
from client.state import BridgeState  # noqa: E402


class _StubSwitch:
    """Minimum SwitchServer surface used by SMOContext during this test."""

    def __init__(self) -> None:
        self.items: list[ItemMsg] = []
        self.kills: list[KillMsg] = []
        self.prints: list[str] = []
        self.ap_states: list[str] = []

    async def send_item(self, item: ItemMsg) -> None:
        self.items.append(item)

    async def send_kill(self, k: KillMsg) -> None:
        self.kills.append(k)

    async def send_print(self, text: str) -> None:
        self.prints.append(text)

    async def send_ap_state(self, conn: str) -> None:
        self.ap_states.append(conn)

    def set_capturesanity_enabled(self, enabled: bool) -> None:
        # SMOContext._handle_ap_package("Connected", ...) calls this with
        # the slot_data flag. No-op for loopback assertions — the seed
        # under test has capturesanity off, so flipping the flag here is
        # the same as the default state.
        pass

    async def push_capturesanity_replay(self) -> None:
        pass

    def set_deathlink_enabled(self, enabled: bool) -> None:
        # SMOContext._handle_ap_package("Connected", ...) calls this when
        # slot_data ships death_link. The loopback seed has DeathLink off,
        # so the value is the same as the default — no-op.
        pass

    async def push_deathlink_helloack(self) -> None:
        pass

    def set_talkatoo_pool(self, enabled: bool, kingdoms: dict[str, list[str]]) -> None:
        # SMOContext._handle_ap_package("Connected", ...) computes the Talkatoo
        # AP-pool from this slot's locations and pushes it. No-op for the
        # loopback assertions — the seed has Talkatoo% off.
        pass

    async def push_talkatoo_pool(self) -> None:
        pass


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
    # Generate.py takes a directory and loads every .yaml in it. SEEDS_DIR
    # carries multiple fixture YAMLs (smo_loopback.yaml, smo_talkatoo.yaml,
    # ...) all with name "Mario" — pointing Generate.py at the shared dir
    # produces "Names have to be unique" because the other fixtures are
    # siblings. Stage just smo_loopback.yaml into a per-test tempdir so
    # Generate.py sees exactly the one slot the loopback exercises.
    import tempfile
    import shutil
    with tempfile.TemporaryDirectory(prefix="smo_loopback_seed_") as td:
        shutil.copy2(SEEDS_DIR / "smo_loopback.yaml", Path(td) / "Mario.yaml")
        subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "ap_generate.py"),
                "--player_files_path", td,
                "--outputpath", str(SEEDS_OUT),
            ],
            check=True,
        )
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

    state = BridgeState()
    dp = DataPackage(apworld_data_dir=APWORLD_DATA)
    sw = _StubSwitch()

    ctx = SMOContext(
        server_address=f"localhost:{port}",
        password=None,
        state=state,
        datapackage=dp,
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    ctx.auth = "Mario"
    ctx.switch = sw  # type: ignore[assignment]

    server_task = asyncio.create_task(server_loop(ctx), name="ap-server-loop")
    try:
        # Wait for AP context to reach `ready`.
        for _ in range(60):
            if state.ap_conn == "ready":
                break
            await asyncio.sleep(0.1)
        assert state.ap_conn == "ready", \
            f"context never reached ap_conn=ready (got {state.ap_conn!r})"
        assert "ready" in sw.ap_states

        # Datapackage should be hydrated from CommonContext on Connected.
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
            if sw.items:
                break
            await asyncio.sleep(0.1)
        assert sw.items, "no ItemMsg arrived at the Switch within 3s"
        first = sw.items[0]
        # Single-slot loopback: AP routes the placed item back to the same
        # slot that checked the location (sender == self.slot). The bridge
        # collapses `from_` to "" for self-finds so the Switch-side Cappy
        # filter skips the bubble. See SMOContext._handle_ap_package.
        assert first.from_ == ""
        # The item placed at this location is determined by gen RNG (the
        # seed yaml pins no random_seed) and the current item pool yields
        # moon / capture / kingdom / other entries. The round-trip
        # property is what this test pins; the specific kind is incidental.
    finally:
        server_task.cancel()
        try:
            await server_task
        except (asyncio.CancelledError, Exception):
            pass
        await ctx.shutdown()
