# CLAUDE.md — context for the next session

This file is a fast-load brief for picking up the **Spicy Meatball Overdrive** project cold. Three identifiers all refer to the same thing but spell it differently — keep them straight:

| Identifier | Value | Scope |
|---|---|---|
| AP-protocol game name | `Spicy Meatball Overdrive` | Wire-format `game` field in YAML seeds and AP `Connect` packets |
| Shipped apworld zip | `smo.apworld` | What lands in `vendor/Archipelago/custom_worlds/`; Archipelago imports it as `worlds.smo` |
| host.yaml settings key | `smo_options` | Derived by Archipelago from the zip stem `smo` |
| In-repo source folder | `apworld/smo_archipelago/` | Kept verbose to avoid churning every dev-workflow path reference; only the deployed artifact uses `smo` |
| Switch mod CMake project | `smo_archipelago` | Unrelated to the apworld; lives in `switch-mod/CMakeLists.txt` |

All four "smo" spellings parse as **S**picy **M**eatball **O**verdrive. The 2026-05-16 rename pass dropped the `Manual_SMO_archipelago` AP identifier (Manual-framework prefix was redundant once we shipped a real client) and shortened the deployed zip to `smo.apworld` (the `_archipelago` suffix was redundant when the parent dir is literally `custom_worlds/`). Read this file first, then `docs/architecture.md` and the plan file at `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md`.

## ⚠️ CRITICAL: Never commit Nintendo IP

This repository is open-source and built on a careful line: **functional identifiers and reference apworld names are okay; bulk-extracted Nintendo content is not.** A misstep here exposes the user to DMCA risk. Before any commit, audit `git status` + `git diff` and refuse to stage anything from this list:

**Must NEVER be committed (already gitignored — keep it that way):**
- `apworld/smo_archipelago/client/data/shine_map.json` — full extracted (stage, obj_id) → display-name table. Generated per-machine by `scripts/extract_shine_map.py`. ~775 verbatim Nintendo USen strings.
- `apworld/smo_archipelago/client/data/capture_map.json` — `hack_name → english_name` table. ~52 verbatim Nintendo USen strings.
- `apworld/smo_archipelago/client/data/shine_map_review.json` and `capture_map_review.json` — diagnostics that include the same strings.
- `.romfs-cache/` — extracted RomFS (~5 GB of Nintendo assets).
- `scripts/.extract-venv/` — local Python 3.12 venv (not IP, but big and machine-specific).
- `docs/main-*.nso`, `*.nsp`, `*.nca`, `*.byml`, `*.szs`, `*.msbt` — any raw Nintendo binary.
- `prod.keys` / `dev.keys` / `title.keys` — Switch keys are themselves IP-sensitive.
- Any moon-name list, capture list, or stage list of more than ~5 entries pasted into a doc, comment, or commit message as illustrative content — bulk transcription is the same exposure as the file.

**Generally OK (already in the repo, established by upstream forks):**
- `apworld/smo_archipelago/data/locations.json` and `items.json` — the community-curated location and capture names (currently 482 locations + 42 captures after the shop / outfit / trap purge — see the 2026-05-16 cleanup entry below). Forked from the public [empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP) Manual world. Edits are fine; bulk additions from a romfs dump are not — alignment with Nintendo's MSBT should happen one mismatch at a time, not as a wholesale copy.
- Functional identifiers like `WaterfallWorldHomeStage`, `obj214`, `ScenarioName_<ObjId>`, `ShineList`, kingdom internal names (`CapWorld`/`SkyWorld`/etc.). These appear in every public SMO modding project (lunakit, MoonFlow, OdysseyDecomp) and are functional, not expressive.
- The one M5.7 anchor entry (`"Our First Power Moon"`) appears in CLAUDE.md, the test suite, and docs as a known ground-truth datapoint. One name as a verifiable test fixture is fine; a list of names is not.

**Safe pattern**: anything that requires a user to run `scripts/extract_shine_map.py` to produce stays in the gitignore. If you find yourself wanting to commit a piece of data so the next agent has a richer starting point, instead document where to regenerate it — see `docs/extract-moon-data.md` for the model.

**If you've staged something questionable**: `git restore --staged <path>` to unstage, then either delete the file or add it to `.gitignore` before retrying. Never override `.gitignore` with `git add -f` for SMO content. When in doubt, ask the user.

## What we're building

A real Archipelago client for **Super Mario Odyssey on a modded Switch (FW 21.2, native SMO 1.0.0 install, Atmosphere CFW)**. Replaces the existing Manual checklist client ([empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP)) — an honor-system tick-the-boxes app — with an in-game module that detects moons/captures/scenario events automatically, applies received items live, and enforces capture locks until the AP item arrives.

### Architecture (two tiers)

```
[ Switch / SMO ]  <--TCP/JSON LAN-->  [ PC Client (Python, inside apworld) ]  <--websocket-->  [ AP server ]
   exlaunch                              SMOContext(CommonContext)                              archipelago.gg
   LunaKit headers                       Kivy GUI (Tracker + Connections tabs)                  or self-host
   ImGui overlay (M8)                    SwitchServer asyncio TCP on :17777
   HUD overlay (M3)                      Forked apworld machinery
```

The PC client (formerly the standalone "bridge" process) lives inside the apworld at
`apworld/smo_archipelago/client/` and ships in the .apworld zip. Archipelago's Launcher
auto-discovers it via the `Component("SMO Client", ...)` registration in the apworld's
`__init__.py`. One process, one Kivy window, one install artifact.

The merge happened in the Phase-1-through-7 reshape (see plan
`C:\Users\maxwe\.claude\plans\please-put-together-a-playful-thacker.md`). Before that,
this was a separate `python -m smo_ap_bridge` script with a Flask web tracker on :8000;
that whole tree was deleted and its responsibilities absorbed into the Kivy client.

The client owns AP-protocol complexity (websocket + deflate + TLS + reconnect, all
inherited from `CommonContext`). Switch speaks a small line-delimited JSON protocol on
port **17777**. Full wire format: `docs/wire-protocol.md`.

## Decisions already made (and why)

| Decision | Why |
|---|---|
| **PC bridge, not direct Switch→AP** | websocket+deflate+TLS+reconnect on Switch is months of work; bridge solves it in ~hundred lines via `CommonContext` |
| **Archipelago as git submodule, not pip install or vendored copy** | Their `setup.py` blocks pip; copying ~15 transitive files would drift fast. Submodule under `vendor/Archipelago/` is drift-proof and also enables seed generation in the same checkout |
| **Forked apworld, not vendored unchanged** | M8 will add automation-only features (deathlink, traps, hint system, progressive moon gating) the Manual world can't enforce |
| **Web tracker priority, in-game ImGui later** | User preference. Web tracker (M5) ships before in-game tracker (M8) |
| **LunaKit as soft dep (link headers), not fork** | LunaKit churns fast; submodule lets us pin without inheriting their bugs |
| **Target SMO 1.0.0** | Canonical version every public mod (lunakit, smo-online, smo-practice, OdysseyDecomp) targets. User has a native 1.0.0 install on a downgraded FW 21.2 Switch |
| **Bit-index capture table generated from apworld** | `scripts/sync_capture_table.py` regenerates `switch-mod/src/ap/capture_table.h` from `data/items.json` so Switch and bridge can't drift on cap-name → bit-index assignment |
| **Game name `Spicy Meatball Overdrive`, zip `smo.apworld`** | Renamed 2026-05-16. AP-protocol name dropped the Manual-framework `Manual_SMO_archipelago` prefix (we ship a real client now, not a Manual world). Deployed zip shortened from `smo_archipelago.apworld` to `smo.apworld` — Archipelago derives the module name from the zip stem, so the world imports as `worlds.smo` and the host.yaml settings key is `smo_options`. The in-repo source folder stayed `apworld/smo_archipelago/` to avoid churning every dev-workflow path reference; see the identifier table in the preamble |

## Current status — track by track

