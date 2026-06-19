# Wire protocol — Switch ↔ Bridge

Single persistent TCP connection. Each message is one line of UTF-8 JSON terminated by `\n`. Field `t` is the message type (short string). Both sides are pure event streams (no request/response pairing).

> **Dev tip (M5.5):** to exercise the SMOClient end-to-end without a Switch, run `scripts/switch_smoke_test.py` against a client connected to a local MultiServer hosting a seed of the forked apworld. See the "AP loopback" recipe in [`../CLAUDE.md`](../CLAUDE.md) for the full sequence, or run `SMOAP_LIVE_AP=1 pytest apworld/smo_archipelago/tests/test_ap_loopback.py` for the scripted version.

- Default port: **17777** (configurable on both sides; SMOClient via `~/.archipelago/host.yaml` under `meatballs_options.switch_listen_port` or the `--switch-port` CLI arg, Switch at compile time via `cmake -DBRIDGE_PORT=...`. The `romfs/ap_config.json` on the SD is informational only — `nn::fs::MountSdCardForDebug` fails on retail firmware, so the mod uses compile-time defaults; see [ApConfig.cpp](../switch-mod/src/ap/ApConfig.cpp).)
- Max line length: **8 KiB**. Longer lines are dropped and the parser resyncs to the next `\n`.
- Canonical kingdom / capture / shine names come from `apworld/smo_archipelago/data/items.json` and `data/locations.json`. Switch holds a static lookup; bridge reads the JSON directly.
- All ids/strings are case-sensitive. The Switch never sees raw AP ids.
- Module dedupes outbound `check` messages via 64-bit FNV-1a hash of the message body — the same `check` is never sent twice in a session.

## Switch → Bridge

```jsonc
// Sent first, after TCP connect succeeds.
{"t":"hello","mod_ver":"0.1.0+abc1234","smo_ver":"1.3.0","cap_table_hash":"sha1:..."}

// A location was just checked in-game. Exactly one of the optional fields
// should be present per kind:
//   moon    → kingdom, shine_id  (or M4 raw: stage_name, object_id, shine_uid)
//   capture → cap                (or M4 raw: hack_name)
// Optional `seq` (int > 0, M6 phase A.5): per-Switch-session monotonic id
// stamped on moon checks. The bridge echoes it back in `moon_label` so the
// cutscene-label hook can correlate. Older Switch builds omit `seq`; the
// bridge skips Channel A entirely when seq is absent.
{"t":"check","kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon","seq":17}
{"t":"check","kind":"capture","cap":"Goomba"}

// Status hint for the tracker (no behavioral effect).
{"t":"status","kingdom":"Metro","scenario":2,"moons_collected":47}

// Goal completed (Bowser defeated / credits triggered). Idempotent — Switch
// only sends once per save.
{"t":"goal"}

// Liveness check; bridge replies with pong.
{"t":"ping","ts_ms":1731536400000}

// Diagnostic. level ∈ {debug, info, warn, error}.
{"t":"log","level":"info","msg":"hook installed for ShineGet at 0x..."}

// M4.5 state snapshot (3-message sequence). Sent right after `hello` on every
// (re)connect, and transitively on save load (SaveLoadHook -> requestRehello
// -> reconnect -> sendHello -> sendSnapshot). Carries RAW SMO identifiers;
// bridge resolves via shine_map.json / capture_map.json — same path as live
// `check` messages.
//
// `save_slot` is informational; the bridge does NOT fence on it. Switching
// SMO save files mid-session merges all snapshots into the same AP slot
// (idempotent at the AP layer because location ids dedupe).
//
// `_meta` chunk carries the cross-stage state: captures the player has used
// (raw hack_names) and goal_reached. Goal is treated as a `goal` message if
// true.
{"t":"state_begin","mod_ver":"0.1.0","save_slot":0}
{"t":"state_chunk","stage_name":"CapWorldHomeStage","shines":[
  {"object_id":"MoonOurFirst","shine_uid":100},
  {"object_id":"MoonHatTrampoline","shine_uid":101}
]}
{"t":"state_chunk","stage_name":"_meta","captures":["Kuribo"],"goal_reached":false}
{"t":"state_end"}

// M6 phase D — per-kingdom PayShineNum snapshot. Sent (a) by ApClient at
// the tail of every (re)connect's sendSnapshot, behind the save+scene
// gate, and (b) at the tail of every AddPayShineHook /
// AddPayShineAllHook fire (after vanilla addPayShine bumps PayShineNum).
//
// `entries` is a complete reading (typically all 17 kingdoms) — kingdoms
// with pay=0 are included so the bridge can confidently zero-out a
// kingdom whose value rolled back. `kingdom` uses the Switch-form short
// name ("Bowser", "Cap"); the bridge translates to AP form on receipt.
// `save_slot` is informational (-1 = absent; bridge does not fence).
//
// The bridge derives outstanding[K] = lifetime_received_AP[K] − pay[K]
// and ships the result back in OutstandingMsg. Crash-survivable: if SMO
// crashes between toss and autosave, the next snapshot reports the
// rolled-back pay value and outstanding rebounds automatically. No
// bridge-side persistence — the whole machinery exists to make
// deposit-then-crash data loss structurally impossible.
{"t":"pay_snapshot","save_slot":0,"complete":true,"entries":[
  {"kingdom":"Cap","pay":3},
  {"kingdom":"Cascade","pay":0},
  {"kingdom":"Bowser","pay":5}
]}
```

