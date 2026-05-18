"""Tests for the SNI-style two-stage connect gate in SMOContext.

The user clicks Connect (or types /connect / passes --connect); the AP
websocket dial is deferred until the Switch HELLOs (`on_switch_ready`).
This avoids the old "Connection refused on launch" behavior where the
client auto-dialed `cfg.ap.host` before the user had touched anything,
and matches how SNIClient gates AP connection on SNES presence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# In-tree worktrees may not have the submodule initialized; fall back to
# the main checkout. Walks up looking for any vendor/Archipelago — matches
# how the other AP-dependent tests resolve the dep, but with the extra
# fallback so worktree-based dev still runs the gate test.
def _find_archipelago() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "vendor" / "Archipelago"
        if (cand / "CommonClient.py").exists():
            return cand
        # Worktrees live at <repo>/.claude/worktrees/<name>/ — try the
        # main checkout one level above the worktree root.
        worktrees = parent.parent
        if worktrees.name == "worktrees":
            main_cand = worktrees.parent.parent / "vendor" / "Archipelago"
            if (main_cand / "CommonClient.py").exists():
                return main_cand
    return None


_AP = _find_archipelago()
if _AP is not None and str(_AP) not in sys.path:
    sys.path.insert(0, str(_AP))

try:  # pragma: no cover
    import ModuleUpdate  # type: ignore[import-not-found]
    ModuleUpdate.update_ran = True
except ImportError:
    pass

pytest.importorskip(
    "CommonClient",
    reason="Archipelago checkout not present; init the vendor/Archipelago submodule.",
)

from CommonClient import CommonContext  # noqa: E402
from client.context import SMOContext  # noqa: E402
from client.datapackage import DataPackage  # noqa: E402
from client.maps import CaptureMap, ShineMap  # noqa: E402
from client.state import BridgeState  # noqa: E402


class _StubSwitch:
    """Just enough surface area for the gate: `is_connected()` flip + the
    sends SMOContext might invoke on a fully-up path."""

    def __init__(self, connected: bool = False) -> None:
        self._connected = connected
        self.items: list = []
        self.kills: list = []
        self.prints: list = []
        self.ap_states: list = []

    def is_connected(self) -> bool:
        return self._connected

    async def send_item(self, item) -> None:  # pragma: no cover - unused
        self.items.append(item)

    async def send_kill(self, k) -> None:  # pragma: no cover - unused
        self.kills.append(k)

    async def send_print(self, text: str) -> None:  # pragma: no cover - unused
        self.prints.append(text)

    async def send_ap_state(self, conn: str) -> None:
        self.ap_states.append(conn)


def _make_ctx(switch_connected: bool) -> tuple[SMOContext, BridgeState, _StubSwitch]:
    state = BridgeState()
    ctx = SMOContext(
        server_address=None,
        password=None,
        state=state,
        datapackage=DataPackage(),
        shine_map=ShineMap(),
        capture_map=CaptureMap(),
    )
    sw = _StubSwitch(connected=switch_connected)
    ctx.switch = sw  # type: ignore[assignment]
    return ctx, state, sw


def _stub_super_connect(ctx: SMOContext, sink: list[str]) -> None:
    """Replace CommonContext.connect on the instance with a recorder.
    We bypass the real websocket dial since we're only checking gating."""

    async def fake(address=None):
        sink.append(address)

    # Bind as the parent method so super().connect() reaches it.
    ctx.__class__.__mro__  # noqa: B018 — sanity that MRO has parent

    import types
    # Patch CommonContext.connect at the class level for the duration of
    # the test. Restored implicitly when the test process exits; tests
    # are isolated enough that one fake leaking would be obvious.
    CommonContext.connect = fake  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_connect_defers_when_switch_not_present(monkeypatch):
    ctx, state, _sw = _make_ctx(switch_connected=False)
    super_calls: list[str | None] = []
    monkeypatch.setattr(CommonContext, "connect", lambda self, address=None: _record(super_calls, address))

    await ctx.connect("localhost:38281")

    assert super_calls == []  # gate held — no websocket attempt
    assert ctx._pending_ap_address == "localhost:38281"
    assert ctx.server_address == "localhost:38281"  # GUI prefill persists
    assert state.ap_conn == "waiting_for_switch"


@pytest.mark.asyncio
async def test_connect_proceeds_when_switch_already_present(monkeypatch):
    ctx, state, _sw = _make_ctx(switch_connected=True)
    super_calls: list[str | None] = []
    monkeypatch.setattr(CommonContext, "connect", lambda self, address=None: _record(super_calls, address))

    await ctx.connect("localhost:38281")

    assert super_calls == ["localhost:38281"]
    assert ctx._pending_ap_address is None
    assert ctx.server_address == "localhost:38281"