| Track | What it is | Status |
|---|---|---|
| **1 — Bridge runtime** | Python bridge can connect to AP server | DONE wiring, needs Archipelago submodule add |
| **2 — Switch dev toolchain** | devkitPro / CMake / Ninja installed on PC | **DONE** |
| **3 — Modded Switch + game dump** | Native SMO 1.0.0 install on FW 21.2 | **DONE.** Native 1.0.0 NSP + `main.nso` dump at `C:\Users\maxwe\Downloads\` (DO NOT commit — copyrighted). Keys at `C:\Users\maxwe\.switch\` |
| **4 — Symbol discovery (M0)** | Mangled symbols in `switch-mod/src/hooks/HookSymbols.hpp` | **DONE + VERIFIED.** All 8 symbols resolve in real 1.0.0 main.nso (`scripts/check_nso_symbols.py`). 3 byte-identical to lunakit's verified 1.0.0 hooks; 5 computed from OdysseyDecomp forward-decls. Runtime `nn::ro::LookupSymbol` will succeed |
| **5 — Ryujinx dev loop** | Build deploys to emulator, validates before Switch | **DONE.** `-DRYU_PATH=C:/Users/maxwe/AppData/Roaming/Ryujinx` post-build hook copies subsdk9+npdm+config into Ryujinx mods |
| **6 — Generate test seed** | Use forked apworld in Archipelago checkout to make a seed | Not started; needs Archipelago submodule add first |
| **7 — Real-Switch deploy** | Final validation after Ryujinx green | Ryujinx green (2026-05-15: HELLO observed end-to-end). Ready when desired |

## Plan milestones

`C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` is authoritative (FW 21.2 + SMO 1.0.0 simplification). The prior plan `there-is-a-super-peaceful-iverson.md` predates the downgrade and is archived for reference only. Summary:

- **M0**: toolchain + symbol map (Track 3 + Track 4) — **DONE**
- **M1**: bridge skeleton — **CODE COMPLETE** (19 tests pass, loopback smoke test green, web tracker JSON endpoint verified)
- **M2**: apworld parity fork — **CODE COMPLETE** (vendored `data/`, `creator: archipelago`)
- **M3**: Switch module skeleton — **RUNTIME VALIDATED** (2026-05-15, Ryujinx). Subsdk9 + main.npdm produced via lunakit stock template; all 8 hooks install via soft-install probe; `nn::socket` worker thread connects, sends HELLO, bridge logs `switch HELLO: mod=0.1.0 smo=1.0.0`. Inbound-item handler / replay / exponential backoff are code-complete but not yet exercised. Real-Switch deploy gated on user choice; Ryujinx loop is canonical for now.
- **M4**: read-only state mirroring — **DONE.** All 6 game-event hooks (MoonGet, CaptureStart, ScenarioFlag, SaveLoad, Ending, Death) emit raw SMO identifiers to the bridge. Bridge resolves via `shine_map.json` / `capture_map.json`. DeathLink outbound wired (inbound apply landed in M4.6). `Check` is now `char[64]` buffers + `FlatHashSet<4096>` for `locations_checked` (allocator NULL-deref workaround in our subsdk9 link). Validated in Ryujinx 2026-05-15.
- **M4.5**: state reconciliation across disconnects — **CODE COMPLETE.** Bridge accepts new `state_begin` / `state_chunk` / `state_end` snapshot from Switch on every (re)connect (transitively on save load via `requestRehello`); accumulates raw IDs by stage and dispatches each entry through the same `check` path live moon-get hooks use. `BridgeState.add_checked_location` dedupes by full ItemRef identity so replays are no-ops. Switch fixes outbound check drop bug in `pumpOnce` (peek-then-pop). 11 new bridge tests; switch-mod enumerate functions stubbed pending M5/M6 GameDataHolder traversal.
- **M4.6**: inbound DeathLink (peeled out of original M6) — **DONE 2026-05-15.** Switch acts on inbound `kill` messages by invoking `DeathHook::Orig(cached_PlayerHitPointData*)` directly — the trampoline's Orig bypasses our own Callback so the synthetic death doesn't echo back out as a fresh DeathLink. `synthetic_death_this_frame` kept as defense-in-depth for any future hook downstream of `PlayerHitPointData::kill`. Single 15s debounce window (`kInboundKillDebounceMs`) covers both "Mario in death animation" and "two kills too close together" via one shared `last_observed_death_ms` timestamp updated on every observed death (organic or synthetic). Inbound queue collapsed to a single atomic bool (`inbound_kill_pending`) so closely-spaced bounces auto-debounce at the producer. DeathLink **toggle moved to bridge config**: bridge's `cfg.deathlink.enabled` is communicated to the Switch in `HelloAckMsg.deathlink_enabled`, parsed into `ApState::deathlink_enabled`, gates the inbound apply path. Outbound death reporting is NOT gated on this Switch-side flag — bridge already gates outbound, double-gating would break old-bridge/new-Switch combos. Chicken-and-egg: first inbound DeathLink before Mario has died once organically is dropped with a log line (`PlayerHitPointData::kill` is the only cache site today; closing this hole needs an earlier "any damage" hook). Debug-only `POST /api/test/inject-deathlink` on the web tracker writes `KillMsg` straight to the Switch socket (bypasses AP), so the apply path can be exercised without a second slot.
  - **Pre-existing recv-loop bug surfaced + fixed**: `ApClient::threadMain` processed at most one inbound line per Select-wake AND only entered the read branch when Select reported socket-readable. When the bridge sends N messages in a single TCP push (the very common handshake `hello_ack + checked_replay + ap_state` triple, or items + kill back-to-back), messages 2..N sat in `read_buf_` until the *next* socket event — sometimes minutes. Validated live: the M4.6 kill stayed buffered 90+ seconds behind a stale `checked_replay` and Mario never died. Fix splits `readOneLine` into `recvIntoBuf` + `popLine`; the loop drains `read_buf_` to completion every iteration, including Select-timeout iterations. This bug had been masking inbound-message issues in M5.7 as well — anyone testing M6 item delivery would have hit it.
- **M5**: web tracker — **CODE COMPLETE** (Flask + SSE, served on :8000; debug `POST /api/test/inject-deathlink` endpoint for DeathLink tests)
- **M5.5**: AP server live integration — **DONE 2026-05-15.** Forked apworld zipped to `vendor/Archipelago/custom_worlds/smo.apworld` (was `smo_archipelago.apworld` at the time of M5.5; renamed 2026-05-16) via `scripts/install_apworld.py`. Seed generation via `scripts/ap_generate.py` (thin wrapper that pre-sets `ModuleUpdate.update_ran = True` to suppress AP's auto-pip on world-specific deps). MultiServer wrapper at `scripts/ap_server.py`. Bridge ↔ local AP loopback validated end-to-end: `>> check Cap: Frog-Jumping Above the Fog` → bridge translates → `LocationChecks` to AP → AP sends `ReceivedItems` → bridge forwards `ItemMsg` to fake-Switch (all under 1s per round-trip). Bridge fix in `ap_client.py::_populate_datapackage_from_ctx` hydrates `self._dp` from CommonContext's `location_names`/`item_names` on `Connected` (CommonContext satisfies its own lookup from Archipelago's shipped `network_data_package.json` and never relays a `DataPackage` packet that our `on_package` could catch). Regression test `bridge/tests/test_ap_loopback.py` skips unless `SMOAP_LIVE_AP=1`; 43 existing tests still green. Test seed at `bridge/test_seeds/smo_loopback.yaml` (gitignored output at `bridge/test_seeds/out/`).
- **M5.7**: Ryujinx E2E — **DONE 2026-05-15.** First real moon traversed the whole stack: Mario collects "Our First Power Moon" in Ryujinx → `MoonGetHook` fires with `stage=WaterfallWorldHomeStage, obj=obj214` → `[pump] Send 102 bytes` → bridge resolves via `shine_map.json` → `LocationCheck id=14481151511` to AP → AP records check, places "Snow Kingdom Power Moon" item → `ReceivedItems` echoed → bridge forwards `ItemMsg` to mod (mod's inbound ring receives it; M6 application still stubbed). Three real bugs surfaced + fixed: (a) mod's `BRIDGE_HOST` was baked at the stale M3-era LAN IP (rebuilt with `-DBRIDGE_HOST=127.0.0.1` for Ryujinx-on-same-host); (b) `shine_map.json` seed entries used aspirational `MoonOurFirst`-style symbolic names but `ShineInfo::objectId` actually emits the placement-file ref `obj214` — confirmed via MoonFlow's public `ShineInfo` schema, replaced with 1 verified entry; (c) `ap_client.report_check` silently returned on `locations_checked` dedup, which combined with persistent `AP_*.apsave` from the M5.5 smoke test masked working pipeline as "moon arrived but nothing happened" — added explicit forwarding-vs-skip log lines. Diagnostic logging shipped permanently: `MoonGetHook` probe (`obj`/`scen`/`uid`), `ApClient::pumpOnce` `[pump]` traces, `ap_client.report_check` forwarding-distinction lines. These were load-bearing observability — every issue would have been silent without them.
- **M5.8**: full moon + capture data extraction — **DONE 2026-05-15.** Single command `python scripts/extract_shine_map.py --nsp <SMO_1.0.0.nsp>` produces a complete 775-entry `shine_map.json` AND 52-entry `capture_map.json`. Self-bootstraps a Python 3.12 venv with `oead` (no 3.13 wheel available); auto-extracts romfs via `hactool` (PFS0 → program NCA → RomFS, ~5 GB cached at `.romfs-cache/`).
  - **Moons**: walks `SystemData/ShineInfo.szs` (17 BYML kingdom shine lists) and joins against per-stage MSBT in `LocalizedData/USen/MessageData/StageMessage.szs` under `ScenarioName_<ObjId>` keys. The MSBT must be the per-shine StageName MSBT (sub-stages like `PushBlockExStage` own their own messages), and kingdom assignment must come from the HomeStage BYML container (sub-stage names don't match `CapWorld*` etc.).
  - **Captures**: walks `SystemData/HackObjList.szs` (130 internal `HackName` strings) and joins against `LocalizedData/USen/MessageData/SystemMessage.szs/HackList.msbt` where the label *is* the internal name and the value is the English form. A small `CAPTURE_NAME_ALIASES` table handles 6 cases where the apworld deliberately diverged from Nintendo (collapsed multi-piece variants like `Picture Match Part (Mario)` → `Picture Match Part`, prefix renames like `Cheep Cheep (Snow Kingdom)` → `Snow Cheep Cheep`, casing like `Bowser statue` → `Bowser Statue`). Investigation showed no public repo publishes the Japanese-internal → English mapping (only the internal names appear in lunakit / OdysseyDecomp as code identifiers), so extraction is the only safe path.
  - **MSBT parser**: shipped as a ~150-line in-tree reader because `pymsyt` only knows BotW's control codes and chokes on SMO's control code 6.
  - **Cross-validation**: 100% (436/436 moons + 43/43 captures) of apworld entries resolve. Emitted files cover the full 775 + 52 SMO entries — extras (339 out-of-apworld-scope moons, 7 out-of-scope captures) emitted so future apworld expansion picks them up automatically. (T-Rex was promoted into the apworld in the logic-audit pass; pre-promotion it was 42/42 + 8 out-of-scope.)
  - **IP discipline**: all 4 generated files (`shine_map.json`, `shine_map_review.json`, `capture_map.json`, `capture_map_review.json`) are gitignored. Nine tests in `bridge/tests/test_shine_map_extraction.py` validate schema/count/dedup/anchors for both maps (auto-skip when files absent). Also fixed 10 apworld typos in `apworld/.../locations.json` (e.g. `"Cafe?"` → `"Café?"`, `"By the Falls"` → `"by the Falls"`). Full workflow in `docs/extract-moon-data.md`.
- **M6 phase A**: AP-credit moon counter HUD substitution — **DONE 2026-05-15.** Two new trampoline hooks (`ShineNumGetHook` on `GameDataFunction::getCurrentShineNum`, `ShineNumByWorldGetHook` on `getGotShineNum`) drop `orig` and return AP-credit-only counts. `ApState` gains `ap_moons_unkingdomed` (truly-generic "Power Moon" credits) + `ap_moons_kingdom[17]` (kingdom-tagged credits, indexed by `kingdomBitFor`). `applyOnFrame` moon arm rewritten to bump credit counters with rich logging (`[m6-moon]` lines); Multi-Moon items grant +3, single-moon +1, kingdom-less generic credits go to `ap_moons_unkingdomed` and only show in the global counter. setGotShine runs untouched so the shine list correctly reflects local pickups — only the visible counter is AP-gated. Validated in Ryujinx (2026-05-15): local moon collection → HUD stays 0, Odyssey ship rejects the moon ("doesn't count"); REPL `grant Cascade Kingdom Power Moon` → HUD ticks to 1, Mario can hand it to the Odyssey; `grant Snow Kingdom Power Moon` rejected by the Cascade Odyssey (kingdom-specific routing works); pre-existing save moons disappear from the visible counter (orig is fully suppressed). `getGotShineNum` hook resolves and fires when explicitly invoked but **never fires during normal Cascade play** — SMO's natural per-kingdom counter reads shine flags directly; the global `getCurrentShineNum` does most of the work for HUD + Odyssey gating. Two new symbols mangled via `aarch64-none-elf-g++ -c` from OdysseyDecomp forward-decls and added to `scripts/check_nso_symbols.py`. Also fixed a latent classifier bug: items use ` Kingdom ` separator (space), not `:` (location form), so `"Cascade Kingdom Power Moon"` was silently routing to `kingdom=None` — fix in `datapackage.py` with new `_ITEM_MOON_KINGDOM_RE`. Bridge `--repl` mode added for dev-test injection without an AP server (commands route through `DataPackage.classify_item` so wire fidelity matches real AP items). (REPL `/grant` / `/capture` / `/kingdom` removed 2026-05-17 — use `/send` on the AP server console; see the "M6 phase-A playtest loop" section.) M6 phase B (captures) + phase C (kingdom unlock via `unlockWorld` + snapshot enumerate bodies) are the obvious continuations.
- **apworld item-pool simplification** (rides along with M6 phase A.5, 2026-05-16): removed the kingdom-AGNOSTIC `Power Moon` item (count=463) from [apworld/.../items.json](apworld/smo_archipelago/data/items.json). Item pool drops from 1043 → 580. Reason: all moon items should be per-kingdom so the per-kingdom HUD counter (`getGotShineNum` hook → `ap_moons_kingdom[bit]`) ticks correctly; the kingdom-agnostic `Power Moon` only fed the global `ap_moons_unkingdomed` counter and was effectively dead weight in a per-kingdom-aware mod. After the change the item pool is just `X Kingdom Power Moon` (+1) and `X Kingdom Multi-Moon` (+3) per kingdom, plus captures. Per-kingdom moon-credit totals now match the in-game moon count for that area (e.g. Cascade: 19 PM + 1 MM = 22 credits = 22 collectable in-game moons). Multi-Moon LOCATION (`Cascade: Multi Moon Atop the Falls`) and the in-game Multi-Moon shine handling are untouched — collecting it just sends a LocationCheck for that location like any other moon, AP routes whatever item is there, the per-kingdom counter ticks +1 or +3 depending on what came back.
- **apworld pool-only-moons-and-captures cleanup** (2026-05-16): purged everything from the item pool that the mod can't actually grant. Shop items (82 hats/outfits/souvenirs/stickers, both Coin and Regional categories) deleted from items.json + the matching 82 `Shop:` locations from locations.json — the Switch mod has no shop-purchase hook so those checks were unreachable anyway. The 9 outfit-rule helpers in [hooks/Rules.py](apworld/smo_archipelago/hooks/Rules.py) (`Sombrero` / `Explorer` / `Builder` / `Snowsuit` / `Resort` / `Chef` / `Samurai` / `Boxers` / `Swimwear`) and their `{Sombrero()}`-style references on 13 moon `requires` strings went with them; standalone refs became `[]`, ANDed refs (`{IntoTheLake()} and {Swimwear()}`) shed the outfit clause. The 3 trap items (`Return Trap` / `Upside Down Trap` / `Cappyless Trap`, all count=0) removed; the [Options.py:20](apworld/smo_archipelago/Options.py:20) auto-register conditional on any-`trap: true` makes `filler_traps` disappear from options automatically. `filler_item_name` in [data/game.json](apworld/smo_archipelago/data/game.json) flipped from "The Will to Do Trick Jumps" to "Coin" — the Manual framework auto-appends an item entry with this name in [Items.py:21](apworld/smo_archipelago/Items.py:21), so no items.json change needed. **Final pool: 68 items.json entries (26 per-kingdom moon types + 42 captures) + 1 Coin auto-appended = 69 declared items; gen places 479 items at 482 locations (3 short → 3 Coin fillers added automatically by `adjust_filler_items`).** Also stripped the now-dead `ItemKind::Shop` enum value + dead `ItemRef.slot` / `Check.slot` / `Item.slot` int fields from both the C++ wire protocol ([ApProtocol.hpp](switch-mod/src/ap/ApProtocol.hpp)) and the Python bridge ([protocol.py](apworld/smo_archipelago/client/protocol.py)) — `slot` was originally introduced for shop-slot positions and was never set non-default in real traffic. `fromWire("shop")` still decodes to `ItemKind::Other` as forward-compat for old-Switch builds. Validation: 120 Python tests + 3 C++ host suites green; seed generation succeeds.
- **M6 phase A.5**: moon-get cutscene label substitution (Channel A) — **DONE 2026-05-16 (Ryujinx-verified).** When Mario collects a moon, the cutscene's "TxtScenario" pane text is replaced with AP-aware text (`Got Cap Power Moon!` / `Sent Cap Power Moon -> P3`). Bridge pre-warms via `LocationScouts` on `Connected` so it already knows what item each location yields — synthesizes label text the moment a check arrives and ships `MoonLabelMsg` in the same TCP push as the handshake reply, so no AP round-trip in the hot path. Switch's `MoonLabelHook` trampolines 3 cutscene state-machine entry points (`StageSceneStateGetShine::exeDemoGet`, `Main::exeDemoGetStart`, `Grand::exeDemoGetStart`) and calls `al::setPaneStringFormat` post-Orig, so our write wins over SMO's vanilla placeholder. Layout offsets (0x20 / 0x40 / 0x40) + pane name (`TxtScenario`) extracted by disassembling each call site against the real 1.0.0 main.nso (Phase 0 of the plan; `aarch64-none-elf-objdump` + a small Python register-simulator). All 4 new symbols verified in `scripts/check_nso_symbols.py` (20/20 total). Bridge uses a release-store-publish pattern on `ApState::pending_moon_label` (no mutex — the libstdc++ allocator NULL-deref applies to std::mutex too); frame thread tracks `label_last_consumed_seq` so the per-frame cutscene `exe` callback only applies once per moon. Sequence ids stamped by `next_check_seq.fetch_add(1)` in `reportMoonChecked` so the bridge can correlate label↔check via `CheckMsg.seq` ↔ `MoonLabelMsg.seq`. Channel B (Cappy bubble for items arriving outside the cutscene window) is the deferred M6.6 follow-up — see the plan for the scope split rationale. Bridge `--repl` got a `label <text>` command for visual testing without an AP server.
- **M6 phase B**: capture grant via `addHackDictionary` — **DONE 2026-05-16.** AP-issued capture items now write into SMO's hack dictionary so unlocked captures appear in the in-game Capture List. Two new symbols (`addHackDictionary` + `isExistInHackDictionary` for idempotency probe) resolved via `nn::ro::LookupSymbol` at module init, stored as function pointers (same pattern as `CaptureStartHook::getCurrentHackName`). New `CaptureGate::grantCapture(cap_name, hack_name)` is called from `ApState::applyOnFrame` capture arm; idempotent via `isExistInHackDictionary`; falls back to identity (`hack_name = cap_name`) when bridge didn't resolve, which works for the ~36 1:1 names like Frog→Frog. `ApState` gains `game_data_holder_cache` (atomic `void*`); `DrawMainHook` reads `HakoniwaSequence::mGameDataHolder` at offset 0xB8 (a `GameDataHolderAccessor` whose first field is the holder ptr) every frame and stores it. `GameDataHolderWriter` / `GameDataHolderAccessor` are 1-pointer Itanium-ABI-trivial wrappers; we declare local mirror structs and brace-init from the cached pointer when constructing arguments. Bridge: `ItemMsg` gains optional `hack_name`; `CaptureMap` gains a `cap_to_hack` reverse lookup; `ap_client.py::ReceivedItems` stamps the resolved hack_name onto `ItemRef` before `add_received_item` so reconnect-replay carries it through `switch_server.py`. (REPL `capture <name>` was the wire-fidelity test fixture at the time; removed 2026-05-17 once the AP-received path was the canonical test — see "M6 phase-A playtest loop".) Latent classifier robustness: `_strip_none` ensures `hack_name: None` is omitted from the wire payload so old mods don't choke. 8 new bridge tests (2 protocol round-trip, 4 reverse-map, 2 REPL). Playtest validated (2026-05-16): REPL `capture <name>` → mod log `[m6-capture] addHackDictionary OK cap='X' hack='Y'` → capture appears unlocked in the Cappy Capture List menu.
- **M6.1 worker-thread allocator hardening** — **DONE 2026-05-16.** After the M6-B playtest, every save load reliably crashed the worker thread in `__memcpy_device` / `nn::os::GetTlsValue` (NULL TLS slot). Each successive iteration peeled off one more libstdc++ allocator caller on the recv-loop; all four are now eliminated:
  1. **Encoder** (`Encoder::beginObject` → `std::vector<bool>::push_back`): replaced with fixed `bool[kMaxDepth=16]` + depth counter.
  2. **Encoder output** (`std::string out_`): replaced with caller-owned `smoap::util::json::LineBuffer` (fixed `char[8 KiB]`). All `encode*` functions now take `LineBuffer&` and return void; ApClient call sites pass stack-local or SnapshotBuilder-member LineBuffers. `value(std::int64_t)` uses `snprintf` into a stack `char[24]` instead of `std::to_string`.
  3. **Inbound buffer + line storage** (`std::string read_buf_`, `popLine(std::string&)`, `handleLine` mutable copy): replaced with `char read_buf_[8 KiB]` + size, and `popLine(char*, size_t&)` / `handleLine(char*, size_t)` operating on caller-mutable buffers. Reader decodes escapes directly into the line buffer.
  4. **DecodedMsg fields** (every `std::string` in `HelloAck` / `ItemRef` / `Item` / `Print` / `ApStateMsg` / `Err` / `Kill` / `DecodedMsg.t`): replaced with `char[N]` (N = 64/128/256/512 depending on field). `readIntoString` → `readIntoField<N>` template. `fromWire` got a `const char*` overload so kind discriminators never construct a std::string. `CheckedReplay::ids` (was `std::vector<ItemRef>`) is now `ItemRef[128]` + `id_count` + `truncated` flag. Because `DecodedMsg` is now ~67 KiB, `handleLine` holds it as a function-local `static` rather than on the worker stack (single instance, worker thread is the only caller). Downstream APIs that took `const std::string&` (`kingdomBitFor`, `captureBitFor`, `grantCapture`, `captureBlocked`) now take `const char*`.
  
  Validated in Ryujinx (2026-05-16): six successive save loads, each triggering re-HELLO → `hello_ack` → `checked_replay: 2 entries` → heartbeats resume; session ended on clean shutdown, no `PrintGuestStackTrace`. Host tests: 27 in `test_json` (encoder/LineBuffer/overflow/round-trip) + all `test_protocol` including new `decode_checked_replay_truncates_past_cap` and `decode_field_overlong_string_truncates`. Outbound `StateChunk::shines` / `StateChunk::captures` are still `std::vector` but populated by stub enumerate functions; convert when M5/M6 enumerate bodies land.
- **M6 phase C** (deferred): kingdom unlocks via `unlockWorld` (the user's "less ideal" fallback should it turn out the AP-credit moon counter doesn't fully gate kingdom progression in every case), plus M4.5 snapshot enumerate bodies (`enumerateOwnedShines` / `enumerateOwnedCaptures`). Symbols already in `scripts/check_nso_symbols.py`. Phase A's REPL-injection flow + the new phase B grant path are the test infrastructure. **NB**: when enumerate bodies land, the StateChunk vector fields will need the same treatment described in M6.1, or the worker-thread allocator NULL-deref will re-emerge on first snapshot send.
- **M6 phase D** — moon-deposit debit (HUD ticks DOWN on Odyssey hand-toss) — **DONE 2026-05-17 (Ryujinx-verified).** Was a real bug: M6-A's HUD was AP-credit-only but had no debit path, so Mario could re-spend the same AP-credit moons forever at the Odyssey ship. Fix is a hook on `GameDataFunction::addPayShine(GameDataHolderWriter, s32)` — the public wrapper for the per-toss spend (the `GameDataFile::addPayShine(s32)` member is inlined into all callers in 1.0.0 main.nso and not present in dynsym; the `GameDataFunction::` wrapper IS, same hookable-wrapper-over-inlined-member pattern as `addHackDictionary`). Hook also covers `GameDataFunction::addPayShineCurrentAll(GameDataHolderWriter)` (rare "pay everything in current kingdom" path). Both clamp at 0 to enforce per-kingdom isolation: a Cap-Odyssey toss can NEVER decrement Wooded credit, even when Cap balance is 0. Also new: `ShineNumGetHook` now returns `ap_moons_kingdom[currentKingdom_bit]` (per-kingdom, not the sum-across-all from M6-A) so the HUD shows exactly what Mario can spend HERE, matching vanilla post-clear `getCurrentShineNum` semantics. Current kingdom resolved via `GameDataFunction::getCurrentWorldIdNoDevelop` (third new symbol; the `NoDevelop` variant clamps the develop-state `-1` to 0). World-id ordering verified against OdysseyDecomp — **NB**: it does NOT match our `kKingdoms[]`; SMO's id 8/9 are Sea/Snow but our bits 8/9 are Snow/Seaside, and SMO id 11/12 are Boss/Sky but our bits 11/12 are Bowser/Ruined. `kingdomBitForWorldId(int)` in [KingdomUnlock.cpp](switch-mod/src/game/KingdomUnlock.cpp) encodes the four-swap translation. Wire-protocol additions: `DepositMsg` (Switch→Bridge, with monotonic per-session `seq`), `DepositAckMsg` (Bridge→Switch, idempotent re-ack of repeated seqs), `OutstandingMsg` (Bridge→Switch, authoritative per-kingdom balance from the AP data store). Per-kingdom outstanding persisted in AP data store under key `smo_outstanding_<team>_<slot>` via `set_notify` + `Set` with `replace` op (single bridge, AP server linearizes back-to-back `Set`s in a single coroutine so no read-modify-write race). Switch keeps unacked deposits in a 32-entry ring; replays on reconnect; `ApClient::threadMain` clears it on save-load-driven re-HELLO (NOT on ordinary disconnects, so a network blip doesn't lose pending deposits). `bridge_connected` atomic gates both hooks: offline → `ShineNumGetHook` returns 0 (Odyssey UI refuses fuel) AND `AddPayShineHook` skips Orig (vanilla PayShine can't drift from AP credit). **Critical wire-protocol invariant in `switch_server.py::_on_hello`**: when sending the post-HELLO item replay, **skip Moon items** — `OutstandingMsg` already carries the authoritative per-kingdom balance, and re-sending Moon items would double-count via the mod's `applyOnFrame` fetch_add. Captures + kingdoms still replay through the existing loop. Tests: 12 new in `test_outstanding.py` + 5 new in `test_protocol.py` + 5 new in `test_switch_server.py` + 7 new in `test_protocol.cpp`. Playtest 2026-05-17: HUD per-kingdom decrement on hand-toss confirmed end-to-end. (A worktree install gotcha surfaced during this playtest — see the "Working from a worktree" section below.)
- **M7 Path A — kingdom-order gate** (2026-05-17): **DONE.** Ryujinx-verified end-to-end on a fresh save with the post-Sand fork: picked the "bottom slot" (which displays as Lake post-substitution, where Wooded would have been), arrived in Lake with full normal visuals. Enforces linear progression at SMO's two world-map bifurcations — post-Sand the player must clear Lake (≥8 AP-credit Lake moons) before Wooded, post-Metro must clear Snow (≥10 AP-credit Snow moons) before Seaside. Pairs with the apworld linear-chain `regions.json` already on main (`24a86dc apworld: linear kingdom chain + drop master Peace toggle`) so AP doesn't pre-grant Lake/Snow moons that would trivially satisfy the gate.
  - **Three-layer substitution architecture** (8 hooks, all in [WorldMapSelectHook.cpp](switch-mod/src/hooks/WorldMapSelectHook.cpp)):
    1. **Layer 1 — regular world-map UI** (4 hooks on `GameDataFunction::getUnlockWorldIdForWorldMap` by ptr-type overload). Catches Odyssey world-map opens AFTER the fork has been resolved. Verified firing as LiveActor + Scene overloads.
    2. **Layer 2 — post-Multi-Moon FORK cinematic** (2 hooks on `GameDataFunction::calcNextLockedWorldIdForWorldMap` by ptr-type overload). Catches the one-time "newly unlocked" presentation that plays right after collecting a kingdom's Multi-Moon. Verified firing as the Scene overload on slot 0 in the fresh-save fork playtest — this is what made the fork case work cleanly.
    3. **Layer 3 — stage-commit BACKSTOP** (2 hooks on `GameDataFunction::tryChangeNextStageWith{DemoWorldWarp,WorldWarpHole}`). Substitutes the `stage` arg if Layers 1+2 both miss. Substitution at this layer can produce broken cutscene visuals (Mario in destination kingdom without the Odyssey, frozen camera — see prior-iteration failure log below). Logs at WARN level so any backstop fire is a loud signal that an upstream catch needs adding.
  - **All substitutions go through the same helper** (`substituteSlotWorldId` in WorldMapSelectHook.cpp): if Orig returns a worldId for a gated kingdom whose prereq isn't met, substitute the prereq's worldId; otherwise pass Orig's value through. Log is throttled on (origin, index, orig_id) so per-frame UI re-queries don't flood.
  - **Gate policy lives in [KingdomOrderGate.{hpp,cpp}](switch-mod/src/game/KingdomOrderGate.cpp)** as a pure module — reads `ApState::ap_moons_kingdom[]` (populated by M6 phase A's ItemMsg handler) against thresholds `kLakeRequiredForWooded=8` and `kSnowRequiredForSeaside=10`. Supporting helpers in [KingdomUnlock.{hpp,cpp}](switch-mod/src/game/KingdomUnlock.cpp): `kingdomShortFromHomeStage` (stage-name routing), `kingdomShortFromWorldId` + `worldIdFromKingdomShort` (worldId↔kingdom mapping). The worldId helpers compose through M6 phase D's `kingdomBitForWorldId` so the four SMO/apworld order swaps (Sea/Snow, Boss/Sky) are honored — direct indexing into kKingdoms[] would mis-route the Seaside/Snow gate.
  - **UX side effect**: when both Lake and Wooded would appear in the same menu (post-Sand fork), both slots show "Lake" until the gate is satisfied — one natural, one substituted. Picking either flies to Lake. Cleaner than missing the fork entirely; could be polished by hooking `getUnlockWorldNumForWorldMap` to suppress the duplicate, but that requires careful index-mapping and isn't required for the gate to function.
  - **All 8 active symbols verified** in `scripts/check_nso_symbols.py` (HIT against SMO 1.0.0 main.nso). All symbol constants live in [HookSymbols.hpp](switch-mod/src/hooks/HookSymbols.hpp) under the "M7 Path A" section.
  - **Iteration history (for future debugging — five attempts before landing on the working design)**:
    1. **Skip Orig in `ChangeStageHook`** when destination is gated → world-map UI committed to the gated kingdom anyway; only that kingdom showed on next takeoff → soft-lock.
    2. **Skip Orig in `DemoWorldWarpHook`** (post-Sand cutscene auto-flight) → cutscene played, Mario returned to Sand, same UI soft-lock.
    3. **Substitute destination in `DemoWorldWarpHook`** (Wooded → Lake) → Mario landed in Lake but **no Odyssey ship, camera didn't follow Mario** — gated-kingdom cutscene assets were pre-loaded by earlier state-machine steps and stayed referenced after the destination flipped. Even nested-sanitizing the constructed `ChangeStageInfo` in a downstream `ChangeStageHook` didn't fix the visuals because the info object was already clean — the bug lives in the cutscene state, not the ChangeStageInfo.
    4. **Hook `StageSceneStateWorldMap::exeDemoWorldSelect`** thinking it was the post-A-press confirmation handler → log proved it ONLY fires once per world-map open for the *opening animation* on the currently-highlighted (current) kingdom. The actual confirmation goes through `exeDemoWorldComment` → `exeExit` and on inspection neither of those receives the chosen kingdom in `mNextStageName` either: the world-map state machine carries the cursor position in a state-machine-local field and only writes to `mNextStageName` at the moment of commit via `tryChangeNextStageWithDemoWorldWarp`.
    5. **Hook `isUnlockedWorld` to lie 'locked'** for gated kingdoms → the cursor could still land on Wooded; isUnlockedWorld isn't the cursor-selectability filter the world-map UI uses. Same playtest, **refuse `tryChange`** (return false without Orig) instead of substitute → SOFT-LOCKED the menu, only the previously-attempted gated kingdom showed next time (SMO's branch-selection state had registered "player picked Wooded" before tryChange was called).
  - **Why Layer 1 alone wasn't enough**: the post-Multi-Moon fork is a one-time cinematic that bypasses the regular world-map UI's per-slot query. On a clean save with the fork visible, `getUnlockWorldIdForWorldMap` never fired — `calcNextLockedWorldIdForWorldMap` is the fork-specific equivalent. Layer 2 catches it.
  - **Why Layer 3 exists despite the visual cost**: the playtest where Layer 2 wasn't yet wired showed `tryChange.Demo` firing with `stage='ForestWorldHomeStage'` for the fork — without an upstream catch, Mario would land in Wooded. Layer 3 ensures the gate is enforced as a last resort even if a future SMO update routes through a code path neither Layer 1 nor Layer 2 catches; the WARN log makes the visual cost visible as a signal to add the missing upstream catch.
- **M7 phase A — capture lock** (DONE 2026-05-16; separate work-item from Path A above, both shipped under the M7 umbrella). Captures Mario hasn't unlocked via AP now fail: `CaptureStartHook` trampoline (M4 read-only) flipped to deny-after-orig. After `Orig` runs and `getCurrentHackName` reports the SMO-internal hack_name (`TRex`, `Kuribo`, `KillerMagnum`, etc.), `captureBlocked(name)` checks `ApState::captures_unlocked.test(bit)`; if unset we enqueue a deferred `PlayerHackKeeper::forceKillHack(self)` to fire ~4s later. Reporting the AP location check is unconditional (preserves wire semantics — first touch sends `LocationCheck`, AP replies with the item, second touch succeeds). The journey to this design touched three real problems worth recording:
  1. **No pre-startHack name lookup exists.** OdysseyDecomp confirmed: `IUsePlayerHack` has only `getPlayerHackKeeper()`, no `getHackName`; `EnemyStateHackStart::tryStart` calls `rs::startHack(self, other, 0)` with a NULL third arg; the canonical name only becomes readable via `PlayerHackKeeper::getCurrentHackName()` AFTER `startHack` populates the keeper. So the deny path has to run *after* `Orig`, not before — there's no "refuse the SensorMsg" alternative that knows the cap name.
  2. **`captureBitFor()` was looking up against the wrong name space.** Pre-M7 the lookup table (`kCaptureNames` generated from apworld `items.json`) held English apworld names like `T-Rex` / `Bowser Statue`, but `getCurrentHackName()` returns SMO-internal Japanese-roman names like `TRex` / `StatueKoopa`. ~39 of 43 captures diverge (e.g. `Goomba`/`Kuribo`, `Bullet Bill`/`Killer`, `Banzai Bill`/`KillerMagnum`) — apworld is English, SMO internals are Japanese-engine. `captureBitFor` fail-opened (returned `0xff`) for nearly every capture. Fix: `scripts/sync_capture_table.py` now also reads `apworld/smo_archipelago/client/data/capture_map.json` and emits a parallel `kCaptureHackNames[i]` array; `captureBitFor` searches hack-names first (hot path — the deny gate) then falls back to apworld names (the M6-B apply path). Identity passthrough when `capture_map.json` is absent preserves fresh-clone behavior.
  3. **`cancelHack()` is a no-op when called from inside `startHack`'s trampoline.** First try: hook `startHack`, after `Orig` and the AP-check fire, call `cancelHack(self)`. Logs showed `BLOCKED hack=TRex — cancelling` + clean return, but Mario stayed captured. Swapped to `forceKillHack` (the "kingdom transition teardown" hammer) — released Mario but despawned the enemy actor on slow-cinematic captures (T-Rex). Final design defers the kill ~4s via two new `ApState` atomics (`pending_kill_keeper`, `pending_kill_at_ms`) drained by `smoap::hooks::tickPendingUncapture()` running once per frame from `DrawMainHook`. The delay solves both problems: (a) state machine has fully entered hack mode so `forceKillHack` actually fires, (b) it's funnier UX — Mario plays as the unowned enemy for a beat before getting snapped back. 1s was too short for T-Rex (camera broke + despawn), 4s clears every cinematic playtested.
  - Symbols: `kPlayerHackKeeperForceKillHack` (`_ZN16PlayerHackKeeper13forceKillHackEv`) added to `HookSymbols.hpp` and `scripts/check_nso_symbols.py`; mangling verified via `aarch64-none-elf-g++ -c`. Also added: `synthetic_uncapture_this_frame` flag on `ApState` for the standard "our own action — don't echo back to AP" defense-in-depth pattern.
  - Out of scope (M7B/M8 territory): cleaner Y-button-style release via `rs::endHack` or `PlayerHackKeeper::endHack(HackEndParam const*)`. Research (`PlayerHackKeeper.cpp` is header-only in OdysseyDecomp; `rs::endHack` IS decompiled and is the canonical "voluntary release, enemy survives" path) showed both options carry non-trivial risk: `rs::endHack` needs an `IUsePlayerHack**` indirection from our `LiveActor*` arg (SMO uses multi-inheritance — `reinterpret_cast` is unsafe), and `PlayerHackKeeper::endHack` direct needs a layout-correct `HackEndParam` (no header, sead-type alignment uncertain). Despawning the enemy is cosmetic; gameplay-correct behavior (no capture without the item) works. T-Rex (already promoted into the apworld during the M5.8 logic-audit pass — see `apworld: promote T-Rex …`) is the canonical gated-capture test case: collect it pre-grant → 4 s playtime as T-Rex then yanked back; AP-grant `T-Rex` → capture sticks.
- **M7 phase B** (deferred): goal detection. `EndingHook` (M3) is wired on `DemoPeachWedding::makeActorAlive` and is supposed to fire `goal_sent` + an AP `StatusUpdate{ClientGoal}` — needs playtest to confirm the chokepoint actually triggers on Bowser-defeat and that the bridge ships the goal cleanly. M4 added the idempotency guard.
- **PopTracker pack** (2026-05-17): **DONE — user-verified.** Independent logic-graph tracker that connects directly to AP's websocket alongside SMOClient. Generated from apworld data by [scripts/build_poptracker_pack.py](scripts/build_poptracker_pack.py) — single-file stdlib-only generator that mirrors the Manual id-allocation algorithm in [apworld/.../Game.py](apworld/smo_archipelago/Game.py) (verified: `Cap: Frog-Jumping Above the Fog`→`14481151500` and `Cascade: Our First Power Moon`→`14481151511` match the M5.7 playtest's observed AP ids exactly). Parser for the Manual `requires` mini-language (`|Name:N|`, `{Func(args)}`, `and`/`or`, paren grouping); translator produces PopTracker OR-of-AND access_rules. Per-region prereq chains flattened at build time via [regions.json](apworld/smo_archipelago/data/regions.json)'s `connects_to` graph; per-category yaml-option gates pulled from [categories.json](apworld/smo_archipelago/data/categories.json). Lua ports of all ~30 functions in [Rules.py](apworld/smo_archipelago/hooks/Rules.py) live in [poptracker/pack-src/scripts/logic.lua](poptracker/pack-src/scripts/logic.lua), guarded on the same `capturesanity` check the Python uses. Yaml options + goal selection live in a Lua `OPTIONS` table populated by `Archipelago:AddClearHandler` from `slot_data` (`fill_slot_data` in [__init__.py](apworld/smo_archipelago/__init__.py:356) already exports every non-common option) — all 20 logic-affecting options snap into place automatically; defaults match apworld defaults so offline-mode is sane.
  - **UI**: PopTracker has NO built-in locations panel or location-tree widget — the documented widget set is `container/dock/array/tabbed/group/item/itemgrid/map/layout/recentpins/text/canvas` (no `tree` / `locationtree` / `locations`). Locations are ONLY visible when placed as pins on a `map` widget. Pack ships a 740×560 dark-gray placeholder PNG (generated stdlib-only via `struct` + `zlib` in `make_solid_png`; ~2.5 KB) with the 16 kingdom buckets pinned on a 4×4 grid (Cap top-left, Captures bottom-right, ordering loosely follows linear-chain progression). Each kingdom is one top-level location with all its moons as `sections` (the DBFZ Manual-pack reference uses this flat shape — nested `children + sections` is two levels deeper than PopTracker accepts and silently breaks the location panel). Click a pin → kingdom drawer with section list; sections color by access-rule state.
  - **Iteration history** (3 swing-and-a-miss before user-verified): (1) tried `tracker_default: {type: "locationtree"}` — invented widget type, broke main view entirely; (2) added kingdom-level layout grouping (`children` of locations holding nested sections) — too deep for PopTracker's location format; (3) stripped layout to a `text` widget telling user to open View > Locations — that menu item doesn't exist, locations need maps to be visible at all. Map+pins approach is the only one that worked.
  - Pack zip ~27 KB; output at `poptracker/build/smo-poptracker-v<version>.zip`, gitignored — rebuild after any apworld change. 20 internal parser/translator/region-prereq tests pass (`python scripts/build_poptracker_pack.py --self-test`). Release workflow ([release.yml](.github/workflows/release.yml)) builds the zip alongside `smo.apworld` on every tagged release; both ship as GitHub release assets with their own sha256 checksums.
- **M8**: apworld extensions + in-game ImGui + polish (incl. dedicated AP-credit HUD overlay — see "What's definitely NOT done")

## Repository layout

```
C:\Users\maxwe\Documents\smo_archipelago\
  README.md                      Project overview
  CLAUDE.md                      ← this file
  LICENSE                        MIT
  .gitignore                     Note: third_party/ ignored; vendor/ tracked
  .gitmodules                    Submodules (vendor/Archipelago, lunakit-vendor)
  apworld/smo_archipelago/       Forked manual_smo_mp3 → smo_archipelago apworld + client
    __init__.py                  World class + SMOSettings + "SMO Client" Component reg
    data/                        items.json / locations.json / regions.json / categories.json
    hooks/                       Manual-framework hook surfaces (Rules, Options, World, ...)
    Data.py, Game.py, ...        Manual framework boilerplate
    ManualClient.py              Vestigial — NOT Launcher-registered; kept because the Manual
                                 framework references it. The active client is client/main.py.
    client/                      Python client (replaces the old standalone `bridge/`)
      __init__.py                Empty / lightweight; never pulls Kivy
      main.py                    Launcher entry point; `def launch(*args)` invoked via Component
      context.py                 SMOContext(CommonContext) + SMOClientCommandProcessor
      gui.py                     SmoManager(GameManager) — Kivy UI; imported lazily inside run_gui
      switch_server.py           asyncio TCP server on :17777; replay on HELLO
      protocol.py, state.py      Wire-format dataclasses + thread-safe state mirror
      datapackage.py, maps.py    AP id↔name + classifier + ShineMap / CaptureMap
      scout_cache.py, display.py Channel A: LocationScouts pre-fetch + label formatting
      commands.py                Pure `parse_command` for the /-commands in context.py
      config.py, logging_setup.py  Legacy TOML overlay (kept for back-compat) + log config
      data/                      shine_map.json + capture_map.json (gitignored; regenerated)
    tests/                       120 passing (11 skipped: live-AP gated on SMOAP_LIVE_AP=1
                                 + extraction tests need shine/capture maps present)
      pyproject.toml             Self-contained pytest config (importmode=importlib)
      conftest.py                Inserts apworld/smo_archipelago/ into sys.path
      seeds/                     Loopback test seeds (smo_loopback.yaml + gitignored out/)
  switch-mod/                    exlaunch C++ module — unchanged by the client merge
    CMakeLists.txt               Builds subsdk9 from lunakit stock templates
    src/
      main.cpp                   exl_main entry — installs hooks, spawns worker
      ap/{ApClient,ApState,ApConfig,ApFrameBridge,ApProtocol}.{cpp,hpp}
      ap/capture_table.h         AUTO-GENERATED (42 cap names)
      hooks/HookSymbols.hpp      8 mangled symbols
      hooks/{MoonGet,CaptureStart,ScenarioFlag,SaveLoad,Ending,MoonLabel}Hook.cpp
      game/{MoonApply,CaptureGate,KingdomUnlock}.{cpp,hpp}
      ui/ApHudOverlay.{cpp,hpp}
      util/{Json,Log}.{cpp,hpp}
    romfs/ap_config.json         INFORMATIONAL ONLY — bridge IP/port are baked in at
                                 compile time via CMake -DBRIDGE_HOST/-DBRIDGE_PORT. The
                                 runtime SD-read path was abandoned (MountSdCardForDebug
                                 fails on retail/newer FW). See ApConfig.cpp:1-8. Editing
                                 this JSON on the SD does NOTHING — rebuild instead.
    lunakit-vendor/              Vendored LunaKit submodule
  scripts/
    switch_smoke_test.py         Fake-Switch end-to-end test (formerly bridge_smoke_test.py)
    sync_capture_table.py        items.json → capture_table.h (use this; ps1 also exists)
    extract_shine_map.py         M5.8: NSP → romfs → shine_map.json + capture_map.json
    install_apworld.py           Zips apworld/smo_archipelago/ → vendor/.../custom_worlds/
    ap_generate.py, ap_server.py Archipelago Generate/MultiServer wrappers (auto-pip suppressed)
    .extract-venv/               Auto-created Python 3.12 venv (gitignored)
  docs/
    architecture.md              Two-tier diagram, threading, responsibilities
    wire-protocol.md             14 message types with examples
    build-windows.md             Toolchain install
    extract-moon-data.md         How to generate shine_map.json + capture_map.json
    install-switch.md            SD card layout, troubleshooting
  vendor/                        For submodules (Archipelago goes here)
  third_party/                   Local clones — gitignored
    SMO-manual-AP/               Reference clone of upstream Manual world
  poptracker/                      PopTracker pack for the logic-graph tracker
    pack-src/                      Hand-authored: manifest, init.lua, logic.lua
                                   (Lua ports of Rules.py), autotracking.lua,
                                   layouts. Map PNG + maps.json generated.
    build/                         Generated; gitignored — re-run scripts/
                                   build_poptracker_pack.py after apworld changes