The bridge accumulates chunks between `state_begin` and `state_end`. On end,
each entry is dispatched through the same `check` path live moon-get hooks
use; the AP server dedupes by location id, so re-sending the same snapshot
is a no-op.

## Bridge → Switch

```jsonc
// Reply to the Switch's hello. `client_ver` is the SMOClient version
// baked at apworld build time (mirror of the Switch's `mod_ver` in
// `hello`); the mod logs both versions so the user sees the pair side-
// by-side. On a version mismatch the bridge replies with `ok=false` +
// an `err` string identifying which side to upgrade, then closes the
// socket. The mod treats `ok=false` as "do not transition to Ready".
{"t":"hello_ack","ok":true,"seed":"X4F2","slot":"Mario","cap_table_hash":"sha1:...","client_ver":"0.1.0"}

// Authoritative replay sent immediately after hello_ack so the Switch can
// rebuild its `locations_checked` set and not double-send checks.
{"t":"checked_replay","ids":[
  {"kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon"},
  {"kind":"capture","cap":"Frog"}
]}

// One per AP item. Sent as items arrive AND replayed in full on every
// (re)connect. Module dedupes idempotently (moon flag write is no-op if
// already set; capture bit is set unconditionally).
{"t":"item","kind":"moon","kingdom":"Sand","shine_id":"PoolUnderwater","from":"Bob"}
{"t":"item","kind":"capture","cap":"Yoshi","from":"self"}
{"t":"item","kind":"other","name":"Power Moon (Generic)","from":"Bob"}

// AP chat / hint / item-find broadcasts, surfaced for the in-game log window.
{"t":"print","text":"Bob found Mario's Power Moon (Lake)"}

// Bridge's view of the AP connection. UI hint only; the Switch's own conn
// state is driven by its TCP socket health.
{"t":"ap_state","conn":"ready"}      // disconnected | connecting | ready

// Reply to ping.
{"t":"pong","ts_ms":1731536400000}

// Soft error.
{"t":"err","code":"unknown_kind","ctx":"check"}

// M4.6: Inbound DeathLink. Forwarded from another DeathLink-tagged slot;
// the Switch kills Mario via DeathHook::Orig. Single-bit pending queue +
// 15s debounce. Bridge-side `cfg.deathlink.enabled` controls whether the
// Switch ACTS on it (toggle communicated via hello_ack.deathlink_enabled).
{"t":"kill","source":"Bob","cause":"Bob died."}

// PC `/warp` escape. Teleports Mario to a hub kingdom home stage so a player
// physically stuck in a one-way kingdom (e.g. Bowser's without the Pokio
// capture, which grounds the Odyssey until the RoboBrood is beaten) can fly
// back to earlier kingdoms. `dest` is a short selector the Switch maps through
// a fixed allowlist ("cascade" -> WaterfallWorldHomeStage, "cap" ->
// CapWorldHomeStage; anything else -> Cascade). The Switch executes it
// (game/StageWarp.cpp) via tryChangeNextStageWithDemoWorldWarp to write the
// LITERAL destination stage, then latches GameDataHolder::setStageChanging() so
// the sequence actually performs the transition. (WorldWarpHole was rejected: it
// remaps the destination through SMO's painting-pairing table — Cascade input
// landed at Seaside — and nothing pairs to Cascade. DemoWorldWarp alone was
// rejected: it doesn't latch, so it silently no-ops from in-stage.) It's a PURE
// stage change that never unlocks a kingdom, so it cannot skip forward past a
// boss gate. Single-bit pending queue drained on the frame thread.
{"t":"warp","dest":"cascade"}

// M6 phase A.5: Channel A — pane-text override for the next moon-get
// cutscene. Bridge sends this in the same TCP push as its reply to the
// triggering `check`, so the text reaches the Switch before the cutscene
// starts. `seq` echoes Check.seq (so the consumer knows which moon it's
// for). `valid_for_ms` is a Switch-relative TTL — expired labels are
// silently dropped (cutscene shows vanilla). Text is pre-truncated by
// the bridge to ≤30 bytes UTF-8.
{"t":"moon_label","text":"Sent Cap Power Moon -> P3","seq":17,"valid_for_ms":4000}

// M6 phase D — derived per-kingdom outstanding (AP credit available for
// the next Odyssey toss). Sent whenever the inputs to compute_outstanding
// change: a Moon item arrives from AP (lifetime_received bumps), OR a
// PaySnapshotMsg lands from the Switch (PayShineNum changes). Also sent
// right after HelloAck IFF the bridge has at least one PaySnapshot
// reading; otherwise deferred until the Switch's first post-HELLO
// PaySnapshot.
//
// `entries[].kingdom` is the apworld-canonical kingdom name
// ("Bowser's", not Switch-form "Bowser"). `lake_received_total` and
// `snow_received_total` are LIFETIME receipt counts (Multi-Moon=3,
// Power Moon=1) for the M7 Path A kingdom-order gate — these never
// decay on deposit, distinct from entries[].count which is the derived
// `lifetime_received − PayShineNum` balance.
{"t":"outstanding","entries":[
  {"kingdom":"Cap","count":2},
  {"kingdom":"Cascade","count":5}
],"lake_received_total":12,"snow_received_total":0}
```