@pytest.mark.asyncio
async def test_on_switch_ready_promotes_pending(monkeypatch):
    ctx, _state, _sw = _make_ctx(switch_connected=False)
    super_calls: list[str | None] = []
    monkeypatch.setattr(CommonContext, "connect", lambda self, address=None: _record(super_calls, address))

    await ctx.connect("localhost:38281")
    assert super_calls == []

    # Switch HELLOs.
    await ctx._on_switch_ready()

    assert super_calls == ["localhost:38281"]
    assert ctx._pending_ap_address is None


@pytest.mark.asyncio
async def test_on_switch_ready_noop_without_pending(monkeypatch):
    ctx, _state, _sw = _make_ctx(switch_connected=False)
    super_calls: list[str | None] = []
    monkeypatch.setattr(CommonContext, "connect", lambda self, address=None: _record(super_calls, address))

    await ctx._on_switch_ready()

    assert super_calls == []


@pytest.mark.asyncio
async def test_disconnect_clears_pending(monkeypatch):
    ctx, state, _sw = _make_ctx(switch_connected=False)
    monkeypatch.setattr(CommonContext, "connect", lambda self, address=None: _record([], address))
    super_disconnect_calls: list[bool] = []
    monkeypatch.setattr(
        CommonContext,
        "disconnect",
        lambda self, allow_autoreconnect=False: _record_bool(super_disconnect_calls, allow_autoreconnect),
    )

    await ctx.connect("localhost:38281")
    assert ctx._pending_ap_address == "localhost:38281"
    assert state.ap_conn == "waiting_for_switch"

    await ctx.disconnect()

    assert ctx._pending_ap_address is None
    assert state.ap_conn == "disconnected"
    # Pending cancellation also drops Switch HELLO into the no-op branch.
    super_calls: list[str | None] = []
    monkeypatch.setattr(CommonContext, "connect", lambda self, address=None: _record(super_calls, address))
    await ctx._on_switch_ready()
    assert super_calls == []


@pytest.mark.asyncio
async def test_disconnect_from_ready_pushes_ap_state_to_switch(monkeypatch):
    """Disconnect while AP was 'ready' broadcasts 'disconnected' to the
    Switch so the CappyMessenger fires a 'Disconnected from Archipelago'
    bubble on the ready -> disconnected transition."""
    ctx, state, sw = _make_ctx(switch_connected=True)
    monkeypatch.setattr(
        CommonContext,
        "disconnect",
        lambda self, allow_autoreconnect=False: _record_bool([], allow_autoreconnect),
    )

    # Simulate a live AP session — bypass the actual Connected handler.
    state.set_ap_conn("ready")
    sw.ap_states.clear()

    await ctx.disconnect()

    assert state.ap_conn == "disconnected"
    assert sw.ap_states == ["disconnected"]


@pytest.mark.asyncio
async def test_disconnect_when_already_disconnected_is_silent(monkeypatch):
    """A no-op disconnect (already in the 'disconnected' state) must not
    push another ap_state to the Switch — keeps reconnect-loop churn from
    spamming the bubble queue."""
    ctx, state, sw = _make_ctx(switch_connected=True)
    monkeypatch.setattr(
        CommonContext,
        "disconnect",
        lambda self, allow_autoreconnect=False: _record_bool([], allow_autoreconnect),
    )

    assert state.ap_conn == "disconnected"  # default
    sw.ap_states.clear()

    await ctx.disconnect()
    await ctx.disconnect()

    assert sw.ap_states == []  # idempotency: zero pushes


@pytest.mark.asyncio
async def test_disconnect_without_switch_does_not_explode(monkeypatch):
    """No Switch attached -> ap_conn still mutates but no send_ap_state."""
    ctx, state, _sw = _make_ctx(switch_connected=False)
    ctx.switch = None
    monkeypatch.setattr(
        CommonContext,
        "disconnect",
        lambda self, allow_autoreconnect=False: _record_bool([], allow_autoreconnect),
    )
    state.set_ap_conn("ready")

    await ctx.disconnect()

    assert state.ap_conn == "disconnected"


# ---- async lambda helpers (monkeypatch needs a coroutine factory) -----


async def _record(sink, address):
    sink.append(address)


async def _record_bool(sink, val):
    sink.append(val)