```

**Note on `bridge/`**: the directory is GONE from the repo source. Contributors with a pre-merge
checkout will see a leftover `bridge/.venv/` (gitignored) — that's their old dev venv. The
bridge venv can be reused as the SMOClient venv (Archipelago's deps are a superset of what we
need); just point at it via `bridge/.venv/Scripts/python` when running tests.

## External paths (outside the repo)

| Path | Purpose |
|---|---|
| `C:\Users\maxwe\.switch\prod.keys` | Console keys (hactool default location). Also `dev.keys` |
| `D:\switch\` | User's microSD — DO NOT write large files here, it's the actual SD card |
| `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` | The authoritative plan (FW 21.2 + 1.0.0 simplification) |
| `C:\Users\maxwe\.claude\projects\C--Users-maxwe-Documents-smo-archipelago\memory\` | Auto-memory directory |

## Dev loop — Ryujinx FIRST, real Switch never as the primary test

The user's HOS increments a "title failed to launch" counter for SMO every time the game crashes during startup. After enough failures HOS shows "Corrupted data detected" prompts. Cart data is never actually damaged (Atmosphere overlays are runtime), but recovery costs the user real time (Settings → Data Management → Check for Corrupted Data, ~1 min, OR an unnecessary 30+ min reinstall if they don't know about that menu). **Never deploy a freshly-changed subsdk9 to their Switch as the first test.**

The flow:

```pwsh
# 0. ONE-TIME (after fresh clone or `git pull` that touched apworld/data/items.json):
#    Generate switch-mod/src/ap/capture_table.h. The file is gitignored — the
#    build will fail with "../ap/capture_table.h: No such file or directory"
#    on the first compile of CaptureGate.cpp until you run this.
python C:\Users\maxwe\Documents\smo_archipelago\scripts\sync_capture_table.py