## State machines

```
AP side:    DISC ─→ CONNECTING ─→ AUTHED ─→ READY
              ↑                                │
              └──────── error / timeout ──────┘
Switch side: LISTEN ─→ ACCEPTED ─→ HELLO_OK ─→ READY
                          ↑                       │
                          └──── conn drop ────────┘
```

- AP drops while Switch is up: bridge buffers outbound checks (deque cap 4096) and flushes on AP READY.
- Switch drops while AP is up: bridge keeps full `ReceivedItems` history; replays on next HELLO.
- Both reconnect with exponential backoff (1, 2, 5, 10, 30 cap seconds).

## Idempotence rules

- `check`: dedupe by FNV-1a(message body) on the Switch. Server idempotent regardless.
- `item`: moon flag writes are no-op if already set; capture / kingdom bits set unconditionally; `other` is UI-only.
- `goal`: `ApState::goal_sent` flag, set after first send; AP server is idempotent on `StatusUpdate(CLIENT_GOAL)`.

## Future-compatible fields

- `from` carries either `"self"` or another player's name. Future bridge versions may add `from_idx` (int) for unique disambiguation; Switch should ignore unknown fields.
- New `kind` values are reserved (e.g. `"trap"`, `"hint"`); Switch should drop with `err code="unknown_kind"` rather than guess.
