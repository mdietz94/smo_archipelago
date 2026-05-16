# CLAUDE.md — context for the next session

This file is a fast-load brief for picking up the **Spicy Meatball Overdrive** project cold. (Project was previously called `SMOArchipelago`; the directory name `smo_archipelago/` and the Python/apworld package identifiers still spell "smo" — they parse as "Spicy Meatball Overdrive" now and renaming them would break existing seeds, configs, and the deployed mod path.) Read this file first, then `docs/architecture.md` and the plan file at `C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md`.

## ⚠️ CRITICAL: Never commit Nintendo IP

This repository is open-source and built on a careful line: **functional identifiers and reference apworld names are okay; bulk-extracted Nintendo content is not.** A misstep here exposes the user to DMCA risk. Before any commit, audit `git status` + `git diff` and refuse to stage anything from this list:

**Must NEVER be committed (already gitignored — keep it that way):**
- `bridge/smo_ap_bridge/data/shine_map.json` — full extracted (stage, obj_id) → display-name table. Generated per-machine by `scripts/extract_shine_map.py`. ~775 verbatim Nintendo USen strings.
- `bridge/smo_ap_bridge/data/capture_map.json` — `hack_name → english_name` table. ~52 verbatim Nintendo USen strings.
- `bridge/smo_ap_bridge/data/shine_map_review.json` and `capture_map_review.json` — diagnostics that include the same strings.
- `.romfs-cache/` — extracted RomFS (~5 GB of Nintendo assets).
- `scripts/.extract-venv/` — local Python 3.12 venv (not IP, but big and machine-specific).
- `docs/main-*.nso`, `*.nsp`, `*.nca`, `*.byml`, `*.szs`, `*.msbt` — any raw Nintendo binary.
- `prod.keys` / `dev.keys` / `title.keys` — Switch keys are themselves IP-sensitive.
- Any moon-name list, capture list, or stage list of more than ~5 entries pasted into a doc, comment, or commit message as illustrative content — bulk transcription is the same exposure as the file.

**Generally OK (already in the repo, established by upstream forks):**
- `apworld/smo_archipelago/data/locations.json` and `items.json` — the 565 community-curated location names + 42 capture names. Forked from the public [empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP) Manual world. Edits are fine; bulk additions from a romfs dump are not — alignment with Nintendo's MSBT should happen one mismatch at a time, not as a wholesale copy.
- Functional identifiers like `WaterfallWorldHomeStage`, `obj214`, `ScenarioName_<ObjId>`, `ShineList`, kingdom internal names (`CapWorld`/`SkyWorld`/etc.). These appear in every public SMO modding project (lunakit, MoonFlow, OdysseyDecomp) and are functional, not expressive.
- The one M5.7 anchor entry (`"Our First Power Moon"`) appears in CLAUDE.md, the test suite, and docs as a known ground-truth datapoint. One name as a verifiable test fixture is fine; a list of names is not.

**Safe pattern**: anything that requires a user to run `scripts/extract_shine_map.py` to produce stays in the gitignore. If you find yourself wanting to commit a piece of data so the next agent has a richer starting point, instead document where to regenerate it — see `docs/extract-moon-data.md` for the model.

**If you've staged something questionable**: `git restore --staged <path>` to unstage, then either delete the file or add it to `.gitignore` before retrying. Never override `.gitignore` with `git add -f` for SMO content. When in doubt, ask the user.

## What we're building