# 1. Build (~10s)
cd C:\Users\maxwe\Documents\smo_archipelago\switch-mod
$env:DEVKITPRO = "C:/devkitPro"
& "C:/Program Files/CMake/bin/cmake.exe" -S . -B build -G Ninja `
    -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake `
    -DBRIDGE_HOST=192.168.1.187 `
    -DRYU_PATH=C:/Users/maxwe/AppData/Roaming/Ryujinx
& "C:/Program Files/CMake/bin/cmake.exe" --build build
# Post-build hook auto-deploys subsdk9+npdm+ap_config.json into
# %APPDATA%/Ryujinx/mods/contents/0100000000010000/smo-archipelago/
#
# Note: if Ninja isn't installed, swap `-G Ninja` for
#   `-G "Unix Makefiles" -DCMAKE_MAKE_PROGRAM=C:/devkitPro/msys2/usr/bin/make.exe`
# Same build product; verified end-to-end.

# 2. Boot SMO in Ryujinx. User does this manually:
#    cd C:\Users\maxwe\Documents\ryujinx-1.3.3 && .\Ryujinx.exe
#    (then double-click SUPER MARIO ODYSSEY in the game list)

# 3. After boot attempt, check for output:
type "C:\Users\maxwe\AppData\Roaming\Ryujinx\sdcard\atmosphere\contents\0100000000010000\smoap.log"
Get-Content (Get-ChildItem "$env:APPDATA\Ryujinx\Logs\Ryujinx_*.log" | Sort LastWriteTime -Descending | Select -First 1) -Tail 80
```

