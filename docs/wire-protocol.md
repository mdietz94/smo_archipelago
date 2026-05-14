# Wire protocol — Switch ↔ Bridge

Single persistent TCP connection. Each message is one line of UTF-8 JSON terminated by `\n`. Field `t` is the message type (short string). Both sides are pure event streams (no request/response pairing).

- Default port: **17777** (configurable on both sides; bridge in `config.toml`, Switch in `romfs/ap_config.json`)
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
//   moon    → kingdom, shine_id
//   capture → cap
//   shop    → kingdom, slot OR name
{"t":"check","kind":"moon","kingdom":"Cascade","shine_id":"Our First Power Moon"}
{"t":"check","kind":"capture","cap":"Goomba"}
{"t":"check","kind":"shop","kingdom":"Cap","slot":3}

// Status hint for the tracker (no behavioral effect).
{"t":"status","kingdom":"Metro","scenario":2,"moons_collected":47}

// Goal completed (Bowser defeated / credits triggered). Idempotent — Switch
// only sends once per save.
{"t":"goal"}

// Liveness check; bridge replies with pong.
{"t":"ping","ts_ms":1731536400000}

// Diagnostic. level ∈ {debug, info, warn, error}.
{"t":"log","level":"info","msg":"hook installed for ShineGet at 0x..."}
```

## Bridge → Switch

```jsonc
// Reply to the Switch's hello.
{"t":"hello_ack","ok":true,"seed":"X4F2","slot":"Mario","cap_table_hash":"sha1:..."}

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
{"t":"item","kind":"kingdom","kingdom":"Lake","from":"Alice"}
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
