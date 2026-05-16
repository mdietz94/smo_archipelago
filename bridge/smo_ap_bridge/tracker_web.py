"""Web tracker (M5).

Minimal Flask app that polls BridgeState. The HTML auto-refreshes every 1.5s
via a tiny vanilla-JS fetch loop against /api/snapshot.

We deliberately avoid Server-Sent Events to keep deps minimal; polling is
fine for a tracker that updates at human speed.

The Flask app runs on its own thread (daemon) so it doesn't fight the asyncio
loop. There's only ever one client (the player), so we don't need worker
processes.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from flask import Flask, jsonify, render_template_string, request

from .state import BridgeState

log = logging.getLogger(__name__)


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Spicy Meatball Overdrive Tracker</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#1a1a1a; color:#e8e8e8;
           margin: 0; padding: 1.5rem; }
    h1 { margin: 0 0 1rem; font-size: 1.4rem; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    .card { background:#252525; border-radius:8px; padding:1rem; }
    .card h2 { margin-top:0; font-size:1rem; color:#bbb; text-transform:uppercase;
               letter-spacing:0.04em; }
    .conn { font-weight:bold; }
    .conn.ready { color:#7fdc7f; }
    .conn.connecting { color:#dca87f; }
    .conn.disconnected { color:#dc7f7f; }
    .kingdom-row { display:flex; justify-content:space-between; padding:2px 0; }
    .item-row { font-size:0.85rem; padding:2px 0; border-bottom:1px solid #333; }
    .item-row .from { color:#888; margin-left:0.4rem; }
    .pill { display:inline-block; padding:1px 6px; margin:1px; border-radius:4px;
            font-size:0.8rem; background:#3a4a3a; color:#cfc; }
    .pill.locked { background:#3a3a3a; color:#666; }
  </style>
</head>
<body>
  <h1>Spicy Meatball Overdrive — slot <span id="slot">?</span> · seed <span id="seed">?</span></h1>
  <div class="grid">
    <div class="card"><h2>Connections</h2>
      <div>AP: <span class="conn" id="ap_conn">?</span></div>
      <div>Switch: <span class="conn" id="switch_conn">?</span></div>
      <div>Items received: <span id="received_count">0</span></div>
      <div>Locations checked: <span id="checked_count">0</span></div>
    </div>
    <div class="card"><h2>Captures</h2>
      <div id="captures"></div>
    </div>
    <div class="card"><h2>Moons by kingdom</h2>
      <div id="moons"></div>
    </div>
    <div class="card"><h2>Recent items</h2>
      <div id="items"></div>
    </div>
  </div>
  <script>
    const el = (id) => document.getElementById(id);
    async function tick() {
      try {
        const r = await fetch('/api/snapshot');
        const s = await r.json();
        el('slot').textContent = s.slot || '?';
        el('seed').textContent = s.seed || '?';
        for (const k of ['ap_conn', 'switch_conn']) {
          el(k).textContent = s[k];
          el(k).className = 'conn ' + s[k];
        }
        el('received_count').textContent = s.received_count;
        el('checked_count').textContent = s.checked_count;
        el('captures').innerHTML = s.captures_unlocked
          .map(c => `<span class="pill">${c}</span>`).join('') || '<em>none yet</em>';
        const kingdoms = new Set([
          ...Object.keys(s.moons_received_by_kingdom),
          ...Object.keys(s.moons_checked_by_kingdom),
        ]);
        el('moons').innerHTML = [...kingdoms].sort().map(k =>
          `<div class="kingdom-row"><span>${k}</span>`
          + `<span>${s.moons_checked_by_kingdom[k]||0} checked / `
          + `${s.moons_received_by_kingdom[k]||0} received</span></div>`
        ).join('') || '<em>nothing yet</em>';
        el('items').innerHTML = s.recent_items.slice().reverse().map(it =>
          `<div class="item-row">${it.name || it.shine_id || it.cap || it.kingdom || it.kind}`
          + `<span class="from">from ${it.from}</span></div>`
        ).join('') || '<em>none yet</em>';
      } catch (e) { console.error(e); }
    }
    tick(); setInterval(tick, 1500);
  </script>
</body>
</html>
"""


def make_app(
    state: BridgeState,
    inject_deathlink: Callable[[str, str], None] | None = None,
) -> Flask:
    app = Flask("smo_ap_bridge.tracker_web")

    @app.get("/")
    def index():  # noqa: D401
        return render_template_string(_TEMPLATE)

    @app.get("/api/snapshot")
    def snapshot():  # noqa: D401
        return jsonify(state.snapshot())

    @app.post("/api/test/inject-deathlink")
    def inject():  # noqa: D401
        # Debug-only: bypass AP entirely and synthesize a kill message direct
        # to the Switch socket. Useful when validating the Switch-side inbound
        # apply path without a second player slot or a real AP bounce. Body is
        # JSON-optional: {"source": "...", "cause": "..."} (both default empty).
        if inject_deathlink is None:
            return jsonify({"error": "inject_deathlink not wired"}), 503
        body = request.get_json(silent=True) or {}
        source = str(body.get("source") or "TestRig")
        cause = str(body.get("cause") or "manual injection")
        try:
            inject_deathlink(source, cause)
        except Exception as e:  # noqa: BLE001
            log.exception("inject_deathlink failed")
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "source": source, "cause": cause})

    return app


def serve_in_thread(
    state: BridgeState,
    host: str,
    port: int,
    inject_deathlink: Callable[[str, str], None] | None = None,
) -> threading.Thread:
    app = make_app(state, inject_deathlink=inject_deathlink)
    # use_reloader=False is critical when running off the main thread.
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        name="smo-ap-tracker-web",
        daemon=True,
    )
    t.start()
    log.info("web tracker on http://%s:%d/", host if host != "0.0.0.0" else "localhost", port)
    return t