Ryujinx's log is gold — it surfaces `[rtld]` unresolved symbols, guest stack traces with C++ demangled names, and guest register dumps. **Far** more useful than the Switch's binary erpts. Always iterate here.

Only after Ryujinx boots clean → propose deploying to the real Switch:

```pwsh
& "C:/Program Files/CMake/bin/cmake.exe" --install build  # populates sd-overlay/
xcopy /E /I /Y C:\Users\maxwe\Documents\smo_archipelago\switch-mod\sd-overlay\atmosphere D:\atmosphere
```

If a Switch deploy ever causes the corruption icon: Settings → Data Management → Software → Super Mario Odyssey → Check for Corrupted Data. NOT a reinstall.

## Working from a worktree — `install_apworld.py` gotcha

If you're working in a `.claude/worktrees/<name>/` worktree (any agent spawned via Claude's spawn-task or via git-worktree directly), `scripts/install_apworld.py` writes to **the worktree's** `vendor/Archipelago/custom_worlds/smo.apworld`, NOT to the main checkout. The path is derived from `__file__` so it's always relative to whichever copy of the script you ran.

But the user **launches SMOClient from the Archipelago Launcher in the main checkout** (`C:\Users\maxwe\Documents\smo_archipelago\vendor\Archipelago\Launcher.py`). The Launcher loads custom_worlds from its own checkout's `custom_worlds/` directory — so it ignores anything you installed into the worktree.