A real Archipelago client for **Super Mario Odyssey on a modded Switch (FW 21.2, native SMO 1.0.0 install, Atmosphere CFW)**. Replaces the existing Manual checklist client ([empathy-mp3/SMO-manual-AP](https://github.com/empathy-mp3/SMO-manual-AP)) — an honor-system tick-the-boxes app — with an in-game module that detects moons/captures/scenario events automatically, applies received items live, and enforces capture locks until the AP item arrives.

### Architecture (three tiers)

```
[ Switch / SMO ]  <--TCP/JSON LAN-->  [ PC Bridge (Python) ]  <--websocket-->  [ AP server ]
   exlaunch                              CommonContext                              archipelago.gg
   LunaKit headers                       Flask web tracker                          or self-host
   ImGui overlay (M8)                    Forked apworld
   HUD overlay (M3)
```

The PC bridge owns AP-protocol complexity (websocket + deflate + TLS + reconnect). Switch speaks a small line-delimited JSON protocol on port **17777**. Full wire format: `docs/wire-protocol.md`.

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
| **Game name `Manual_SMO_archipelago`** | Distinct from Manual client's `Manual_SMO_mp3`. Seeds are intentionally incompatible |

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
- **M5.5**: AP server live integration — **DONE 2026-05-15.** Forked apworld zipped to `vendor/Archipelago/custom_worlds/smo_archipelago.apworld` via `scripts/install_apworld.py`. Seed generation via `scripts/ap_generate.py` (thin wrapper that pre-sets `ModuleUpdate.update_ran = True` to suppress AP's auto-pip on world-specific deps). MultiServer wrapper at `scripts/ap_server.py`. Bridge ↔ local AP loopback validated end-to-end: `>> check Cap: Frog-Jumping Above the Fog` → bridge translates → `LocationChecks` to AP → AP sends `ReceivedItems` → bridge forwards `ItemMsg` to fake-Switch (all under 1s per round-trip). Bridge fix in `ap_client.py::_populate_datapackage_from_ctx` hydrates `self._dp` from CommonContext's `location_names`/`item_names` on `Connected` (CommonContext satisfies its own lookup from Archipelago's shipped `network_data_package.json` and never relays a `DataPackage` packet that our `on_package` could catch). Regression test `bridge/tests/test_ap_loopback.py` skips unless `SMOAP_LIVE_AP=1`; 43 existing tests still green. Test seed at `bridge/test_seeds/smo_loopback.yaml` (gitignored output at `bridge/test_seeds/out/`).
- **M5.7**: Ryujinx E2E — **DONE 2026-05-15.** First real moon traversed the whole stack: Mario collects "Our First Power Moon" in Ryujinx → `MoonGetHook` fires with `stage=WaterfallWorldHomeStage, obj=obj214` → `[pump] Send 102 bytes` → bridge resolves via `shine_map.json` → `LocationCheck id=14481151511` to AP → AP records check, places "Snow Kingdom Power Moon" item → `ReceivedItems` echoed → bridge forwards `ItemMsg` to mod (mod's inbound ring receives it; M6 application still stubbed). Three real bugs surfaced + fixed: (a) mod's `BRIDGE_HOST` was baked at the stale M3-era LAN IP (rebuilt with `-DBRIDGE_HOST=127.0.0.1` for Ryujinx-on-same-host); (b) `shine_map.json` seed entries used aspirational `MoonOurFirst`-style symbolic names but `ShineInfo::objectId` actually emits the placement-file ref `obj214` — confirmed via MoonFlow's public `ShineInfo` schema, replaced with 1 verified entry; (c) `ap_client.report_check` silently returned on `locations_checked` dedup, which combined with persistent `AP_*.apsave` from the M5.5 smoke test masked working pipeline as "moon arrived but nothing happened" — added explicit forwarding-vs-skip log lines. Diagnostic logging shipped permanently: `MoonGetHook` probe (`obj`/`scen`/`uid`), `ApClient::pumpOnce` `[pump]` traces, `ap_client.report_check` forwarding-distinction lines. These were load-bearing observability — every issue would have been silent without them.
- **M5.8**: full moon + capture data extraction — **DONE 2026-05-15.** Single command `python scripts/extract_shine_map.py --nsp <SMO_1.0.0.nsp>` produces a complete 775-entry `shine_map.json` AND 52-entry `capture_map.json`. Self-bootstraps a Python 3.12 venv with `oead` (no 3.13 wheel available); auto-extracts romfs via `hactool` (PFS0 → program NCA → RomFS, ~5 GB cached at `.romfs-cache/`).
  - **Moons**: walks `SystemData/ShineInfo.szs` (17 BYML kingdom shine lists) and joins against per-stage MSBT in `LocalizedData/USen/MessageData/StageMessage.szs` under `ScenarioName_<ObjId>` keys. The MSBT must be the per-shine StageName MSBT (sub-stages like `PushBlockExStage` own their own messages), and kingdom assignment must come from the HomeStage BYML container (sub-stage names don't match `CapWorld*` etc.).
  - **Captures**: walks `SystemData/HackObjList.szs` (130 internal `HackName` strings) and joins against `LocalizedData/USen/MessageData/SystemMessage.szs/HackList.msbt` where the label *is* the internal name and the value is the English form. A small `CAPTURE_NAME_ALIASES` table handles 6 cases where the apworld deliberately diverged from Nintendo (collapsed multi-piece variants like `Picture Match Part (Mario)` → `Picture Match Part`, prefix renames like `Cheep Cheep (Snow Kingdom)` → `Snow Cheep Cheep`, casing like `Bowser statue` → `Bowser Statue`). Investigation showed no public repo publishes the Japanese-internal → English mapping (only the internal names appear in lunakit / OdysseyDecomp as code identifiers), so extraction is the only safe path.
  - **MSBT parser**: shipped as a ~150-line in-tree reader because `pymsyt` only knows BotW's control codes and chokes on SMO's control code 6.
  - **Cross-validation**: 100% (436/436 moons + 43/43 captures) of apworld entries resolve. Emitted files cover the full 775 + 52 SMO entries — extras (339 out-of-apworld-scope moons, 7 out-of-scope captures) emitted so future apworld expansion picks them up automatically. (T-Rex was promoted into the apworld in the logic-audit pass; pre-promotion it was 42/42 + 8 out-of-scope.)
  - **IP discipline**: all 4 generated files (`shine_map.json`, `shine_map_review.json`, `capture_map.json`, `capture_map_review.json`) are gitignored. Nine tests in `bridge/tests/test_shine_map_extraction.py` validate schema/count/dedup/anchors for both maps (auto-skip when files absent). Also fixed 10 apworld typos in `apworld/.../locations.json` (e.g. `"Cafe?"` → `"Café?"`, `"By the Falls"` → `"by the Falls"`). Full workflow in `docs/extract-moon-data.md`.
- **M6 phase A**: AP-credit moon counter HUD substitution — **DONE 2026-05-15.** Two new trampoline hooks (`ShineNumGetHook` on `GameDataFunction::getCurrentShineNum`, `ShineNumByWorldGetHook` on `getGotShineNum`) drop `orig` and return AP-credit-only counts. `ApState` gains `ap_moons_unkingdomed` (truly-generic "Power Moon" credits) + `ap_moons_kingdom[17]` (kingdom-tagged credits, indexed by `kingdomBitFor`). `applyOnFrame` moon arm rewritten to bump credit counters with rich logging (`[m6-moon]` lines); Multi-Moon items grant +3, single-moon +1, kingdom-less generic credits go to `ap_moons_unkingdomed` and only show in the global counter. setGotShine runs untouched so the shine list correctly reflects local pickups — only the visible counter is AP-gated. Validated in Ryujinx (2026-05-15): local moon collection → HUD stays 0, Odyssey ship rejects the moon ("doesn't count"); REPL `grant Cascade Kingdom Power Moon` → HUD ticks to 1, Mario can hand it to the Odyssey; `grant Snow Kingdom Power Moon` rejected by the Cascade Odyssey (kingdom-specific routing works); pre-existing save moons disappear from the visible counter (orig is fully suppressed). `getGotShineNum` hook resolves and fires when explicitly invoked but **never fires during normal Cascade play** — SMO's natural per-kingdom counter reads shine flags directly; the global `getCurrentShineNum` does most of the work for HUD + Odyssey gating. Two new symbols mangled via `aarch64-none-elf-g++ -c` from OdysseyDecomp forward-decls and added to `scripts/check_nso_symbols.py`. Also fixed a latent classifier bug: items use ` Kingdom ` separator (space), not `:` (location form), so `"Cascade Kingdom Power Moon"` was silently routing to `kingdom=None` — fix in `datapackage.py` with new `_ITEM_MOON_KINGDOM_RE`. Bridge `--repl` mode added for dev-test injection without an AP server (commands route through `DataPackage.classify_item` so wire fidelity matches real AP items). M6 phase B (captures) + phase C (kingdom unlock via `unlockWorld` + snapshot enumerate bodies) are the obvious continuations.
- **apworld item-pool simplification** (rides along with M6 phase A.5, 2026-05-16): removed the kingdom-AGNOSTIC `Power Moon` item (count=463) from [apworld/.../items.json](apworld/smo_archipelago/data/items.json). Item pool drops from 1043 → 580. Reason: all moon items should be per-kingdom so the per-kingdom HUD counter (`getGotShineNum` hook → `ap_moons_kingdom[bit]`) ticks correctly; the kingdom-agnostic `Power Moon` only fed the global `ap_moons_unkingdomed` counter and was effectively dead weight in a per-kingdom-aware mod. After the change the item pool is just `X Kingdom Power Moon` (+1) and `X Kingdom Multi-Moon` (+3) per kingdom, plus captures/kingdoms/shop/stickers. Per-kingdom moon-credit totals now match the in-game moon count for that area (e.g. Cascade: 19 PM + 1 MM = 22 credits = 22 collectable in-game moons). Multi-Moon LOCATION (`Cascade: Multi Moon Atop the Falls`) and the in-game Multi-Moon shine handling are untouched — collecting it just sends a LocationCheck for that location like any other moon, AP routes whatever item is there, the per-kingdom counter ticks +1 or +3 depending on what came back.
- **M6 phase A.5**: moon-get cutscene label substitution (Channel A) — **DONE 2026-05-16 (Ryujinx-verified, see [user playtest log notes](#m6-phase-a5-playtest-2026-05-16) below).** When Mario collects a moon, the cutscene's "TxtScenario" pane text is replaced with AP-aware text (`Got Cap Power Moon!` / `Sent Cap Power Moon -> P3`). Bridge pre-warms via `LocationScouts` on `Connected` so it already knows what item each location yields — synthesizes label text the moment a check arrives and ships `MoonLabelMsg` in the same TCP push as the handshake reply, so no AP round-trip in the hot path. Switch's `MoonLabelHook` trampolines 3 cutscene state-machine entry points (`StageSceneStateGetShine::exeDemoGet`, `Main::exeDemoGetStart`, `Grand::exeDemoGetStart`) and calls `al::setPaneStringFormat` post-Orig, so our write wins over SMO's vanilla placeholder. Layout offsets (0x20 / 0x40 / 0x40) + pane name (`TxtScenario`) extracted by disassembling each call site against the real 1.0.0 main.nso (Phase 0 of the plan; `aarch64-none-elf-objdump` + a small Python register-simulator). All 4 new symbols verified in `scripts/check_nso_symbols.py` (20/20 total). Bridge uses a release-store-publish pattern on `ApState::pending_moon_label` (no mutex — the libstdc++ allocator NULL-deref applies to std::mutex too); frame thread tracks `label_last_consumed_seq` so the per-frame cutscene `exe` callback only applies once per moon. Sequence ids stamped by `next_check_seq.fetch_add(1)` in `reportMoonChecked` so the bridge can correlate label↔check via `CheckMsg.seq` ↔ `MoonLabelMsg.seq`. Channel B (Cappy bubble for items arriving outside the cutscene window) is the deferred M6.6 follow-up — see the plan for the scope split rationale. Bridge `--repl` got a `label <text>` command for visual testing without an AP server.
- **M6 phase B**: capture grant via `addHackDictionary` — **DONE 2026-05-16.** AP-issued capture items now write into SMO's hack dictionary so unlocked captures appear in the in-game Capture List. Two new symbols (`addHackDictionary` + `isExistInHackDictionary` for idempotency probe) resolved via `nn::ro::LookupSymbol` at module init, stored as function pointers (same pattern as `CaptureStartHook::getCurrentHackName`). New `CaptureGate::grantCapture(cap_name, hack_name)` is called from `ApState::applyOnFrame` capture arm; idempotent via `isExistInHackDictionary`; falls back to identity (`hack_name = cap_name`) when bridge didn't resolve, which works for the ~36 1:1 names like Frog→Frog. `ApState` gains `game_data_holder_cache` (atomic `void*`); `DrawMainHook` reads `HakoniwaSequence::mGameDataHolder` at offset 0xB8 (a `GameDataHolderAccessor` whose first field is the holder ptr) every frame and stores it. `GameDataHolderWriter` / `GameDataHolderAccessor` are 1-pointer Itanium-ABI-trivial wrappers; we declare local mirror structs and brace-init from the cached pointer when constructing arguments. Bridge: `ItemMsg` gains optional `hack_name`; `CaptureMap` gains a `cap_to_hack` reverse lookup; `ap_client.py::ReceivedItems` stamps the resolved hack_name onto `ItemRef` before `add_received_item` so reconnect-replay carries it through `switch_server.py`. REPL also threads the `CaptureMap` so `capture <name>` ships an identical wire payload to a real AP-issued capture. Latent classifier robustness: `_strip_none` ensures `hack_name: None` is omitted from the wire payload so old mods don't choke. 8 new bridge tests (2 protocol round-trip, 4 reverse-map, 2 REPL). Playtest validated (2026-05-16): REPL `capture <name>` → mod log `[m6-capture] addHackDictionary OK cap='X' hack='Y'` → capture appears unlocked in the Cappy Capture List menu.
- **M6.1 worker-thread allocator hardening** — **DONE 2026-05-16.** After the M6-B playtest, every save load reliably crashed the worker thread in `__memcpy_device` / `nn::os::GetTlsValue` (NULL TLS slot). Each successive iteration peeled off one more libstdc++ allocator caller on the recv-loop; all four are now eliminated:
  1. **Encoder** (`Encoder::beginObject` → `std::vector<bool>::push_back`): replaced with fixed `bool[kMaxDepth=16]` + depth counter.
  2. **Encoder output** (`std::string out_`): replaced with caller-owned `smoap::util::json::LineBuffer` (fixed `char[8 KiB]`). All `encode*` functions now take `LineBuffer&` and return void; ApClient call sites pass stack-local or SnapshotBuilder-member LineBuffers. `value(std::int64_t)` uses `snprintf` into a stack `char[24]` instead of `std::to_string`.
  3. **Inbound buffer + line storage** (`std::string read_buf_`, `popLine(std::string&)`, `handleLine` mutable copy): replaced with `char read_buf_[8 KiB]` + size, and `popLine(char*, size_t&)` / `handleLine(char*, size_t)` operating on caller-mutable buffers. Reader decodes escapes directly into the line buffer.
  4. **DecodedMsg fields** (every `std::string` in `HelloAck` / `ItemRef` / `Item` / `Print` / `ApStateMsg` / `Err` / `Kill` / `DecodedMsg.t`): replaced with `char[N]` (N = 64/128/256/512 depending on field). `readIntoString` → `readIntoField<N>` template. `fromWire` got a `const char*` overload so kind discriminators never construct a std::string. `CheckedReplay::ids` (was `std::vector<ItemRef>`) is now `ItemRef[128]` + `id_count` + `truncated` flag. Because `DecodedMsg` is now ~67 KiB, `handleLine` holds it as a function-local `static` rather than on the worker stack (single instance, worker thread is the only caller). Downstream APIs that took `const std::string&` (`kingdomBitFor`, `captureBitFor`, `grantCapture`, `captureBlocked`) now take `const char*`.
  
  Validated in Ryujinx (2026-05-16): six successive save loads, each triggering re-HELLO → `hello_ack` → `checked_replay: 2 entries` → heartbeats resume; session ended on clean shutdown, no `PrintGuestStackTrace`. Host tests: 27 in `test_json` (encoder/LineBuffer/overflow/round-trip) + all `test_protocol` including new `decode_checked_replay_truncates_past_cap` and `decode_field_overlong_string_truncates`. Outbound `StateChunk::shines` / `StateChunk::captures` are still `std::vector` but populated by stub enumerate functions; convert when M5/M6 enumerate bodies land.
- **M6 phase C** (deferred): kingdom unlocks via `unlockWorld` (the user's "less ideal" fallback should it turn out the AP-credit moon counter doesn't fully gate kingdom progression in every case), plus M4.5 snapshot enumerate bodies (`enumerateOwnedShines` / `enumerateOwnedCaptures`). Symbols already in `scripts/check_nso_symbols.py`. Phase A's REPL-injection flow + the new phase B grant path are the test infrastructure. **NB**: when enumerate bodies land, the StateChunk vector fields will need the same treatment described in M6.1, or the worker-thread allocator NULL-deref will re-emerge on first snapshot send.
- **M7**: capture lock + goal detection
- **M8**: apworld extensions + in-game ImGui + polish (incl. dedicated AP-credit HUD overlay — see "What's definitely NOT done")

## Repository layout

```
C:\Users\maxwe\Documents\smo_archipelago\
  README.md                      Project overview
  CLAUDE.md                      ← this file
  LICENSE                        MIT
  .gitignore                     Note: third_party/ ignored; vendor/ tracked
  .gitmodules                    (after `git submodule add`)
  apworld/                       Forked manual_smo_mp3 → smo_archipelago
    smo_archipelago/             Full package; only `data/game.json` creator field changed
    README.md
  bridge/                        Python bridge — 102 tests pass (+1 live-AP skipped, 2 extraction tests flake in fresh worktrees pending capture_map.json)
    smo_ap_bridge/
      __main__.py
      config.py                  TOML loader, CLI overrides, env var SMOAP_PASSWORD / SMOAP_AP_PATH
      protocol.py                Wire-format dataclasses (Switch ↔ Bridge), iter_lines, MAX_LINE_BYTES
      ap_client.py               CommonContext subclass; three-tier Archipelago path resolution
      switch_server.py           asyncio TCP server, line-JSON framing, replay on HELLO
      datapackage.py             AP id↔name + classifier (Moon/Capture/Kingdom/Shop/Other)
      state.py                   Thread-safe state mirror for tracker + replay
      tracker_web.py             Flask app on :8000, /api/snapshot, /api/test/inject-deathlink (debug)
      scout_cache.py             M6 phase A.5: LocationScouts pre-fetch so Channel A labels beat AP round-trip
      display.py                 M6 phase A.5: Channel A label formatting (UTF-8-safe truncation + kingdom shortening)
      logging_setup.py
    tests/                       102 passing (live-AP + 2 extraction tests auto-skip when prereqs absent)
    pyproject.toml
    requirements.txt
    config.example.toml
  switch-mod/                    exlaunch C++ module
    CMakeLists.txt               Builds subsdk9 from lunakit stock templates; no FW 22+ hacks
    src/
      main.cpp                   exl_main entry — installs hooks, spawns worker
      ap/{ApClient,ApState,ApConfig,ApFrameBridge,ApProtocol}.{cpp,hpp}
      ap/capture_table.h         AUTO-GENERATED (42 cap names)
      hooks/HookSymbols.hpp      8 mangled symbols
      hooks/{MoonGet,CaptureStart,ScenarioFlag,SaveLoad,Ending,MoonLabel}Hook.cpp
      game/{MoonApply,CaptureGate,KingdomUnlock}.{cpp,hpp}
      ui/ApHudOverlay.{cpp,hpp}
      util/{Json,Log}.{cpp,hpp}  Json reader implemented; rest stubs
    romfs/ap_config.json         Switch reads at runtime for bridge IP
    lunakit-vendor/              Vendored LunaKit submodule (toolchain + templates + libs)
  scripts/
    bridge_smoke_test.py         Fake-Switch end-to-end test
    sync_capture_table.py        items.json → capture_table.h (use this; ps1 also exists)
    sync_capture_table.ps1
    extract_shine_map.py         M5.8: NSP → romfs → shine_map.json + capture_map.json (self-bootstrapping)
    .extract-venv/               Auto-created Python 3.12 venv with oead (gitignored)
  docs/
    architecture.md              Three-tier diagram, threading, responsibilities
    wire-protocol.md             14 message types with examples
    build-windows.md             Toolchain install
    extract-moon-data.md         M5.8: how to generate shine_map.json + capture_map.json from your dump
    install-switch.md            SD card layout, troubleshooting
  vendor/                        For submodules (Archipelago goes here)
  third_party/                   Local clones — gitignored
    SMO-manual-AP/               Reference clone of upstream Manual world
```

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

## Subsdk slot

Module ships as **`subsdk9`** at `sd:/atmosphere/contents/0100000000010000/exefs/subsdk9` — the lunakit default. SMO 1.0.0 has no subsdks in its exefs so the slot is free.

## Game dump (1.0.0)

User has a native SMO 1.0.0 NSP installed — no Atmosphere downgrade overlay. Local copies of `SMO_1.0.0.nsp` and the extracted `main.nso` (15.4 MB) live at `C:\Users\maxwe\Downloads\`. **Never commit these — copyrighted.** `.gitignore` covers `docs/main-*.nso` and the Downloads location is outside the repo.

For offline symbol verification: `bridge/.venv/Scripts/python scripts/check_nso_symbols.py C:\Users\maxwe\Downloads\main.nso`. The script decompresses the NSO segments (LZ4 block) and grep's the `.dynstr` table for the 8 mangled hook names. As of 2026-05-15 all 8 resolve.

## libnx extern "C" gotcha

Critical bug we hit twice. `lunakit-vendor/src/lib/nx/kernel/svc.h` and `lib/nx/result.h` declare functions WITHOUT any `extern "C"` wrapper. The wrapper is in the umbrella `lib/nx/nx.h`. From C++ TUs, **always `#include "lib/nx/nx.h"`**, never the inner headers directly. Including them direct gives C++ mangling at call sites (e.g. `_Z20svcOutputDebugStringPKcm`), the assembly stubs have C linkage, link succeeds, runtime gets unresolved-symbol from rtld, PC jumps to 0, process aborts.

## nn::fs SD mount

`sd:/...` paths in nn::fs are NOT accessible by default in our process. SMO doesn't mount the SD via the Nintendo SDK API (its asset path goes through `sead::FileDeviceMgr` to RomFS). To use `nn::fs::OpenFile("sd:/...")` we must call `nn::fs::MountSdCardForDebug("sd")` once. LunaKit does this by hooking `sead::FileDeviceMgr` ctor. We do it inline in our `GameSystemInitHook::Callback` (plus a fallback in `DrawMainHook` first-call). Without this, `nn::fs::CreateFile` aborts via internal `GetFreeSpaceSize` because "sd:" is unmounted.

## How to run the bridge

```pwsh
cd C:\Users\maxwe\Documents\smo_archipelago\bridge
.\.venv\Scripts\python -m pytest                            # 80 tests pass (1 skipped: live-AP)
.\.venv\Scripts\python -m smo_ap_bridge --no-web-tracker    # without web tracker
.\.venv\Scripts\python -m smo_ap_bridge --config config.local.toml --web-tracker  # full
```

Bridge listens on `0.0.0.0:17777` (Switch TCP) and `0.0.0.0:8000` (web tracker). AP-side connection requires `vendor/Archipelago/` submodule with deps installed (see "Loopback dev setup" in README).

## AP loopback (recommended pre-Ryujinx test)

Validates the whole bridge↔AP stack without booting SMO. After fresh clone:

```pwsh
# Build apworld zip
bridge/.venv/Scripts/python scripts/install_apworld.py

# Generate test seed (one-time per apworld change)
bridge/.venv/Scripts/python scripts/ap_generate.py `
    --player_files_path bridge/test_seeds --outputpath bridge/test_seeds/out

# Unzip the .archipelago server file out of the player zip
bridge/.venv/Scripts/python -c "import zipfile, glob; [zipfile.ZipFile(z).extractall('bridge/test_seeds/out') for z in glob.glob('bridge/test_seeds/out/AP_*.zip')]"

# Host server (pane A)
bridge/.venv/Scripts/python scripts/ap_server.py --port 38281 bridge/test_seeds/out/AP_*.archipelago

# Bridge (pane B) — needs bridge/config.local.toml with host=localhost slot=Mario
bridge/.venv/Scripts/python -m smo_ap_bridge --config bridge/config.local.toml

# Drive checks (pane C)
python scripts/bridge_smoke_test.py
# Expect: each `>> check` mirrored by a `<< item` within ~1s

# Or scripted via pytest:
$env:SMOAP_LIVE_AP="1"; bridge/.venv/Scripts/python -m pytest -v bridge/tests/test_ap_loopback.py
```

Quick old-style smoke test (Switch-only, no AP server):
```pwsh
python C:\Users\maxwe\Documents\smo_archipelago\scripts\bridge_smoke_test.py
```

## What's next

**M6 implementation** (next milestone): in-game item application now that the moon-data table is complete and the round-trip is observable end-to-end. Three pieces:

1. `game/MoonApply::grantShine` — idempotent GameDataHolder write that grants a moon by `(stage_name, object_id, shine_uid)`. Must set `ApState::synthetic_grant_this_frame` so our own `MoonGetHook` doesn't re-report the synthetic grant.
2. `game/CaptureGate::captureBlocked` — bitset gate on `PlayerHackKeeper::startHack` keyed by capture name → bit index from `capture_table.h`. Locks captures Mario hasn't received from AP.
3. Snapshot enumerate bodies — `enumerateOwnedShines` / `enumerateOwnedCaptures` walk the same GameDataHolder traversal as `grantShine` and emit raw IDs into the `state_chunk` snapshot stream the bridge consumes for state reconciliation (M4.5 handler is already wired).

All three are GameDataHolder reads/writes. Symbol set is in `switch-mod/src/hooks/HookSymbols.hpp`; expect to add 1-2 more for the GameDataHolder accessor (forward-declared from OdysseyDecomp + mangled via `aarch64-none-elf-g++ -c`).

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
# Bridge tests (Python)
cd C:\Users\maxwe\Documents\smo_archipelago\bridge
python -m pytest -v

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

# Loopback smoke test (with bridge running separately)
python C:\Users\maxwe\Documents\smo_archipelago\scripts\bridge_smoke_test.py
```

**Critical cross-build gotcha**: msys2 cmake (`/c/devkitPro/msys2/usr/bin/cmake`) inside Git Bash CANNOT find DEVKITPRO (it expects `/opt/devkitpro` mount which Git Bash doesn't have). Use the Windows CMake at `C:/Program Files/CMake/bin/cmake.exe` with `DEVKITPRO=C:/devkitPro` env var.

The build also needs `set_source_files_properties(... PROPERTIES COMPILE_FLAGS "-fpermissive")` on lunakit's vendored sources because devkitA64 GCC 15 rejects const-T `std::construct_at` in lunakit's `typed_storage.hpp`. Already wired in our CMakeLists.

## Game data extraction (M5.8)

Done — see `docs/extract-moon-data.md`. One command after `git clone` produces both the moon map and the capture map:

```pwsh
python scripts/extract_shine_map.py --nsp <SMO_1.0.0.nsp>
```

Self-bootstraps a Python 3.12 venv with `oead` (no 3.13 wheel exists), runs `hactool` to extract RomFS (~5 GB cache at `.romfs-cache/`), then:

- **Moons**: walks the 17 `ShineList_<HomeStage>.byml` files in `SystemData/ShineInfo.szs`, joins each `ObjId` against the per-stage MSBT in `LocalizedData/USen/MessageData/StageMessage.szs` under key `ScenarioName_<ObjId>`. 775 entries → `bridge/smo_ap_bridge/data/shine_map.json` (gitignored).
- **Captures**: walks `SystemData/HackObjList.szs` (130 internal `HackName` strings), joins against `SystemMessage.szs/HackList.msbt` where the label *is* the internal name and the value is the English string. 52 deduped entries → `bridge/smo_ap_bridge/data/capture_map.json` (gitignored).

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

## M6 phase A.5 playtest (2026-05-16)

Confirmed end-to-end on real seed (Mario slot 1) in Ryujinx 1.3.3 with the
M6-phase-A.5 mod (`subsdk9`) auto-deployed from the worktree build:

1. **Channel A label substitution works**: collecting an in-game moon →
   `MoonGetHook` → bridge resolves via shine_map → AP `LocationCheck` →
   AP routes scouted item → bridge sends `MoonLabelMsg` in the same TCP
   push → cutscene's "TxtScenario" pane shows AP-aware text. Verified on
   regular Cascade moons (Our First Power Moon, Behind the Waterfall)
   *and* on the Multi-Moon (Multi Moon Atop the Falls).
2. **Multi-Moon uses `Grand::exeDemoGetStart`** — the hook I deliberately
   chose. `Grand::exeDemoGetFirst` (deliberately not hooked) does not
   need to be hooked. Original plan's open question #1 (in
   [i-wrote-a-plan-fluffy-otter.md](../../.claude/plans/i-wrote-a-plan-fluffy-otter.md))
   is resolved.
3. **Bridge bootstrap gotcha discovered**: a fresh worktree does NOT have
   `bridge/smo_ap_bridge/data/{shine_map,capture_map}.json` (gitignored;
   per-machine). The bridge starts up cleanly without them but every
   moon collect logs `no shine_map entry for stage='X' object='Y'` and
   silently drops the check — no LocationCheck, no MoonLabelMsg,
   cutscene stays vanilla. The fix is to either copy the files from the
   main repo or run `scripts/extract_shine_map.py`. **Future agents:
   copy these two files into a fresh worktree as part of bridge setup**
   (the `bridge/smo_ap_bridge/data/` directory itself doesn't exist
   either, so `mkdir -p` first).

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
- **`AP-server KeyError on scout for missing locations`** — fix at
  [ap_client.py](bridge/smo_ap_bridge/ap_client.py): the warmup now
  scopes to `ctx.missing_locations | ctx.checked_locations` instead of
  the full datapackage. Otherwise a single not-in-this-slot location_id
  in the scout request kills the websocket connection → bridge reconnect
  loop. Burned ~30 minutes finding this one during playtest setup.
- **`apworld/.../data/{items,locations,regions}.json` invariant**: the
  Multi-Moon rework removed the kingdom-agnostic `Power Moon` item but
  it was referenced in 19 `|Power Moon:N| or ...` branches across
  `regions.json` + `locations.json`. The DataValidation pass at seed
  gen catches this loudly. **Future agents removing or renaming any
  item must grep both files for the bare name and update all
  `requires` strings.** Today this is a manual discipline; a CI lint
  would catch it.

## What's definitely NOT done

- On-screen status overlay — deferred to M8 per user Q&A; M3 ships heartbeat-to-lm-log instead (web tracker is the canonical source of truth)
- HELLO `cap_table_hash` field is empty — populated in M4 once we hash the generated `capture_table.h`
- **AP-credit HUD overlay (M8)**: M6 phase A hooks `getCurrentShineNum`/`getGotShineNum` to return AP-credit-only counts (not orig+credit). The natural HUD shows our AP count — visually weird: a locally collected moon does NOT bump the counter even though the shine appears in the shine list. A dedicated ImGui-style AP overlay (à la lunakit devgui) belongs in M8 to surface AP credit info in a clearer, separate UI element. Hooks lying about the natural counter is a stopgap.
- **`getGotShineNum` doesn't fire in normal gameplay**: M6 phase A playtest showed the per-kingdom counter hook never fires when Mario plays in Cascade. SMO's natural per-kingdom counter reads from a different code path. The hook is harmless (returns AP credit when called); if a future code path does call it the credit lands correctly. Kingdom-progression gating via moon counts is therefore an open question — phase B / M6.x may need to land `unlockWorld` for explicit AP-gated kingdom unlocks rather than relying on moon-count substitution.

## M6 phase-A playtest loop

Bridge has a `--repl` mode for direct item injection (no AP server required):

```pwsh
bridge/.venv/Scripts/python -m smo_ap_bridge --config bridge/config.local.toml --repl
# Then at the prompt:
#   smo-ap-bridge> grant Cascade Kingdom Power Moon
#   smo-ap-bridge> grant Power Moon
#   smo-ap-bridge> grant Cascade Kingdom Multi-Moon
#   smo-ap-bridge> capture Goomba
#   smo-ap-bridge> kingdom Sand
#   smo-ap-bridge> label Sent Cap Power Moon -> P3       (M6 phase A.5 — visual test of Channel A)
#   smo-ap-bridge> status
#   smo-ap-bridge> help
```

Items route through `DataPackage.classify_item` so wire fidelity matches real AP-issued items. `from=repl` on the mod side distinguishes them from AP grants in log lines.

`label <text>` directly writes a `MoonLabelMsg` to the Switch's `pending_moon_label` slot — useful for visual testing the cutscene-label hook standalone (collect any moon in Ryujinx within ~4s of running the command and the text appears in the moon-get cutscene). Real bridge↔AP Channel A use needs a live AP server so the `LocationScouts` warmup populates the `scout_cache` from which `_dispatch_check` synthesizes labels on-the-fly.
