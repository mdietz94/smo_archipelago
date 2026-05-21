# Architecture

Two tiers, each independently restartable:

```
[ Switch / SMO ]                [ SMOClient (Python, inside .apworld) ]   [ AP server ]
  LibHakkun subsdk9                asyncio                                   archipelago.gg
  OdysseyHeaders                   SMOContext(CommonContext)                 or self-host
  sail (symbol DB)     <--TCP-->   SwitchServer asyncio TCP    <--websocket-->
  HUD + Cappy bubbles              Kivy GUI (Tracker + Connections tabs)
                                   Forked apworld (in-zip, same package)
```

## Why an in-apworld client (instead of a standalone bridge)

Earlier revisions of this project shipped a standalone `python -m smo_ap_bridge` script, distributed separately from the apworld. The Phase 1-7 reshape (plan: `~/.claude/plans/please-put-together-a-playful-thacker.md`) collapsed it into a single Kivy-based `SMOClient` that lives inside the apworld at `apworld/smo_archipelago/client/`. Same wire protocol, same Python responsibilities — just one process, one Launcher button, one install artifact.

The Archipelago wire format (websocket + per-message-deflate + TLS) is too heavy to ship on Switch directly. `CommonContext` solves it in a few hundred lines. By subclassing CommonContext we get:

- AP websocket, reconnect, deflate, TLS — all inherited.
- A standard place to register the Launcher button (`Component("SMO Client", ...)` in `apworld/smo_archipelago/__init__.py`).
- A standard Kivy GUI (`GameManager` subclass) with logging tabs, command bar, and our custom Tracker + Connections tabs.

The Switch still only needs to speak a small line-delimited JSON protocol over a single TCP socket on the LAN — see [wire-protocol.md](wire-protocol.md). The wire protocol did NOT change in the merge.

## Module identity

The apworld registers as `Spicy Meatball Overdrive`. The client's `Connect` packet uses this exact game name. Seeds generated for this world are not interchangeable with any earlier upstream's seeds; that's intentional — this world gains richer enforcement options that an honor-system upstream cannot honor. The deployed apworld zip stem is `meatballs` (so Archipelago imports as `worlds.meatballs` and the host.yaml settings key is `meatballs_options`); the in-repo Python source folder stayed `smo_archipelago/` to avoid churning every dev-workflow path reference. See the identifier table at the top of [CLAUDE.md](../CLAUDE.md) for the full mapping.

## Process boundaries

| Process | Owns |
|---|---|
| Switch module (`subsdk9`) | hooks, game-state mirror, capture lock enforcement, HUD overlay |
| SMOClient (`worlds.meatballs.client.main` in the deployed zip; `smo_archipelago.client.main` in a loose source checkout) | AP websocket, SwitchServer (TCP :17777), Kivy GUI with Tracker + Connections tabs, datapackage, replay-on-reconnect |

## Threading

| Thread | Inside | Owns |
|---|---|---|
| SMO main (frame thread) | hooks, drawMain trampoline | `ApState::applyOnFrame()`, HUD draw, hook callbacks |
| AP socket thread (Switch) | `ApClient::loop` | `nn::socket` recv/send, JSON parse |
| Client asyncio loop | SMOClient | AP websocket via `server_loop(ctx)`, SwitchServer accept/dispatch |
| Client Kivy main loop | SMOClient | UI rendering, command-bar input, scheduled refresh of Tracker + Connections tabs |

The Switch's two threads coordinate through SPSC ring buffers (`outbound_checks`, `inbound`, `outbound_status`) plus `std::atomic`s — no mutexes.

## Key data flows

### Player collects a moon

```
SMO frame → MoonGetHook trampoline
         → reportMoonChecked(kingdom, shine_id)
         → enqueue Check on outbound_checks
AP socket thread → drains outbound_checks → wire {"t":"check",...}
SMOClient asyncio→ SwitchServer.on_check → SMOContext.report_check
                 → CommonContext.send_msgs(LocationChecks)
AP server        → broadcasts ReceivedItems to recipient
```

### Player receives a moon item from another world

```
AP server        → ReceivedItems
SMOClient asyncio→ SMOContext._handle_ap_package → classify via apworld data
                 → SwitchServer.send_item → wire {"t":"item",...}
AP socket thread (Switch) → push onto inbound ring
SMO frame thread → ApState::applyOnFrame → MoonApply::grantShine
                 → GameDataHolder::setShineGet → moon flags update; gates open
```

### Player throws Cappy at a locked enemy

```
SMO frame → CaptureStartHook (HOOK_DEFINE_REPLACE if locked path)
         → CaptureGate::captureBlocked(name) ?
         → playSE_NG(); return false  (no possession)
```

## Responsibilities map

| Concern | Switch module | SMOClient |
|---|---|---|
| AP websocket / deflate / TLS | — | yes |
| Datapackage / id resolution | — | yes |
| Item classification | — | yes (Moon/Capture/Kingdom/Other) |
| Moon flag writes | yes | — |
| Capture lock enforcement | yes | — |
| Goal detection | yes (hook) | yes (forwards to AP) |
| Replay on reconnect | applies idempotently | sends replay |
| Tracker | — | yes (Kivy GUI Tracker tab) |
| In-game HUD overlay | yes (HUD M3, ImGui M8) | — |

## Why we build on LibHakkun + OdysseyHeaders + sail

[LibHakkun](https://github.com/fruityloops1/LibHakkun) is an actively maintained subsdk runtime with musl + LLVM libc++ + the `HeapSourceDynamic` addon (which re-exports `operator new` / `malloc` / `free` from SMO's own thread-safe allocator). [OdysseyHeaders](https://github.com/MonsterDruide1/OdysseyHeaders) ships full SMO 1.0.0 type layouts (`al::`, `agl::`, `game::`, `sead::`, `nn::`, ...). Sail is Hakkun's symbol-DB resolver — a host-side tool that reads `switch-mod/syms/*.sym` at build time and emits a `fakesymbols.so` stub library plus a runtime resolver that patches in real addresses against `main.nso`'s dynsym at module load.

We migrated to this stack from exlaunch + lunakit-vendor on 2026-05-21 (M9 cutover, see [milestones.md](milestones.md#m9)). The migration surfaced 5 real bugs that affected real-Switch behavior, including an AArch64 PC-relative prologue relocation gap in LibHakkun's trampoline pool that we patched via `scripts/patch_hakkun.py` (upstream-PR-ready). The full list lives in the M9 narrative.

Coexistence: only one `subsdk9` can be installed at a time. Users running mods like LunaKit must rename one slot or pick one.