**Symptom**: the Switch mod (rebuilt + deployed from the worktree to Ryujinx mods/) sends a brand-new message type (e.g. `DepositMsg` from M6 phase D) and the SMOClient log shows `unknown message type from Switch: <type>`. The mod is current; the apworld zip the bridge loaded is stale.

**Fix every time you ship from a worktree**: after `python scripts/install_apworld.py` in the worktree, also copy the freshly-built zip over the main checkout's:

```pwsh
Copy-Item -Force `
    C:\Users\maxwe\Documents\smo_archipelago\.claude\worktrees\<name>\vendor\Archipelago\custom_worlds\smo.apworld `
    C:\Users\maxwe\Documents\smo_archipelago\vendor\Archipelago\custom_worlds\smo.apworld
```

This bit M6 phase D 2026-05-17 — bridge dropped every `DepositMsg` until the zip was overwritten. Future cleanup option: make `install_apworld.py` accept a `--also-to <path>` flag, or detect a worktree and write to both. For now it's documented, not coded.

## Subsdk slot

Module ships as **`subsdk9`** at `sd:/atmosphere/contents/0100000000010000/exefs/subsdk9` — the lunakit default. SMO 1.0.0 has no subsdks in its exefs so the slot is free.

## Game dump (1.0.0)

User has a native SMO 1.0.0 NSP installed — no Atmosphere downgrade overlay. Local copies of `SMO_1.0.0.nsp` and the extracted `main.nso` (15.4 MB) live at `C:\Users\maxwe\Downloads\`. **Never commit these — copyrighted.** `.gitignore` covers `docs/main-*.nso` and the Downloads location is outside the repo.

For offline symbol verification: `bridge/.venv/Scripts/python scripts/check_nso_symbols.py C:\Users\maxwe\Downloads\main.nso`. The script decompresses the NSO segments (LZ4 block) and grep's the `.dynstr` table for the 8 mangled hook names. As of 2026-05-15 all 8 resolve.

## libnx extern "C" gotcha

Critical bug we hit twice. `lunakit-vendor/src/lib/nx/kernel/svc.h` and `lib/nx/result.h` declare functions WITHOUT any `extern "C"` wrapper. The wrapper is in the umbrella `lib/nx/nx.h`. From C++ TUs, **always `#include "lib/nx/nx.h"`**, never the inner headers directly. Including them direct gives C++ mangling at call sites (e.g. `_Z20svcOutputDebugStringPKcm`), the assembly stubs have C linkage, link succeeds, runtime gets unresolved-symbol from rtld, PC jumps to 0, process aborts.

## nn::fs SD mount

`sd:/...` paths in nn::fs are NOT accessible by default in our process. SMO doesn't mount the SD via the Nintendo SDK API (its asset path goes through `sead::FileDeviceMgr` to RomFS). To use `nn::fs::OpenFile("sd:/...")` we must call `nn::fs::MountSdCardForDebug("sd")` once. LunaKit does this by hooking `sead::FileDeviceMgr` ctor. We do it inline in our `GameSystemInitHook::Callback` (plus a fallback in `DrawMainHook` first-call). Without this, `nn::fs::CreateFile` aborts via internal `GetFreeSpaceSize` because "sd:" is unmounted.

## How to run the client

```pwsh
# Run tests (120 pass, 11 skipped — live-AP/extraction gated)
cd C:\Users\maxwe\Documents\smo_archipelago
.\bridge\.venv\Scripts\python -m pytest apworld\smo_archipelago\tests\

# Launch via the Archipelago Launcher (the canonical user flow)
.\bridge\.venv\Scripts\python vendor\Archipelago\Launcher.py
# → click "SMO Client" in the GUI

# Headless / scripted launch
.\bridge\.venv\Scripts\python vendor\Archipelago\Launcher.py "SMO Client" `
    --connect localhost:38281 --name Mario
```

SMO Client listens on `0.0.0.0:17777` (Switch TCP) by default — override via
`~/.archipelago/host.yaml` under `smo_options.switch_listen_port` or
`--switch-port` on the command line. (The host.yaml key is derived from the
shipped apworld zip stem `smo`, NOT from the AP game name `Spicy Meatball
Overdrive` and NOT from the in-repo source folder `smo_archipelago/`.)

Settings live in `host.yaml`:
```yaml
smo_options:
  switch_listen_host: "0.0.0.0"
  switch_listen_port: 17777
  shine_map_path: ""          # empty falls back to client/data/shine_map.json
  capture_map_path: ""
  deathlink_default: false
```

## AP loopback (recommended pre-Ryujinx test)

Validates the whole Switch↔Client↔AP stack without booting SMO. After fresh clone:

```pwsh
# Build apworld zip (re-run after any apworld/client/__init__.py change)
.\bridge\.venv\Scripts\python scripts\install_apworld.py

# Generate test seed (one-time per apworld change)
.\bridge\.venv\Scripts\python scripts\ap_generate.py `
    --player_files_path apworld\smo_archipelago\tests\seeds `
    --outputpath apworld\smo_archipelago\tests\seeds\out

# Unzip the .archipelago server file out of the player zip
.\bridge\.venv\Scripts\python -c "import zipfile, glob; [zipfile.ZipFile(z).extractall('apworld/smo_archipelago/tests/seeds/out') for z in glob.glob('apworld/smo_archipelago/tests/seeds/out/AP_*.zip')]"

# Host server (pane A)
.\bridge\.venv\Scripts\python scripts\ap_server.py --port 38281 `
    apworld\smo_archipelago\tests\seeds\out\AP_*.archipelago

# Launch SMO Client (pane B) — connects to localhost
.\bridge\.venv\Scripts\python vendor\Archipelago\Launcher.py "SMO Client" `
    --connect localhost:38281 --name Mario

# Drive a fake Switch (pane C)
python scripts\switch_smoke_test.py
# Expect: each `>> check` mirrored by a `<< item` within ~1s

# Or scripted via pytest:
$env:SMOAP_LIVE_AP="1"
.\bridge\.venv\Scripts\python -m pytest -v `
    apworld\smo_archipelago\tests\test_ap_loopback.py
```

Quick old-style smoke test (Switch-only, no AP server, just exercises the
SwitchServer):
```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\switch_smoke_test.py
```

## What's next

**M7 phase B — goal detection playtest.** `EndingHook` is wired on `DemoPeachWedding::makeActorAlive` since M3 and is supposed to set `ApState::goal_sent` + ship an AP `StatusUpdate{ClientGoal}`. Validation requires actually defeating Bowser in Ryujinx; expected to be a small task if the symbol resolves and the demo fires once-per-defeat as designed.

**M6 phase C — kingdom unlock + snapshot enumerate** is the natural next big-ticket item if M7B turns out clean. The M6-A AP-credit moon-counter substitution gates Odyssey-ship handoff; whether it also gates the in-game kingdom unlock flow needs validation. If not, `unlockWorld` is in the symbol catalog ready to bind. Snapshot enumerate (`enumerateOwnedShines` / `enumerateOwnedCaptures`) populates the M4.5 reconciliation stream the bridge already consumes — stubs are in `CaptureGate.cpp` / `MoonApply.cpp` and just need GameDataHolder traversal bodies.

## Adding new hook targets

8 symbols in `switch-mod/src/hooks/HookSymbols.hpp`. 3 come verbatim from lunakit's `src/program/main.cpp` `InstallAtSymbol(...)` calls; that's the canonical 1.0.0 source. For symbols lunakit doesn't hook, forward-declare the signature from `MonsterDruide1/OdysseyDecomp` (a 1.0.0 decompilation) and pass through `aarch64-none-elf-g++ -c` + `nm` to get the mangled name — Itanium ABI mangling is deterministic from the signature, so forward decls are sufficient. If a function turns out to be inlined on 1.0.0, fall back to delta-polling the relevant field from `drawMain` (one-frame latency, zero symbol dependency).

## User collaboration style (from memory)

- **No blind Switch deploys.** Every subsdk build must boot clean in Ryujinx first. Failed Switch launches trigger HOS corruption flag → painful recovery. Memory: `feedback_no_blind_switch_deploys.md`.
- **Show output before fix**: when the user reports an error from a tool, wait for the full output before committing to a remediation tool call. Memory: `feedback_show_output_first.md`.
- **Atmosphere `enable_log_manager` is broken** on the user's HATS 1.11.1 pack — enabling it crashes Atmosphere at boot. Don't suggest. Memory: `feedback_atmosphere_log_manager_broken.md`.
- User has Switch homebrew experience (Goldleaf, prod.keys from Lockpick_RCM, FW 21.2 downgraded Switch with native SMO 1.0.0 install, Ryujinx 1.3.3 installed at `C:\Users\maxwe\Documents\ryujinx-1.3.3` with firmware + game imported).
- Windows 11, PowerShell-default shell, Python 3.14.3 installed.
- D: drive is the microSD card — never write large files there.
- PowerShell execution policy is restrictive (`-ExecutionPolicy Bypass` is denied). Use Python alternatives for scripts.

## Test commands worth knowing

```pwsh
# Client tests (Python) — 120 pass + 11 skip (live-AP / extraction gated)
cd C:\Users\maxwe\Documents\smo_archipelago
.\bridge\.venv\Scripts\python -m pytest apworld\smo_archipelago\tests\ -v

# Switch-module host tests (C++ via standalone msys2 mingw64 g++; devkitPro
# does NOT ship a host compiler — devkitA64 is AArch64-only). Build + run
# from PowerShell. PATH prepend is required so the produced exe finds its
# mingw runtime DLLs (libstdc++-6.dll etc.).
$env:Path = "C:\msys64\mingw64\bin;" + $env:Path
& "C:\msys64\mingw64\bin\g++.exe" -std=c++20 -Wall -Wextra -O0 -g `
    switch-mod/tests/test_json.cpp switch-mod/src/util/Json.cpp `
    -Iswitch-mod/src -o test_json.exe
.\test_json.exe
& "C:\msys64\mingw64\bin\g++.exe" -std=c++20 -Wall -Wextra -O0 -g `
    switch-mod/tests/test_protocol.cpp switch-mod/src/ap/ApProtocol.cpp `
    switch-mod/src/util/Json.cpp -Iswitch-mod/src -o test_protocol.exe
.\test_protocol.exe

# Switch-module cross build (devkitA64 + Windows CMake; not msys2 cmake)
cd C:\Users\maxwe\Documents\smo_archipelago\switch-mod
$env:DEVKITPRO = "C:/devkitPro"
& "C:/Program Files/CMake/bin/cmake.exe" -S . -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE=lunakit-vendor/cmake/toolchain.cmake
& "C:/Program Files/CMake/bin/cmake.exe" --build build
& "C:/Program Files/CMake/bin/cmake.exe" --install build  # populates sd-overlay/

# Regenerate capture table after apworld change
python C:\Users\maxwe\Documents\smo_archipelago\scripts\sync_capture_table.py

# Loopback smoke test (with SMOClient running separately)
python C:\Users\maxwe\Documents\smo_archipelago\scripts\switch_smoke_test.py
```

**Critical cross-build gotcha**: msys2 cmake (`/c/devkitPro/msys2/usr/bin/cmake`) inside Git Bash CANNOT find DEVKITPRO (it expects `/opt/devkitpro` mount which Git Bash doesn't have). Use the Windows CMake at `C:/Program Files/CMake/bin/cmake.exe` with `DEVKITPRO=C:/devkitPro` env var.

The build also needs `set_source_files_properties(... PROPERTIES COMPILE_FLAGS "-fpermissive")` on lunakit's vendored sources because devkitA64 GCC 15 rejects const-T `std::construct_at` in lunakit's `typed_storage.hpp`. Already wired in our CMakeLists.

## Game data extraction (M5.8)

Done — see `docs/extract-moon-data.md`. One command after `git clone` produces both the moon map and the capture map:

```pwsh
python scripts/extract_shine_map.py --nsp <SMO_1.0.0.nsp>
```

Self-bootstraps a Python 3.12 venv with `oead` (no 3.13 wheel exists), runs `hactool` to extract RomFS (~5 GB cache at `.romfs-cache/`), then:

- **Moons**: walks the 17 `ShineList_<HomeStage>.byml` files in `SystemData/ShineInfo.szs`, joins each `ObjId` against the per-stage MSBT in `LocalizedData/USen/MessageData/StageMessage.szs` under key `ScenarioName_<ObjId>`. 775 entries → `apworld/smo_archipelago/client/data/shine_map.json` (gitignored).
- **Captures**: walks `SystemData/HackObjList.szs` (130 internal `HackName` strings), joins against `SystemMessage.szs/HackList.msbt` where the label *is* the internal name and the value is the English string. 52 deduped entries → `apworld/smo_archipelago/client/data/capture_map.json` (gitignored).

Ground-truth conventions discovered during build:
- Moon MSBT lookup is in the **per-shine StageName MSBT**, NOT the HomeStage MSBT — sub-stages like `PushBlockExStage` carry their own `ScenarioName_<obj>` entries.
- Moon kingdom assignment comes from **which BYML the shine came from** (HomeStage), not by the per-shine StageName prefix — those don't match for `*ExStage`/`*Zone` sub-stages.
- Capture lookup is direct: HackList.msbt label is the internal name, value is the English. No key construction needed.
- `pymsyt` only knows BotW's control-code set and chokes on SMO's control code 6. We ship a ~150-line in-tree MSBT reader in `scripts/extract_shine_map.py` that generically skips all `0x0E…/0x0F…` sequences.
- The Japanese-internal → English capture mapping is **NOT publicly published anywhere** — lunakit/OdysseyDecomp use the internal names as code identifiers but never alongside English equivalents. Per the user's IP-safety stance, captures must be extracted at user-runtime (same as moons), not hand-coded.

Cross-validation: 100% of both apworld moons (436/436) and apworld captures (43/43) resolve. Out-of-apworld-scope SMO entries (339 moons, 7 captures) are emitted anyway so future apworld expansion picks them up automatically.

A small `CAPTURE_NAME_ALIASES` table in the extractor handles 6 cases where the apworld deliberately diverged from Nintendo's strings (collapsed multi-piece variants, prefix renames, casing). Apworld typo fixes from M5.8 stage 1 (10 moon-name corrections in `apworld/.../locations.json`) are also checked in.

## Known unknowns / risks

1. **`PlayerHackKeeper::startHack` may not be a single chokepoint** — capture entry can split across multiple functions per cap-type. Secondary read-only check on `CapTargetInfo::isCaptureTarget` from the frame pump if the trampoline misses cases.
2. **Synthetic moon grant** must not retrigger our own hook — `ApState::synthetic_grant_this_frame` guard exists, plus belt-and-braces dedupe by `locations_checked` hash set.
3. **`Game.py` game-name guard**: bridge should compare `game_name` against `RoomInfo` at startup to catch seed mis-pairing. Not yet implemented; M4 todo.
4. **DemoPeachWedding hook fires for the wedding cutscene** which is the canonical SMO ending. If 1.0.0 names that demo differently (unlikely given OdysseyDecomp targets 1.0.0), the symbol won't resolve and we'd fall back to hooking a `setMainScenarioNo` call with the post-Bowser scenario value.

## M6.6 (deferred, next milestone)

Channel B — Cappy speech bubble for items arriving *outside* the
moon-get cutscene window (other players' checks routing items to us;
late echoes; kingdom-unlock items; capture-unlock items). The wire
format and bridge generation logic were sketched in
[i-wrote-a-plan-fluffy-otter.md](../../.claude/plans/i-wrote-a-plan-fluffy-otter.md);
the unknown is the Switch UI mechanism. Three candidates to spike:

1. **Hook SMO's `CapMessenger`** if it exists — lowest effort. Grep
   `OdysseyDecomp/src/Player/` for a class that surfaces tutorial-style
   speech bubbles. Confirmed missing from `.romfs-cache/syms310.ld`
   (that file's a sparse subset) but should appear in the real
   `main.nso` dynsym — re-run `scripts/check_nso_symbols.py` with
   candidate symbols added inline.
2. **Hijack the tutorial-bubble pane** — overwrite its text via
   `al::setPaneStringFormat` (already used by M6-A.5) and trigger its
   appear-animation. Medium effort.
3. **Custom toast overlay** via `agl::DrawContext` + a hand-rolled
   layout — pushed to M8 unless 1 + 2 both bust.

Bridge-side `CappyMsg` could ship ahead of UI as Channel-B-prime "log
only" — the mod just `SMOAP_LOG_INFO`s incoming `cappy` messages until
the UI mechanism lands. Useful for proving the AP→Bridge→Switch path
works end-to-end before committing to a UI choice.

## Other follow-ups for next agents

- **`docs/extract-moon-data.md` could mention the M6 A.5 dependency**.
  Today it documents how to generate `shine_map.json` for the M5/M5.7
  use case; Channel A *also* hard-depends on it. A new agent might
  think "I don't need moons resolved, I just want the cutscene labels"
  and skip the extract step — that's the same fail mode as the M6 A.5
  playtest above.
- **`MAX_MOON_LABEL_BYTES = 30` is empirical**. Playtest only confirmed
  that short labels (`Got Cap Power Moon!`, ~20 bytes) render. Longer
  labels at the 30-byte cap may overflow the SMO font's pane width.
  Worth a separate playtest with a deliberately long label (REPL:
  `label Sent Wooded Kingdom Power Moon -> VeryLongPlayerName`).
- **Scout-cache cold-warmup race** — first 100-500 ms after AP
  `Connected` the scout cache hasn't absorbed all 560 `LocationInfo`
  entries yet. If Mario collects a moon in that window, bridge sends
  the LocationCheck but compose_moon_label returns None (cache miss),
  so cutscene shows vanilla. Mild UX issue; the M4.5 state-replay path
  on disconnect/reconnect always hits this case (no labels for
  retroactively-applied checks). Could mitigate by waiting on the
  scout cache to fully warm before flipping `display_enabled = True`.
- **PopTracker pack is visually plain**: ships a 740×560 dark-gray
  placeholder PNG with 16 kingdom buckets on a 4×4 grid (see
  `scripts/build_poptracker_pack.py::make_solid_png`). Functional but
  ugly. A polish pass — proper kingdom artwork (one map per kingdom or
  a single composite world-map background), themed pin icons, maybe a
  side-panel that groups moons by sub-region — would make the tracker
  feel like a real companion app rather than a wireframe. Lowest-effort
  win: replace `make_solid_png` with a baked-in PNG of the SMO
  world-map art. Bigger win: per-kingdom maps with moon pins placed at
  approximate in-world coordinates (would need coords sourced from the
  M5.8 BYML walk; ShineInfo doesn't currently emit positions but
  could). Pack-generator rebuild stays single-command.
- **`apworld/.../data/{items,locations,regions}.json` invariant**: the
  Multi-Moon rework removed the kingdom-agnostic `Power Moon` item but
  it was referenced in 19 `|Power Moon:N| or ...` branches across
  `regions.json` + `locations.json`. The DataValidation pass at seed
  gen catches this loudly. **Future agents removing or renaming any
  item must grep both files for the bare name and update all
  `requires` strings.** Today this is a manual discipline; a CI lint
  would catch it.
- **2D moons aren't recolored by item type yet.** The
  `ShineAppearanceHook` inline-patches 4 BL sites inside `Shine::init`
  (3D moon actor) — the 2D moon variant collected in side-scrolling
  mural rooms goes through a different actor class and bypasses every
  patched offset, so those shines show vanilla yellow regardless of AP
  classification. Symmetric inline patches on the 2D shine init path
  would close the gap; resolve the 2D shine class via OdysseyDecomp
  (`Shine2D` / `Shine2DMap` / similar) and find its
  `rs::setStageShineAnimFrame` call sites the same way Kgamer77 found
  the 3D ones.

## What's definitely NOT done

- On-screen status overlay — deferred to M8 per user Q&A; M3 ships heartbeat-to-lm-log instead (web tracker is the canonical source of truth)
- HELLO `cap_table_hash` field is empty — populated in M4 once we hash the generated `capture_table.h`
- **AP-credit HUD overlay (M8)**: M6 phase A hooks `getCurrentShineNum`/`getGotShineNum` to return AP-credit-only counts (not orig+credit). The natural HUD shows our AP count — visually weird: a locally collected moon does NOT bump the counter even though the shine appears in the shine list. A dedicated ImGui-style AP overlay (à la lunakit devgui) belongs in M8 to surface AP credit info in a clearer, separate UI element. Hooks lying about the natural counter is a stopgap.
- **`getGotShineNum` doesn't fire in normal gameplay**: M6 phase A playtest showed the per-kingdom counter hook never fires when Mario plays in Cascade. SMO's natural per-kingdom counter reads from a different code path. The hook is harmless (returns AP credit when called); if a future code path does call it the credit lands correctly. Kingdom-progression gating via moon counts is therefore an open question — phase B / M6.x may need to land `unlockWorld` for explicit AP-gated kingdom unlocks rather than relying on moon-count substitution.
- **Cleaner M7 uncapture animation (M8 polish)**: M7 phase A uses `PlayerHackKeeper::forceKillHack` to release Mario from a blocked capture. It works (Mario returns to himself, gameplay is correct) but uses SMO's "kingdom transition teardown" code path, so the captured enemy actor despawns instead of being left alive and walking away. The visual is jarring on big captures like T-Rex (the dinosaur vanishes mid-frame). Revisit if player feedback flags this. Two researched alternatives, both with non-trivial risk that's why they didn't ship in M7-A:
  - **`rs::endHack(IUsePlayerHack**)`** — the canonical "voluntary release, enemy survives" path (same one the Y button takes, internally calls `initHackEndParam` → `PlayerHackKeeper::endHack`). Mangled `_ZN2rs7endHackEPP14IUsePlayerHack`. Risk: needs an `IUsePlayerHack**` indirection, but our `startHack` 4th arg is `al::LiveActor*` and SMO uses multi-inheritance — a raw `reinterpret_cast` to `IUsePlayerHack*` is unsafe (pointer-adjustment unknown without the inheritance graph). Would need vtable probing or a runtime cast helper.
  - **`PlayerHackKeeper::endHack(HackEndParam const*)`** direct — mangled `_ZN16PlayerHackKeeper7endHackEPK12HackEndParam`. Pass a stack-allocated `HackEndParam` with `quat.w = 1.0f` and `escapeScale = 1.0f`; other fields zero. Risk: `HackEndParam` layout depends on `sead::Vector3f` (12 B, 4-align) and `sead::Quatf` (16 B, possibly 16-aligned if SIMD); a wrong layout = function reads garbage. Mitigation: runtime-probe with sentinel bytes in Ryujinx before committing offsets. Lower risk than option 1.
  - Source: research summary in M7 phase A milestone commit message; OdysseyDecomp `src/Util/PlayerHackFunction.cpp` (decompiled body of `rs::endHack`) and `src/Player/PlayerHackKeeper.h` (`HackEndParam` declaration; `.cpp` is header-only in the public decomp).

## M6 phase-A playtest loop

Item injection runs through the AP server console, the same way every
other apworld does it. Connect a slot, then from the AP server's command
prompt:

```
/send Mario Cascade Kingdom Power Moon
/send Mario Cascade Kingdom Multi-Moon
/send Mario Goomba
/send Mario Sand Kingdom         (or whatever the kingdom-unlock item is named)
/hint Mario Cap: Frog-Jumping Above the Fog
```

(The earlier `/grant`, `/capture`, `/kingdom` client-side commands were
removed 2026-05-17. They duplicated `/send` and had a name-resolution
bug on the AP-received path — items arrived with no `name` field and
rendered as `?` in-game. Fix was a one-line change in
[datapackage.py](apworld/smo_archipelago/client/datapackage.py)
`ClassifiedItem.to_ref()` to always populate `name`, regardless of
ItemKind. Regression test in [test_commands.py](apworld/smo_archipelago/tests/test_commands.py).)

The surviving SMOClient `/`-commands are debug-only and run inside the
Kivy command bar:

```
/label Sent Cap Power Moon -> P3       (M6 phase A.5 — visual test of Channel A)
/smo_status                            (read-only tracker state)
/inject_deathlink TestRig manual       (synthesize an inbound DeathLink, no AP needed)
/help
```

`/label <text>` writes a `MoonLabelMsg` directly to the Switch's
`pending_moon_label` slot — useful for visually testing the cutscene-label
hook standalone (collect any moon in Ryujinx within ~4s and the text
appears in the moon-get cutscene). Real bridge↔AP Channel A use needs a
live AP server so the `LocationScouts` warmup populates the
`scout_cache` from which `_dispatch_check` synthesizes labels
on-the-fly.

## M7 Path A playtest loop

After a build in this worktree (`cmake --build switch-mod/build`), deploy to
Ryujinx. If the build was configured without `-DRYU_PATH` the post-build hook
doesn't auto-deploy — either reconfigure with
`-DRYU_PATH=C:/Users/maxwe/AppData/Roaming/Ryujinx` and rebuild, or copy
manually:

```pwsh
$RYU = "$env:APPDATA\Ryujinx\mods\contents\0100000000010000\smo-archipelago"
Copy-Item C:\Users\maxwe\Documents\smo_archipelago\.claude\worktrees\kind-matsumoto-562d93\switch-mod\build\subsdk9  $RYU\exefs\subsdk9
Copy-Item C:\Users\maxwe\Documents\smo_archipelago\.claude\worktrees\kind-matsumoto-562d93\switch-mod\build\main.npdm $RYU\exefs\main.npdm
```

Tail the mod log in another pane:

```pwsh
Get-Content "$env:APPDATA\Ryujinx\sdcard\atmosphere\contents\0100000000010000\smoap.log" -Wait -Tail 80
```

**Validation cases**:

1. **Fresh save, post-Sand Multi-Moon fork**: the "newly unlocked" cinematic
   should present Lake at both slots (where Wooded would have been is now
   Lake — duplicate is the documented UX cost). Picking the bottom slot
   should fly to Lake with full normal visuals. Expected log:
   ```
   [wmap.menu.NextLocked.Scene] SUB slot=0 origId=3 (Wooded) -> prereqId=4 (Lake) have=0 need=8
   [wmap.tryChange.Demo] FIRE stage='LakeWorldHomeStage' kingdom=Lake gated=0
   ```
   (No `BACKSTOP` line — Layer 2 caught it upstream of tryChange.)

2. **Regular world-map open** (post-fork, any later save): same Wooded→Lake
   substitution but via Layer 1:
   ```
   [wmap.menu.Id.Scene] SUB slot=3 origId=3 (Wooded) -> prereqId=4 (Lake) ...
   ```

3. **Allow path**: pick Lake (not gated) → no SUB line, no log noise, normal
   flight.

4. **Prereq satisfied**: grant 8 Lake moons via the AP server console
   (`/send Mario Lake Kingdom Multi-Moon` ×3 = 9 ≥ 8), re-open world map →
   Wooded appears as Wooded (no SUB), picking it flies cleanly.

5. **Same flow for Snow/Seaside post-Metro fork** — symmetric, threshold
   `kSnowRequiredForSeaside = 10`.

**If you ever see a `BACKSTOP substituting` WARN** in the log: a code path
neither Layer 1 nor Layer 2 caught reached tryChange. Mario will still go to
the prereq kingdom but with potentially broken cutscene visuals (Odyssey
missing, frozen camera). Add a new hook for the missing upstream entry point;
the BACKSTOP guarantees functional gating in the meantime.

**Kill switch**: flip `kGateEnabled = false` in
[WorldMapSelectHook.cpp](switch-mod/src/hooks/WorldMapSelectHook.cpp) to
disable all substitution while keeping the throttled "SUB" log lines that
show what WOULD have been substituted — useful for debugging without
modifying game behavior.

**REPL commands referenced above (`/grant ...`)** were removed in M6 phase D's
playtest cleanup — see the renamed commands section above. To grant Lake
moons for case 4, use the AP server console:
`/send Mario Lake Kingdom Multi-Moon` (×3 = 9 moons, satisfies threshold 8).
