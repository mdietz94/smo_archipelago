# Milestone history

Deep per-milestone narratives. CLAUDE.md keeps the compact status table; this file
holds the long form. Most agents won't need any of this unless researching a specific
past decision or pattern. Anchors below match the links in CLAUDE.md's status table.

`C:\Users\maxwe\.claude\plans\after-much-work-i-tender-thompson.md` is the authoritative plan
(FW 21.2 + SMO 1.0.0 simplification). The prior plan `there-is-a-super-peaceful-iverson.md`
predates the downgrade and is archived for reference only.

## M0

Toolchain + symbol map (Track 3 + Track 4) — **DONE**.

## M1

Bridge skeleton — **CODE COMPLETE** (19 tests pass, loopback smoke test green, web tracker JSON endpoint verified).

## M2

Apworld parity fork — **CODE COMPLETE** (vendored `data/`, `creator: archipelago`).

## M3

Switch module skeleton — **RUNTIME VALIDATED** (2026-05-15, Ryujinx). Subsdk9 + main.npdm produced via lunakit stock template; all 8 hooks install via soft-install probe; `nn::socket` worker thread connects, sends HELLO, bridge logs `switch HELLO: mod=0.1.0 smo=1.0.0`. Inbound-item handler / replay / exponential backoff are code-complete but not yet exercised. Real-Switch deploy gated on user choice; Ryujinx loop is canonical for now.

## M4

Read-only state mirroring — **DONE.** All 6 game-event hooks (MoonGet, CaptureStart, ScenarioFlag, SaveLoad, Ending, Death) emit raw SMO identifiers to the bridge. Bridge resolves via `shine_map.json` / `capture_map.json`. DeathLink outbound wired (inbound apply landed in M4.6). `Check` is now `char[64]` buffers + `FlatHashSet<4096>` for `locations_checked` (allocator NULL-deref workaround in our subsdk9 link). Validated in Ryujinx 2026-05-15.

## M4.5

State reconciliation across disconnects — **CODE COMPLETE.** Bridge accepts new `state_begin` / `state_chunk` / `state_end` snapshot from Switch on every (re)connect (transitively on save load via `requestRehello`); accumulates raw IDs by stage and dispatches each entry through the same `check` path live moon-get hooks use. `BridgeState.add_checked_location` dedupes by full ItemRef identity so replays are no-ops. Switch fixes outbound check drop bug in `pumpOnce` (peek-then-pop). 11 new bridge tests; switch-mod enumerate functions stubbed pending M5/M6 GameDataHolder traversal.

## M4.6

Inbound DeathLink (peeled out of original M6) — **DONE 2026-05-15.** Switch acts on inbound `kill` messages by invoking `DeathHook::Orig(cached_PlayerHitPointData*)` directly — the trampoline's Orig bypasses our own Callback so the synthetic death doesn't echo back out as a fresh DeathLink. `synthetic_death_this_frame` kept as defense-in-depth for any future hook downstream of `PlayerHitPointData::kill`. Single 15s debounce window (`kInboundKillDebounceMs`) covers both "Mario in death animation" and "two kills too close together" via one shared `last_observed_death_ms` timestamp updated on every observed death (organic or synthetic). Inbound queue collapsed to a single atomic bool (`inbound_kill_pending`) so closely-spaced bounces auto-debounce at the producer. DeathLink **toggle moved to bridge config**: bridge's `cfg.deathlink.enabled` is communicated to the Switch in `HelloAckMsg.deathlink_enabled`, parsed into `ApState::deathlink_enabled`, gates the inbound apply path. Outbound death reporting is NOT gated on this Switch-side flag — bridge already gates outbound, double-gating would break old-bridge/new-Switch combos. Chicken-and-egg: first inbound DeathLink before Mario has died once organically is dropped with a log line (`PlayerHitPointData::kill` is the only cache site today; closing this hole needs an earlier "any damage" hook). Debug-only `/inject_deathlink` on the SMOClient command bar writes `KillMsg` straight to the Switch socket (bypasses AP), so the apply path can be exercised without a second slot.

**Pre-existing recv-loop bug surfaced + fixed**: `ApClient::threadMain` processed at most one inbound line per Select-wake AND only entered the read branch when Select reported socket-readable. When the bridge sends N messages in a single TCP push (the very common handshake `hello_ack + checked_replay + ap_state` triple, or items + kill back-to-back), messages 2..N sat in `read_buf_` until the *next* socket event — sometimes minutes. Validated live: the M4.6 kill stayed buffered 90+ seconds behind a stale `checked_replay` and Mario never died. Fix splits `readOneLine` into `recvIntoBuf` + `popLine`; the loop drains `read_buf_` to completion every iteration, including Select-timeout iterations. This bug had been masking inbound-message issues in M5.7 as well — anyone testing M6 item delivery would have hit it.

## M5

Web tracker — **CODE COMPLETE**, then **SUPERSEDED** by the in-apworld Kivy SMOClient in the Phase 1-7 reshape. The standalone tracker process is gone; the Tracker/Connections tabs live inside SMOClient now. `/inject_deathlink` on the SMOClient command bar covers the inbound-DeathLink debug path that used to need a separate HTTP call.

## M5.5

AP server live integration — **DONE 2026-05-15.** Forked apworld zipped to `vendor/Archipelago/custom_worlds/smo.apworld` (was `smo_archipelago.apworld` at the time of M5.5; renamed 2026-05-16) via `scripts/install_apworld.py`. Seed generation via `scripts/ap_generate.py` (thin wrapper that pre-sets `ModuleUpdate.update_ran = True` to suppress AP's auto-pip on world-specific deps). MultiServer wrapper at `scripts/ap_server.py`. Bridge ↔ local AP loopback validated end-to-end: `>> check Cap: Frog-Jumping Above the Fog` → bridge translates → `LocationChecks` to AP → AP sends `ReceivedItems` → bridge forwards `ItemMsg` to fake-Switch (all under 1s per round-trip). Bridge fix in `ap_client.py::_populate_datapackage_from_ctx` hydrates `self._dp` from CommonContext's `location_names`/`item_names` on `Connected` (CommonContext satisfies its own lookup from Archipelago's shipped `network_data_package.json` and never relays a `DataPackage` packet that our `on_package` could catch). Regression test `bridge/tests/test_ap_loopback.py` skips unless `SMOAP_LIVE_AP=1`; 43 existing tests still green. Test seed at `bridge/test_seeds/smo_loopback.yaml` (gitignored output at `bridge/test_seeds/out/`).

## M5.7

Ryujinx E2E — **DONE 2026-05-15.** First real moon traversed the whole stack: Mario collects "Our First Power Moon" in Ryujinx → `MoonGetHook` fires with `stage=WaterfallWorldHomeStage, obj=obj214` → `[pump] Send 102 bytes` → bridge resolves via `shine_map.json` → `LocationCheck id=14481151511` to AP → AP records check, places "Snow Kingdom Power Moon" item → `ReceivedItems` echoed → bridge forwards `ItemMsg` to mod (mod's inbound ring receives it; M6 application still stubbed). Three real bugs surfaced + fixed: (a) mod's `BRIDGE_HOST` was baked at the stale M3-era LAN IP (rebuilt with `-DBRIDGE_HOST=127.0.0.1` for Ryujinx-on-same-host); (b) `shine_map.json` seed entries used aspirational `MoonOurFirst`-style symbolic names but `ShineInfo::objectId` actually emits the placement-file ref `obj214` — confirmed via MoonFlow's public `ShineInfo` schema, replaced with 1 verified entry; (c) `ap_client.report_check` silently returned on `locations_checked` dedup, which combined with persistent `AP_*.apsave` from the M5.5 smoke test masked working pipeline as "moon arrived but nothing happened" — added explicit forwarding-vs-skip log lines. Diagnostic logging shipped permanently: `MoonGetHook` probe (`obj`/`scen`/`uid`), `ApClient::pumpOnce` `[pump]` traces, `ap_client.report_check` forwarding-distinction lines. These were load-bearing observability — every issue would have been silent without them.

## M5.8

Full moon + capture data extraction — **DONE 2026-05-15.** Single command `python scripts/extract_shine_map.py --nsp <SMO_1.0.0.nsp>` produces a complete 775-entry `shine_map.json` AND 52-entry `capture_map.json`. Self-bootstraps a Python 3.12 venv with `oead` (no 3.13 wheel available); auto-extracts romfs via `hactool` (PFS0 → program NCA → RomFS, ~5 GB cached at `.romfs-cache/`).

- **Moons**: walks `SystemData/ShineInfo.szs` (17 BYML kingdom shine lists) and joins against per-stage MSBT in `LocalizedData/USen/MessageData/StageMessage.szs` under `ScenarioName_<ObjId>` keys. The MSBT must be the per-shine StageName MSBT (sub-stages like `PushBlockExStage` own their own messages), and kingdom assignment must come from the HomeStage BYML container (sub-stage names don't match `CapWorld*` etc.).
- **Captures**: walks `SystemData/HackObjList.szs` (130 internal `HackName` strings) and joins against `LocalizedData/USen/MessageData/SystemMessage.szs/HackList.msbt` where the label *is* the internal name and the value is the English form. A small `CAPTURE_NAME_ALIASES` table handles 6 cases where the apworld deliberately diverged from Nintendo (collapsed multi-piece variants like `Picture Match Part (Mario)` → `Picture Match Part`, prefix renames like `Cheep Cheep (Snow Kingdom)` → `Snow Cheep Cheep`, casing like `Bowser statue` → `Bowser Statue`). Investigation showed no public repo publishes the Japanese-internal → English mapping (only the internal names appear in lunakit / OdysseyDecomp as code identifiers), so extraction is the only safe path.
- **MSBT parser**: shipped as a ~150-line in-tree reader because `pymsyt` only knows BotW's control codes and chokes on SMO's control code 6.
- **Cross-validation**: 100% (436/436 moons + 43/43 captures) of apworld entries resolve. Emitted files cover the full 775 + 52 SMO entries — extras (339 out-of-apworld-scope moons, 7 out-of-scope captures) emitted so future apworld expansion picks them up automatically. (T-Rex was promoted into the apworld in the logic-audit pass; pre-promotion it was 42/42 + 8 out-of-scope.)
- **IP discipline**: all 4 generated files (`shine_map.json`, `shine_map_review.json`, `capture_map.json`, `capture_map_review.json`) are gitignored. Nine tests in `bridge/tests/test_shine_map_extraction.py` validate schema/count/dedup/anchors for both maps (auto-skip when files absent). Also fixed 10 apworld typos in `apworld/.../locations.json` (e.g. `"Cafe?"` → `"Café?"`, `"By the Falls"` → `"by the Falls"`). Full workflow in `docs/extract-moon-data.md`.

## M6 phase A

AP-credit moon counter HUD substitution — **DONE 2026-05-15.** Two new trampoline hooks (`ShineNumGetHook` on `GameDataFunction::getCurrentShineNum`, `ShineNumByWorldGetHook` on `getGotShineNum`) drop `orig` and return AP-credit-only counts. `ApState` gains `ap_moons_unkingdomed` (truly-generic "Power Moon" credits) + `ap_moons_kingdom[17]` (kingdom-tagged credits, indexed by `kingdomBitFor`). `applyOnFrame` moon arm rewritten to bump credit counters with rich logging (`[m6-moon]` lines); Multi-Moon items grant +3, single-moon +1, kingdom-less generic credits go to `ap_moons_unkingdomed` and only show in the global counter. setGotShine runs untouched so the shine list correctly reflects local pickups — only the visible counter is AP-gated. Validated in Ryujinx (2026-05-15): local moon collection → HUD stays 0, Odyssey ship rejects the moon ("doesn't count"); REPL `grant Cascade Kingdom Power Moon` → HUD ticks to 1, Mario can hand it to the Odyssey; `grant Snow Kingdom Power Moon` rejected by the Cascade Odyssey (kingdom-specific routing works); pre-existing save moons disappear from the visible counter (orig is fully suppressed). `getGotShineNum` hook resolves and fires when explicitly invoked but **never fires during normal Cascade play** — SMO's natural per-kingdom counter reads shine flags directly; the global `getCurrentShineNum` does most of the work for HUD + Odyssey gating. Two new symbols mangled via `aarch64-none-elf-g++ -c` from OdysseyDecomp forward-decls and added to `scripts/check_nso_symbols.py`. Also fixed a latent classifier bug: items use ` Kingdom ` separator (space), not `:` (location form), so `"Cascade Kingdom Power Moon"` was silently routing to `kingdom=None` — fix in `datapackage.py` with new `_ITEM_MOON_KINGDOM_RE`. Bridge `--repl` mode added for dev-test injection without an AP server (commands route through `DataPackage.classify_item` so wire fidelity matches real AP items). (REPL `/grant` / `/capture` / `/kingdom` removed 2026-05-17 — use `/send` on the AP server console; see the [M6 phase-A playtest loop](#m6-phase-a-playtest-loop) section.) M6 phase B (captures) + phase C (snapshot enumerate bodies) are the obvious continuations.

## apworld item-pool simplification

Rides along with M6 phase A.5 (2026-05-16): removed the kingdom-AGNOSTIC `Power Moon` item (count=463) from [apworld/.../items.json](../apworld/smo_archipelago/data/items.json). Item pool drops from 1043 → 580. Reason: all moon items should be per-kingdom so the per-kingdom HUD counter (`getGotShineNum` hook → `ap_moons_kingdom[bit]`) ticks correctly; the kingdom-agnostic `Power Moon` only fed the global `ap_moons_unkingdomed` counter and was effectively dead weight in a per-kingdom-aware mod. After the change the item pool is just `X Kingdom Power Moon` (+1) and `X Kingdom Multi-Moon` (+3) per kingdom, plus captures. Per-kingdom moon-credit totals now match the in-game moon count for that area (e.g. Cascade: 19 PM + 1 MM = 22 credits = 22 collectable in-game moons). Multi-Moon LOCATION (`Cascade: Multi Moon Atop the Falls`) and the in-game Multi-Moon shine handling are untouched — collecting it just sends a LocationCheck for that location like any other moon, AP routes whatever item is there, the per-kingdom counter ticks +1 or +3 depending on what came back.

## apworld pool-only-moons-and-captures cleanup

2026-05-16: purged everything from the item pool that the mod can't actually grant. Shop items (82 hats/outfits/souvenirs/stickers, both Coin and Regional categories) deleted from items.json + the matching 82 `Shop:` locations from locations.json — the Switch mod has no shop-purchase hook so those checks were unreachable anyway. The 9 outfit-rule helpers in [hooks/Rules.py](../apworld/smo_archipelago/hooks/Rules.py) (`Sombrero` / `Explorer` / `Builder` / `Snowsuit` / `Resort` / `Chef` / `Samurai` / `Boxers` / `Swimwear`) and their `{Sombrero()}`-style references on 13 moon `requires` strings went with them; standalone refs became `[]`, ANDed refs (`{IntoTheLake()} and {Swimwear()}`) shed the outfit clause. The 3 trap items (`Return Trap` / `Upside Down Trap` / `Cappyless Trap`, all count=0) removed; the [Options.py:20](../apworld/smo_archipelago/Options.py:20) auto-register conditional on any-`trap: true` makes `filler_traps` disappear from options automatically. `filler_item_name` in the inlined `game_table` ([Data.py](../apworld/smo_archipelago/Data.py)) flipped from "The Will to Do Trick Jumps" to "Coin" — [Items.py:21](../apworld/smo_archipelago/Items.py:21) auto-appends an item entry with this name, so no items.json change needed. **Final pool: 68 items.json entries (26 per-kingdom moon types + 42 captures) + 1 Coin auto-appended = 69 declared items; gen places 479 items at 482 locations (3 short → 3 Coin fillers added automatically by `adjust_filler_items`).** Also stripped the now-dead `ItemKind::Shop` enum value + dead `ItemRef.slot` / `Check.slot` / `Item.slot` int fields from both the C++ wire protocol ([ApProtocol.hpp](../switch-mod/src/ap/ApProtocol.hpp)) and the Python bridge ([protocol.py](../apworld/smo_archipelago/client/protocol.py)) — `slot` was originally introduced for shop-slot positions and was never set non-default in real traffic. `fromWire("shop")` still decodes to `ItemKind::Other` as forward-compat for old-Switch builds. Validation: 120 Python tests + 3 C++ host suites green; seed generation succeeds.

## M6 phase A.5

Moon-get cutscene label substitution (Channel A) — **DONE 2026-05-16 (Ryujinx-verified, see [playtest log](#m6-phase-a5-playtest-2026-05-16) below).** When Mario collects a moon, the cutscene's "TxtScenario" pane text is replaced with AP-aware text (`Got Cap Power Moon!` / `Sent Cap Power Moon -> P3`). Bridge pre-warms via `LocationScouts` on `Connected` so it already knows what item each location yields — synthesizes label text the moment a check arrives and ships `MoonLabelMsg` in the same TCP push as the handshake reply, so no AP round-trip in the hot path. Switch's `MoonLabelHook` trampolines 3 cutscene state-machine entry points (`StageSceneStateGetShine::exeDemoGet`, `Main::exeDemoGetStart`, `Grand::exeDemoGetStart`) and calls `al::setPaneStringFormat` post-Orig, so our write wins over SMO's vanilla placeholder. Layout offsets (0x20 / 0x40 / 0x40) + pane name (`TxtScenario`) extracted by disassembling each call site against the real 1.0.0 main.nso (Phase 0 of the plan; `aarch64-none-elf-objdump` + a small Python register-simulator). All 4 new symbols verified in `scripts/check_nso_symbols.py` (20/20 total). Bridge uses a release-store-publish pattern on `ApState::pending_moon_label` (no mutex — the libstdc++ allocator NULL-deref applies to std::mutex too); frame thread tracks `label_last_consumed_seq` so the per-frame cutscene `exe` callback only applies once per moon. Sequence ids stamped by `next_check_seq.fetch_add(1)` in `reportMoonChecked` so the bridge can correlate label↔check via `CheckMsg.seq` ↔ `MoonLabelMsg.seq`. Channel B (Cappy bubble for items arriving outside the cutscene window) is the deferred M6.6 follow-up — see the plan for the scope split rationale. Bridge `--repl` got a `label <text>` command for visual testing without an AP server.

### M6 phase A.5 playtest (2026-05-16)

Confirmed end-to-end on real seed (Mario slot 1) in Ryujinx 1.3.3 with the M6-phase-A.5 mod (`subsdk9`) auto-deployed from the worktree build:

1. **Channel A label substitution works**: collecting an in-game moon → `MoonGetHook` → bridge resolves via shine_map → AP `LocationCheck` → AP routes scouted item → bridge sends `MoonLabelMsg` in the same TCP push → cutscene's "TxtScenario" pane shows AP-aware text. Verified on regular Cascade moons (Our First Power Moon, Behind the Waterfall) *and* on the Multi-Moon (Multi Moon Atop the Falls).
2. **Multi-Moon uses `Grand::exeDemoGetStart`** — the hook I deliberately chose. `Grand::exeDemoGetFirst` (deliberately not hooked) does not need to be hooked. Original plan's open question #1 (in [i-wrote-a-plan-fluffy-otter.md](../../.claude/plans/i-wrote-a-plan-fluffy-otter.md)) is resolved.
3. **Client bootstrap gotcha discovered**: a fresh worktree does NOT have `apworld/smo_archipelago/client/data/{shine_map,capture_map}.json` (gitignored; per-machine). The client starts up cleanly without them but every moon collect logs `no shine_map entry for stage='X' object='Y'` and silently drops the check — no LocationCheck, no MoonLabelMsg, cutscene stays vanilla. The fix is to either copy the files from the main repo or run `scripts/extract_shine_map.py`. **Future agents: copy these two files into a fresh worktree as part of client setup** (the `apworld/smo_archipelago/client/data/` directory exists; if missing, `mkdir -p` first).

## M6 phase B

Capture grant via `addHackDictionary` — **DONE 2026-05-16.** AP-issued capture items now write into SMO's hack dictionary so unlocked captures appear in the in-game Capture List. Two new symbols (`addHackDictionary` + `isExistInHackDictionary` for idempotency probe) resolved via `nn::ro::LookupSymbol` at module init, stored as function pointers (same pattern as `CaptureStartHook::getCurrentHackName`). New `CaptureGate::grantCapture(cap_name, hack_name)` is called from `ApState::applyOnFrame` capture arm; idempotent via `isExistInHackDictionary`; falls back to identity (`hack_name = cap_name`) when bridge didn't resolve, which works for the ~36 1:1 names like Frog→Frog. `ApState` gains `game_data_holder_cache` (atomic `void*`); `DrawMainHook` reads `HakoniwaSequence::mGameDataHolder` at offset 0xB8 (a `GameDataHolderAccessor` whose first field is the holder ptr) every frame and stores it. `GameDataHolderWriter` / `GameDataHolderAccessor` are 1-pointer Itanium-ABI-trivial wrappers; we declare local mirror structs and brace-init from the cached pointer when constructing arguments. Bridge: `ItemMsg` gains optional `hack_name`; `CaptureMap` gains a `cap_to_hack` reverse lookup; `ap_client.py::ReceivedItems` stamps the resolved hack_name onto `ItemRef` before `add_received_item` so reconnect-replay carries it through `switch_server.py`. (REPL `capture <name>` was the wire-fidelity test fixture at the time; removed 2026-05-17 once the AP-received path was the canonical test — see [M6 phase-A playtest loop](#m6-phase-a-playtest-loop).) Latent classifier robustness: `_strip_none` ensures `hack_name: None` is omitted from the wire payload so old mods don't choke. 8 new bridge tests (2 protocol round-trip, 4 reverse-map, 2 REPL). Playtest validated (2026-05-16): REPL `capture <name>` → mod log `[m6-capture] addHackDictionary OK cap='X' hack='Y'` → capture appears unlocked in the Cappy Capture List menu.

## M6.1

Worker-thread allocator hardening — **DONE 2026-05-16.** After the M6-B playtest, every save load reliably crashed the worker thread in `__memcpy_device` / `nn::os::GetTlsValue` (NULL TLS slot). Each successive iteration peeled off one more libstdc++ allocator caller on the recv-loop; all four are now eliminated:

1. **Encoder** (`Encoder::beginObject` → `std::vector<bool>::push_back`): replaced with fixed `bool[kMaxDepth=16]` + depth counter.
2. **Encoder output** (`std::string out_`): replaced with caller-owned `smoap::util::json::LineBuffer` (fixed `char[8 KiB]`). All `encode*` functions now take `LineBuffer&` and return void; ApClient call sites pass stack-local or SnapshotBuilder-member LineBuffers. `value(std::int64_t)` uses `snprintf` into a stack `char[24]` instead of `std::to_string`.
3. **Inbound buffer + line storage** (`std::string read_buf_`, `popLine(std::string&)`, `handleLine` mutable copy): replaced with `char read_buf_[8 KiB]` + size, and `popLine(char*, size_t&)` / `handleLine(char*, size_t)` operating on caller-mutable buffers. Reader decodes escapes directly into the line buffer.
4. **DecodedMsg fields** (every `std::string` in `HelloAck` / `ItemRef` / `Item` / `Print` / `ApStateMsg` / `Err` / `Kill` / `DecodedMsg.t`): replaced with `char[N]` (N = 64/128/256/512 depending on field). `readIntoString` → `readIntoField<N>` template. `fromWire` got a `const char*` overload so kind discriminators never construct a std::string. `CheckedReplay::ids` (was `std::vector<ItemRef>`) is now `ItemRef[128]` + `id_count` + `truncated` flag. Because `DecodedMsg` is now ~67 KiB, `handleLine` holds it as a function-local `static` rather than on the worker stack (single instance, worker thread is the only caller). Downstream APIs that took `const std::string&` (`kingdomBitFor`, `captureBitFor`, `grantCapture`, `captureBlocked`) now take `const char*`.

Validated in Ryujinx (2026-05-16): six successive save loads, each triggering re-HELLO → `hello_ack` → `checked_replay: 2 entries` → heartbeats resume; session ended on clean shutdown, no `PrintGuestStackTrace`. Host tests: 27 in `test_json` (encoder/LineBuffer/overflow/round-trip) + all `test_protocol` including new `decode_checked_replay_truncates_past_cap` and `decode_field_overlong_string_truncates`. Outbound `StateChunk::shines` / `StateChunk::captures` are still `std::vector` but populated by stub enumerate functions; convert when M5/M6 enumerate bodies land.

**Pattern guidance for future Switch-side code**: any code path touched by the worker thread MUST use fixed-buffer patterns (LineBuffer, char[N] fields, FlatHashSet) — not std::string/std::vector/std::set/std::to_string. The libstdc++ allocator NULL-derefs unpredictably in our subsdk9 link; the worker thread is NOT a safe haven (proven 2026-05-16). See memory: `project_libstdcpp_allocator_broken_in_subsdk9.md`.

## M6 phase C (deferred)

M4.5 snapshot enumerate bodies (`enumerateOwnedShines` / `enumerateOwnedCaptures`). Stubs are in [MoonApply.cpp](../switch-mod/src/game/MoonApply.cpp) / [CaptureGate.cpp](../switch-mod/src/game/CaptureGate.cpp); the GameDataHolder traversal bodies are the missing pieces. Symbols (`isGotShine`, `getGameDataFile`) already in `scripts/check_nso_symbols.py`. **NB**: when enumerate bodies land, the StateChunk vector fields will need the same treatment described in [M6.1](#m61), or the worker-thread allocator NULL-deref will re-emerge on first snapshot send.

The originally-scoped *kingdom unlock via `unlockWorld`* fallback was dropped on 2026-05-18 — M7 Path A's kingdom-order gate landed cleanly without needing an explicit AP-gated unlock path, and the apworld never added kingdom-unlock items. The `ItemKind::Kingdom` wire kind, `received_kingdom_mask`, `kingdoms_unlocked` PC-state field, and the `unlockWorld`/`isUnlockedWorld` symbol entries have all been removed. `KingdomUnlock.{hpp,cpp}` kept (the filename is now legacy) — it still owns the kingdom name ↔ bit ↔ worldId table that M6 phase D and M7 Path A both depend on.

## M6 phase D

Moon-deposit debit (HUD ticks DOWN on Odyssey hand-toss) — **DONE 2026-05-17 (Ryujinx-verified).** Was a real bug: M6-A's HUD was AP-credit-only but had no debit path, so Mario could re-spend the same AP-credit moons forever at the Odyssey ship. Fix is a hook on `GameDataFunction::addPayShine(GameDataHolderWriter, s32)` — the public wrapper for the per-toss spend (the `GameDataFile::addPayShine(s32)` member is inlined into all callers in 1.0.0 main.nso and not present in dynsym; the `GameDataFunction::` wrapper IS, same hookable-wrapper-over-inlined-member pattern as `addHackDictionary`). Hook also covers `GameDataFunction::addPayShineCurrentAll(GameDataHolderWriter)` (rare "pay everything in current kingdom" path). Both clamp at 0 to enforce per-kingdom isolation: a Cap-Odyssey toss can NEVER decrement Wooded credit, even when Cap balance is 0. Also new: `ShineNumGetHook` now returns `ap_moons_kingdom[currentKingdom_bit]` (per-kingdom, not the sum-across-all from M6-A) so the HUD shows exactly what Mario can spend HERE, matching vanilla post-clear `getCurrentShineNum` semantics. Current kingdom resolved via `GameDataFunction::getCurrentWorldIdNoDevelop` (third new symbol; the `NoDevelop` variant clamps the develop-state `-1` to 0). World-id ordering verified against OdysseyDecomp — **NB**: SMO's id 8/9 are Sea/Snow but our bits 8/9 are Snow/Seaside, so `kingdomBitForWorldId(int)` in [KingdomUnlock.cpp](../switch-mod/src/game/KingdomUnlock.cpp) encodes that one swap. (The Boss/Sky pair at ids 11/12 IS identity-mapped against our bits 11/12 Ruined/Bowser — see the file header comment for the OdysseyDecomp + ShineList-content evidence. An earlier note in this doc claimed a Boss/Sky swap was needed; that turned out to be wrong and produced a Bowser↔Ruined HUD/outstanding swap until corrected.)

Wire-protocol additions: `DepositMsg` (Switch→Bridge, with monotonic per-session `seq`), `DepositAckMsg` (Bridge→Switch, idempotent re-ack of repeated seqs), `OutstandingMsg` (Bridge→Switch, authoritative per-kingdom balance from the AP data store). Per-kingdom outstanding persisted in AP data store under key `smo_outstanding_<team>_<slot>` via `set_notify` + `Set` with `replace` op (single bridge, AP server linearizes back-to-back `Set`s in a single coroutine so no read-modify-write race). Switch keeps unacked deposits in a 32-entry ring; replays on reconnect; `ApClient::threadMain` clears it on save-load-driven re-HELLO (NOT on ordinary disconnects, so a network blip doesn't lose pending deposits). `bridge_connected` atomic gates both hooks: offline → `ShineNumGetHook` returns 0 (Odyssey UI refuses fuel) AND `AddPayShineHook` skips Orig (vanilla PayShine can't drift from AP credit).

**Critical wire-protocol invariant in `switch_server.py::_on_hello`**: when sending the post-HELLO item replay, **skip Moon items** — `OutstandingMsg` already carries the authoritative per-kingdom balance, and re-sending Moon items would double-count via the mod's `applyOnFrame` fetch_add. Captures still replay through the existing loop.

Tests: 12 new in `test_outstanding.py` + 5 new in `test_protocol.py` + 5 new in `test_switch_server.py` + 7 new in `test_protocol.cpp`. Playtest 2026-05-17: HUD per-kingdom decrement on hand-toss confirmed end-to-end. Latent bug found + fixed during playtest: `install_apworld.py` writes to its OWN checkout's `vendor/Archipelago/custom_worlds/`, not the main checkout's — when working in a worktree you must copy the worktree's `smo.apworld` to the main checkout's `vendor/Archipelago/custom_worlds/` if the user launches SMOClient from the main checkout's Launcher (which they typically do). Symptom is `unknown message type from Switch: deposit` in the bridge log even though `AddPayShineHook` fired on the Switch. See the **smo-build** skill for the install_apworld worktree workaround.

## M7 Path A — kingdom-order gate

2026-05-17: **DONE.** Ryujinx-verified end-to-end on a fresh save with the post-Sand fork: picked the "bottom slot" (which displays as Lake post-substitution, where Wooded would have been), arrived in Lake with full normal visuals. Enforces linear progression at SMO's two world-map bifurcations — post-Sand the player must clear Lake (≥8 AP-credit Lake moons) before Wooded, post-Metro must clear Snow (≥10 AP-credit Snow moons) before Seaside. Pairs with the apworld linear-chain `regions.json` already on main (`24a86dc apworld: linear kingdom chain + drop master Peace toggle`) so AP doesn't pre-grant Lake/Snow moons that would trivially satisfy the gate.

**Three-layer substitution architecture** (8 hooks, all in [WorldMapSelectHook.cpp](../switch-mod/src/hooks/WorldMapSelectHook.cpp)):

1. **Layer 1 — regular world-map UI** (4 hooks on `GameDataFunction::getUnlockWorldIdForWorldMap` by ptr-type overload). Catches Odyssey world-map opens AFTER the fork has been resolved. Verified firing as LiveActor + Scene overloads.
2. **Layer 2 — post-Multi-Moon FORK cinematic** (2 hooks on `GameDataFunction::calcNextLockedWorldIdForWorldMap` by ptr-type overload). Catches the one-time "newly unlocked" presentation that plays right after collecting a kingdom's Multi-Moon. Verified firing as the Scene overload on slot 0 in the fresh-save fork playtest — this is what made the fork case work cleanly.
3. **Layer 3 — stage-commit BACKSTOP** (2 hooks on `GameDataFunction::tryChangeNextStageWith{DemoWorldWarp,WorldWarpHole}`). Substitutes the `stage` arg if Layers 1+2 both miss. Substitution at this layer can produce broken cutscene visuals (Mario in destination kingdom without the Odyssey, frozen camera — see prior-iteration failure log below). Logs at WARN level so any backstop fire is a loud signal that an upstream catch needs adding.

**All substitutions go through the same helper** (`substituteSlotWorldId` in WorldMapSelectHook.cpp): if Orig returns a worldId for a gated kingdom whose prereq isn't met, substitute the prereq's worldId; otherwise pass Orig's value through. Log is throttled on (origin, index, orig_id) so per-frame UI re-queries don't flood.

**Gate policy lives in [KingdomOrderGate.{hpp,cpp}](../switch-mod/src/game/KingdomOrderGate.cpp)** as a pure module — reads `ApState::ap_moons_kingdom[]` (populated by M6 phase A's ItemMsg handler) against thresholds `kLakeRequiredForWooded=8` and `kSnowRequiredForSeaside=10`. Supporting helpers in [KingdomUnlock.{hpp,cpp}](../switch-mod/src/game/KingdomUnlock.cpp): `kingdomShortFromHomeStage` (stage-name routing), `kingdomShortFromWorldId` + `worldIdFromKingdomShort` (worldId↔kingdom mapping). The worldId helpers compose through M6 phase D's `kingdomBitForWorldId` so the Sea/Snow swap is honored — direct indexing into kKingdoms[] would mis-route the Seaside/Snow gate.

**UX side effect**: when both Lake and Wooded would appear in the same menu (post-Sand fork), both slots show "Lake" until the gate is satisfied — one natural, one substituted. Picking either flies to Lake. Cleaner than missing the fork entirely; could be polished by hooking `getUnlockWorldNumForWorldMap` to suppress the duplicate, but that requires careful index-mapping and isn't required for the gate to function.

**All 8 active symbols verified** in `scripts/check_nso_symbols.py` (HIT against SMO 1.0.0 main.nso). All symbol constants live in [HookSymbols.hpp](../switch-mod/src/hooks/HookSymbols.hpp) under the "M7 Path A" section.

**Pattern for future "lie to the game" hooks**: the three-layer approach (UI query → cinematic state → stage commit) generalizes — catch upstream of the visible state change, never just at the commit, or you'll see broken visuals from preloaded assets. Always include a BACKSTOP at commit with WARN logging.

### Iteration history (five attempts before landing on the working design)

1. **Skip Orig in `ChangeStageHook`** when destination is gated → world-map UI committed to the gated kingdom anyway; only that kingdom showed on next takeoff → soft-lock.
2. **Skip Orig in `DemoWorldWarpHook`** (post-Sand cutscene auto-flight) → cutscene played, Mario returned to Sand, same UI soft-lock.
3. **Substitute destination in `DemoWorldWarpHook`** (Wooded → Lake) → Mario landed in Lake but **no Odyssey ship, camera didn't follow Mario** — gated-kingdom cutscene assets were pre-loaded by earlier state-machine steps and stayed referenced after the destination flipped. Even nested-sanitizing the constructed `ChangeStageInfo` in a downstream `ChangeStageHook` didn't fix the visuals because the info object was already clean — the bug lives in the cutscene state, not the ChangeStageInfo.
4. **Hook `StageSceneStateWorldMap::exeDemoWorldSelect`** thinking it was the post-A-press confirmation handler → log proved it ONLY fires once per world-map open for the *opening animation* on the currently-highlighted (current) kingdom. The actual confirmation goes through `exeDemoWorldComment` → `exeExit` and on inspection neither of those receives the chosen kingdom in `mNextStageName` either: the world-map state machine carries the cursor position in a state-machine-local field and only writes to `mNextStageName` at the moment of commit via `tryChangeNextStageWithDemoWorldWarp`.
5. **Hook `isUnlockedWorld` to lie 'locked'** for gated kingdoms → the cursor could still land on Wooded; isUnlockedWorld isn't the cursor-selectability filter the world-map UI uses. Same playtest, **refuse `tryChange`** (return false without Orig) instead of substitute → SOFT-LOCKED the menu, only the previously-attempted gated kingdom showed next time (SMO's branch-selection state had registered "player picked Wooded" before tryChange was called).

**Why Layer 1 alone wasn't enough**: the post-Multi-Moon fork is a one-time cinematic that bypasses the regular world-map UI's per-slot query. On a clean save with the fork visible, `getUnlockWorldIdForWorldMap` never fired — `calcNextLockedWorldIdForWorldMap` is the fork-specific equivalent. Layer 2 catches it.

**Why Layer 3 exists despite the visual cost**: the playtest where Layer 2 wasn't yet wired showed `tryChange.Demo` firing with `stage='ForestWorldHomeStage'` for the fork — without an upstream catch, Mario would land in Wooded. Layer 3 ensures the gate is enforced as a last resort even if a future SMO update routes through a code path neither Layer 1 nor Layer 2 catches; the WARN log makes the visual cost visible as a signal to add the missing upstream catch.

### M7 Path A playtest loop

After a build in this worktree (`cmake --build switch-mod/build`), deploy to Ryujinx. If the build was configured without `-DRYU_PATH` the post-build hook doesn't auto-deploy — either reconfigure with `-DRYU_PATH=C:/Users/maxwe/AppData/Roaming/Ryujinx` and rebuild, or copy manually:

```pwsh
$RYU = "$env:APPDATA\Ryujinx\mods\contents\0100000000010000\smo-archipelago"
Copy-Item C:\Users\maxwe\Documents\smo_archipelago\.claude\worktrees\<name>\switch-mod\build\subsdk9  $RYU\exefs\subsdk9
Copy-Item C:\Users\maxwe\Documents\smo_archipelago\.claude\worktrees\<name>\switch-mod\build\main.npdm $RYU\exefs\main.npdm
```

Tail the mod log in another pane:

```pwsh
Get-Content "$env:APPDATA\Ryujinx\sdcard\atmosphere\contents\0100000000010000\smoap.log" -Wait -Tail 80
```

**Validation cases**:

1. **Fresh save, post-Sand Multi-Moon fork**: the "newly unlocked" cinematic should present Lake at both slots (where Wooded would have been is now Lake — duplicate is the documented UX cost). Picking the bottom slot should fly to Lake with full normal visuals. Expected log:
   ```
   [wmap.menu.NextLocked.Scene] SUB slot=0 origId=3 (Wooded) -> prereqId=4 (Lake) have=0 need=8
   [wmap.tryChange.Demo] FIRE stage='LakeWorldHomeStage' kingdom=Lake gated=0
   ```
   (No `BACKSTOP` line — Layer 2 caught it upstream of tryChange.)
2. **Regular world-map open** (post-fork, any later save): same Wooded→Lake substitution but via Layer 1:
   ```
   [wmap.menu.Id.Scene] SUB slot=3 origId=3 (Wooded) -> prereqId=4 (Lake) ...
   ```
3. **Allow path**: pick Lake (not gated) → no SUB line, no log noise, normal flight.
4. **Prereq satisfied**: grant 8 Lake moons via the AP server console (`/send Mario Lake Kingdom Multi-Moon` ×3 = 9 ≥ 8), re-open world map → Wooded appears as Wooded (no SUB), picking it flies cleanly.
5. **Same flow for Snow/Seaside post-Metro fork** — symmetric, threshold `kSnowRequiredForSeaside = 10`.

**If you ever see a `BACKSTOP substituting` WARN** in the log: a code path neither Layer 1 nor Layer 2 caught reached tryChange. Mario will still go to the prereq kingdom but with potentially broken cutscene visuals (Odyssey missing, frozen camera). Add a new hook for the missing upstream entry point; the BACKSTOP guarantees functional gating in the meantime.

**Kill switch**: flip `kGateEnabled = false` in [WorldMapSelectHook.cpp](../switch-mod/src/hooks/WorldMapSelectHook.cpp) to disable all substitution while keeping the throttled "SUB" log lines that show what WOULD have been substituted — useful for debugging without modifying game behavior.

## M7 phase A — capture lock

DONE 2026-05-16; separate work-item from Path A above, both shipped under the M7 umbrella. Retimed 2026-05-20 (see update below) — the working design now gates on `isActiveHackStartDemo` instead of a fixed-time table, and splits the release path by cap type. The historical narrative below preserves the journey that led there.

Captures Mario hasn't unlocked via AP now fail: `CaptureStartHook` trampoline (M4 read-only) flipped to deny-after-orig. After `Orig` runs and `getCurrentHackName` reports the SMO-internal hack_name (`TRex`, `Kuribo`, `KillerMagnum`, etc.), `captureBlocked(name)` checks `ApState::captures_unlocked.test(bit)`; if unset we queue a deferred release on the keeper. Reporting the AP location check is unconditional (preserves wire semantics — first touch sends `LocationCheck`, AP replies with the item, second touch succeeds). The journey to this design touched three real problems worth recording:

1. **No pre-startHack name lookup exists.** OdysseyDecomp confirmed: `IUsePlayerHack` has only `getPlayerHackKeeper()`, no `getHackName`; `EnemyStateHackStart::tryStart` calls `rs::startHack(self, other, 0)` with a NULL third arg; the canonical name only becomes readable via `PlayerHackKeeper::getCurrentHackName()` AFTER `startHack` populates the keeper. So the deny path has to run *after* `Orig`, not before — there's no "refuse the SensorMsg" alternative that knows the cap name.
2. **`captureBitFor()` was looking up against the wrong name space.** Pre-M7 the lookup table (`kCaptureNames` generated from apworld `items.json`) held English apworld names like `T-Rex` / `Bowser Statue`, but `getCurrentHackName()` returns SMO-internal Japanese-roman names like `TRex` / `StatueKoopa`. ~39 of 43 captures diverge (e.g. `Goomba`/`Kuribo`, `Bullet Bill`/`Killer`, `Banzai Bill`/`KillerMagnum`) — apworld is English, SMO internals are Japanese-engine. `captureBitFor` fail-opened (returned `0xff`) for nearly every capture. Fix: `scripts/sync_capture_table.py` now also reads `apworld/smo_archipelago/client/data/capture_map.json` and emits a parallel `kCaptureHackNames[i]` array; `captureBitFor` searches hack-names first (hot path — the deny gate) then falls back to apworld names (the M6-B apply path). Identity passthrough when `capture_map.json` is absent preserves fresh-clone behavior.
3. **`cancelHack()` is a no-op when called from inside `startHack`'s trampoline.** First try: hook `startHack`, after `Orig` and the AP-check fire, call `cancelHack(self)`. Logs showed `BLOCKED hack=TRex — cancelling` + clean return, but Mario stayed captured. Swapped to `forceKillHack` (the "kingdom transition teardown" hammer) — released Mario but despawned the enemy actor on slow-cinematic captures (T-Rex). Initial fix was to defer the kill via wall-clock delay (`pending_kill_keeper` + `pending_kill_at_ms` atomics drained from `DrawMainHook`'s `tickPendingUncapture`): 1s was too short for T-Rex (camera broke + despawn), 4s cleared most cinematics, T-Rex needed 6s, Bullet Bill/Zipper needed 2s to prevent out-of-logic moon grabs during the wait. The 2026-05-20 retiming replaces this entire table with the actual signal — see update below.

Symbols (initial): `kPlayerHackKeeperForceKillHack` (`_ZN16PlayerHackKeeper13forceKillHackEv`) added to `HookSymbols.hpp` and `scripts/check_nso_symbols.py`; mangling verified via `aarch64-none-elf-g++ -c`. Also added: `synthetic_uncapture_this_frame` flag on `ApState` for the standard "our own action — don't echo back to AP" defense-in-depth pattern.

### 2026-05-20 update — demo-end gate + tryEscapeHack split

The fixed-delay timer table (4000ms default, 6000ms TRex, 2000ms Killer/Fastener) was a proxy for "is the capture-entry cinematic over yet?". The actual signal is `PlayerHackKeeper::isActiveHackStartDemo()` — returns true while the dive-in demo is playing, false after. Polling it per frame from `tickPendingUncapture` releases the moment the demo ends, no per-cap tuning required.

Pattern was lifted from KGamer77's [SuperMarioOdysseyArchipelago](https://github.com/Kgamer77/SuperMarioOdysseyArchipelago) at `Mod/source/main.cpp:73`, which uses the same gate. KGamer77 also splits the release path by cap type: 7 inanimate captures (Cactus, BazookaElectric, Tree, RockForest, Guidepost, Manhole, HackFork) use the gentler `PlayerHackKeeper::tryEscapeHack` (no actor despawn) since they have no intro state machine to race against teardown; everything else keeps `forceKillHack` (synchronous teardown that prevents T-Rex et al from continuing their intro and null-dereffing on the cleared keeper — still the canonical T-Rex failure mode that ruled out `endHack` originally).

**Removed:** the fixed-delay constants (`kDeferredKillMs`, `kCapKillDelayOverrides`, `deferredKillMsForCap`) and the `pending_kill_at_ms` field on `ApState`.

**Added:** `kPlayerHackKeeperTryEscapeHack` (`_ZN16PlayerHackKeeper13tryEscapeHackEv`) and `kPlayerHackKeeperIsActiveHackStartDemo` (`_ZNK16PlayerHackKeeper21isActiveHackStartDemoEv`) in `HookSymbols.hpp` and `scripts/check_nso_symbols.py`; both demangled-verified via `aarch64-none-elf-c++filt`. `kCapsUsingTryEscape[]` table + `capUsesTryEscape()` helper in `CaptureStartHook.cpp`. Three new function-pointer slots (`s_forceKillHack`, `s_tryEscapeHack`, `s_isActiveHackStartDemo`) resolved at install time.

**Failure modes:** if `isActiveHackStartDemo` fails to resolve, the deny path is disabled entirely (captures go ungated — fails closed, since firing `forceKillHack` mid-cinematic was the original T-Rex crash). If `tryEscapeHack` fails to resolve, inanimate caps fall back to `forceKillHack` with the actor-despawn visual.

**Validated 2026-05-20 (Ryujinx):** T-Rex releases cleanly without crash and without the prior 6s wait; Bullet Bill releases before a moon grab is possible; Cactus releases without the actor-despawn pop. Phase 1.5b name re-verify guard preserved unchanged. Local cross-reference clone at `third_party/Kgamer77-SMOAP/` (gitignored — not committed).

## M7 phase B

2026-05-19: **DONE — Switch-side Mushroom-visit trigger** (third design, second working).

**Prior-iteration failure log.**

1. **M3-era `EndingHook` on `DemoPeachWedding::makeActorAlive`** (failed 2026-05-18). The first playtest fired goal after defeating Bowser in *Bowser's Kingdom*, well before the actual ending. Investigation against [MonsterDruide1/OdysseyDecomp](https://github.com/MonsterDruide1/OdysseyDecomp/blob/master/src/Demo/DemoPeachWedding.h): `DemoPeachWedding` is a generic actor — manages Peach + her Tiara subactor (`ティアラの目`) — registered in `ProjectActorFactory.cpp` alongside `DemoActorCapManHero`, `DemoActorCapManHeroine`, `DemoActorKoopaShip` (all wedding-cast actors that any demo BYAML can place). The Bowser's-Kingdom escape cutscene (Peach kidnapped in wedding dress) instantiates it too, so the hook can't distinguish "Peach in wedding garb is on screen for the kidnapping" from "Peach in wedding garb is on screen for the real ceremony". OdysseyDecomp has decompiled exactly *one* Demo class — no more-specific alternative exists.

2. **Bridge-side trigger on "Defeat Bowser and Escape the Moon" / "Long Journey's End" alias** (failed 2026-05-19). The 2026-05-18 fix hooked goal on the bridge via `MOON_NAME_ALIASES = {"Moon: Long Journey's End": "Defeat Bowser and Escape the Moon"}` — but "Long Journey's End" is actually the **Darker Side** completion Multi Moon (`mariowiki.com/Long_Journey's_End`: "the sole story mission of the Darker Side... accessible once the main game is completed and 500 Power Moons have been obtained"). Vanilla SMO awards NO Power Moon for clearing the main game; Mario is just deposited in Mushroom Kingdom after the wedding cutscene with nothing to collect. So the bridge would only ever fire goal post-Darker-Side (which gates on 500 moons — basically never in an AP run) or never.

**Fix (the working design).** Reuse the M7 Path A `visited_kingdoms` machinery for goal detection. `WorldMapSelectHook::markVisitedFromStage` ([WorldMapSelectHook.cpp](../switch-mod/src/hooks/WorldMapSelectHook.cpp)) already fires for every kingdom-transition stage commit (`tryChangeNextStageWithDemoWorldWarp` cinematic-flight + `tryChangeNextStageWithWorldWarpHole` regular-map portal-hole) and OR's a sticky bit in `ApState::visited_kingdoms`. The post-wedding cutscene drops Mario in `PeachWorldHomeStage` via `DemoWorldWarp` — the cinematic-flight path — so the existing chokepoint already catches it. The patch is one conditional at the bottom of `markVisitedFromStage`: when the 0→1 transition is for the `Mushroom` short name AND `ApState::goal_sent` is still false, call `smoap::ap::reportGoal()`. `goal_sent` is the existing flag the snapshot path encodes for HELLO replay; `SaveLoadHook` clears it on save load, so a different save can re-trigger.

`smoap::ap::reportGoal()` is re-introduced in [ApFrameBridge.cpp](../switch-mod/src/ap/ApFrameBridge.cpp) as a one-liner that pushes `StatusEvent{goal=true}` into the existing `outbound_status` ring — `ApClient::pumpOnce` already drains that queue and sends `encodeGoal` for `e.goal`. No new wire format, no new symbol.

**Apworld rename**. `data/locations.json`'s sole `victory: true` location changed from "Defeat Bowser and Escape the Moon" to "Arrive in the Mushroom Kingdom" to reflect the actual trigger. Access requirements (Banzai Bill capture + Bowser capture + Parabones skip) stay the same — reaching Mushroom Kingdom still requires beating Bowser.

**Bridge cleanup**. `MOON_NAME_ALIASES`, `VICTORY_LOCATION_NAME`, and the `loc_name == VICTORY_LOCATION_NAME` branch inside `report_check` are gone. The `Connected`-handler pre-arm by victory loc_id is gone (the location no longer carries an AP id after the rename anyway — `__init__.py` sets `location_game_complete.address = None`). The single goal producer is now `SwitchServer._on_goal` (fired by `goal` wire messages from the Switch) → `ctx.report_goal()`, which carries the `_goal_reported` latch for log hygiene across snapshot replays.

**Verification**: [tests/test_goal_on_victory_location.py](../apworld/smo_archipelago/tests/test_goal_on_victory_location.py) covers the new path (StatusUpdate emission, idempotency, and a regression guard that moon checks never trigger goal). Manual Ryujinx playtest pending the next end-of-game run.

## M-color — per-classification moon recolor

2026-05-20: **DONE.** Power Moons now tint in-game by AP classification (filler = vanilla yellow, progression = green, useful = cyan/blue, trap = red) on all three shine variants — 3D Shine, 2D ShineDot (mural / side-scrolling rooms), and ShineGrand. The shipped path is a `Shine::init` post-trampoline that writes the tint directly into the body material's color slots; the earlier matanim-frame-substitute path was wrong about what the matanim animated and has been retired.

**Prior-iteration failure log.**

1. **Inline patches at 4 `BL` sites inside `Shine::init`** (PR #108, retired 2026-05-20). The first design substituted W2 (matanim frame index) at four call sites to `rs::setStageShineAnimFrame`, then let `Color_fcl` play the requested frame. Visual bisection on SMO 1.0.0 (frames 1, 2, 3, 7) showed every frame produced similar reddish shades on 3D moons regardless of palette value — `Color_fcl` is an emission/highlight matanim ("moon is glowing red" overlay), not a body-diffuse-color driver. ShineDot (2D) showed no color change at all because its archive's Color matanim is a texture-pattern animation (`Color_ftp`) with no material-color stream, and it has no `Mcl` anim player allocated for the frame-substitute entry point. Three follow-up patches (`c9671d0` re-target via X19, `b07b5f2` translate list-index → BYML UniqueId, `564cd93` strip diagnostic logging) refined the wrong path; the underlying assumption stayed broken.

2. **Why frame substitution can't work for body color, ever.** `Color_fcl` is the wrong matanim *category*. SMO body color lives in material-parameter slots that the shader composes per variant (uncollected, collected/grey, scenario-locked, grand) — there is no single "color frame" to substitute. The matanim path can animate sparkle hue at best.

**Shipped approach.** Trampoline `Shine::init` and, after `Orig` finishes setting up the actor's model and materials, write the AP-classification tint directly into the body material via four SDK helpers resolved from `nn::ro::LookupSymbol`:

- `al::setMaterialProgrammable` — unlock runtime writes on the material
- `al::setModelMaterialParameterF32` — flip the `enable_<X>_mul_color` gates
- `al::setModelMaterialParameterRgba` — write the RGBA tint
- `al::isExistMaterial` — probe-before-set guard (the SDK setters NULL-deref on a missing material name)

Per-shine-type body material name (runtime-probed against the SMO 1.0.0 BFRES archives via `scripts/dump_shine_bfres.py`, gitignored local-only tool):

| `mShineType` (+0x1a0) | Variant | Body material |
|---|---|---|
| 0 | 3D Shine | `BodyMT` |
| 1 | ShineDot (2D) | `BodyMT00` |
| 2 | ShineGrand | `BodyMT` |

3D / Grand variants' shaders sample several slots depending on collected/grand state, so the override writes the same tint into `uniform0_mul_color`, `uniform1_mul_color`, `base_color_mul_color`, and `const_color0` (and flips each one's `enable_*` gate). The SDK silently no-ops slots the shader doesn't sample, so the over-write is cheap (~6 calls per init). ShineDot only needs `uniform0_mul_color` — its shader doesn't read the others, so the extra writes are skipped to keep the per-shine SDK-call count minimal.

**Per-variant palette intensity split** (both palettes in [ShineAppearanceHook.cpp](../switch-mod/src/hooks/ShineAppearanceHook.cpp)). The 3D / Grand composition path stacks the tint through multiple slots — the same RGBA reads as less saturated on the visible body. The 2D path is single-slot — the same RGBA reads as more saturated on the flat 2D-camera body. Two palettes are shipped: a 3D/Grand palette softened ~10% toward identity, and a ShineDot palette softened ~20% so the perceived saturation matches. Filler stays at identity (1, 1, 1) on both — unscouted moons look vanilla.

**Index → UniqueId translation** (load-bearing, kept from PR #108's `b07b5f2`). The Shine actor stores `mShineId` at +0x290, but that's a **list-INDEX into `mShineHintList`**, not the BYML UniqueId the palette is keyed on. `resolveShineIndexToUniqueId` walks `GameDataHolder` (cached on `ApState` by `DrawMainHook`) → `GameDataFile` (+0x20) → `mShineHintList` (+0x9A0) → `HintInfo[index].UniqueId` (+0x1F0 inside a 0x238-byte struct), bounded by `kShineHintListMaxIndex = 0x400`. Read-only, no allocation — safe inside the trampoline callback.

**Symbols added** to [HookSymbols.hpp](../switch-mod/src/hooks/HookSymbols.hpp) + `scripts/check_nso_symbols.py` (verified against the real `main.nso` dynstr): `al::setMaterialProgrammable`, `al::setModelMaterialParameterRgba`, `al::setModelMaterialParameterF32`, `al::isExistMaterial`. Mangling via the `aarch64-none-elf-g++ -c` forward-decl path (see `.claude/skills/smo-symbol-discovery/SKILL.md`).

**Guards.** Missing-symbol is non-fatal — `installShineAppearanceHook` only installs the trampoline if `setMaterialProgrammable` + `setModelMaterialParameterRgba` resolved; otherwise it logs and skips, leaving moons at vanilla yellow. Missing-material at runtime (a future SMO build with a renamed material) logs once per shine type via a 3-slot `s_warned[]` and skips that init — never NULL-derefs. The first 8 overrides per session log a one-line `[shine-color] override#N type=T unique_id=U palette=P` for visual confirmation that the path is wired, then stay quiet.

**Out of scope (future polish).** The collected/grey post-pickup variant still uses SMO's natural greyscale shader path — the tint stays applied, so a collected progression-green moon reads as a desaturated green rather than vanilla grey. Considered acceptable; reverting to vanilla grey on collect would need a second trampoline on the per-frame appearance update.

## M8

Apworld extensions + in-game ImGui + polish (incl. dedicated AP-credit HUD overlay).

## Phase 4 — Talkatoo% mode

2026-05-20 → 2026-05-21: **DONE — Ryujinx-verified end-to-end.** Opt-in seed option (`talkatoo_mode: true`) that turns Talkatoo into the player's moon-naming oracle: his speech bubble names AP-pool moons from the current kingdom, and Mario can only get credit for moons Talkatoo has actually spoken. Non-named non-progression moons silently fail to flag — Mario sees the get-cinematic with a "Blocked by Talkatoo!" label, then the moon respawns on save-reload. Builds on Phase 3's substitution path; adds a per-shine-uid named-set in `ApState` plus a `progression: true` schema in `locations.json` that exempts scenario-advancing moons from the block.

**Discovery path** (the long way to the right hook target):

1. **Talkatoo's actor class is `Poetter`.** German for "poet" — Nintendo's pun on the bird's lyrical hint-giving. Found three ways in parallel: OdysseyDecomp's `src/Scene/ProjectActorFactory.cpp` lists `{"Poetter", nullptr}` (class registered but body undecompiled); `GameDataFile::isExistPoetter()` / `getPoetterTrans()` track per-kingdom Talkatoo position; `MrKatzenGaming/SMO-SeededTalkatoo` (an existing public mod) pins anchor offsets `TableHookSym = 0x003afb08` + `GetRandomHookSym = 0x003afb1c` for SMO 1.0.0, and the dynsym scan confirmed both addresses fall inside `_ZN7Poetter7exeWaitEv` (`Poetter::exeWait`, 0x3afa50..0x3afe10). Talkatoo's MSBT character token is `Hint_Bird` but no `Hint_Bird_*` speech-text MSBT keys exist — the dialogue is composed at runtime via `tryFindShineMessage(shine_index)`, ruling out the MSBT-substitution pattern from M6 phase C / CappyMessageHook.

2. **Substitution lives at `GameDataFunction::tryFindShineMessage`**, not Talkatoo's own actor. Mangled: `_ZN16GameDataFunction19tryFindShineMessageEPKN2al9LiveActorEPKNS0_17IUseMessageSystemEii`. `Poetter::exeWait` picks a shine_index from `rs::calcShineIndexTableNameAvailable` then calls `tryFindShineMessage` to resolve it to a `char16_t*` UTF-16 display message; the pointer is stashed at `Poetter+0x130` for a downstream EventFlow to paint the speech bubble. By trampolining `tryFindShineMessage` with a vtable filter on the first arg (cmp `actor->vptr` against `_ZTV7Poetter`), we substitute the return value with a pointer into our own static UTF-16 buffer rotation — vanilla pipeline paints our text, no pane discovery needed.

3. **Block point is `GameDataFile::setGotShine`**, not `Shine::get`. The first iteration spiked `Shine::get` (the 11-instruction function that reads `ShineInfo*` at `this+0x120` and calls `setGotShine`). User playtest 2026-05-20 confirmed the hook never fired for `obj407` ("Chomp Through the Rocks") — that moon went through one of the OTHER four Shine entry points (`getDirect` / `getDirectWithDemo` / `receiveMsg` / `exeWaitRequestDemo`). All five funnel into `GameDataFunction::setGotShine` (an 8-byte stub) → `GameDataFile::setGotShine` (the ~1300-byte inner method), which is the universal chokepoint we'd already hooked since M4 (as `MoonGetHook` for AP credit reporting). Block lives inside the existing trampoline: read `(stage, obj_id)` from the `ShineInfo*` via the M4 layout mirror, resolve to `shine_uid` via the new `shineUidByStageObj` lookup over `shine_table.h`, and skip Orig if `talkatoo_mode` is on AND not in named set AND not `isProgressionShine`.

**Multi Moon / scenario-advance exemption** (the audit that turned 14 progression moons into 22):

A naive block rejects every unnamed moon — including Multi Moons. SMO's `scenario_no` advances on Multi Moon collection, gating downstream moons. Blocking the Cascade Multi Moon → kingdom 2 (Sand) inaccessible. Blocking Sand's Hariet Multi Moon → can't reach Knucklotec. Hard soft-lock on fresh-start playthroughs.

Solution: `progression: true` flag on locations.json entries, plumbed through `sync_shine_table.py` into a `bool progression` column in `shine_table.h`. `MoonGetHook` short-circuits the block for progression moons via `isProgressionShine(stage, obj)`. Audited against [mariowiki.com/Multi_Moon](https://www.mariowiki.com/Multi_Moon) per-kingdom plus story walkthroughs — final list is 22 moons:

- **Multi Moons** (the canonical scenario advancers): Cascade Multi Moon Atop the Falls; Sand × 2 (Hariet, Knucklotec); Lake Broodals; Wooded × 2 (Spewart, Torkdrift); Metro × 2 (Mechawiggler, Pauline); Snow Bound Bowl; Seaside Mollusque; Luncheon × 2 (meat, fight); Ruined Lord of Lightning; Bowser's RoboBrood.
- **Single-moon prereqs** that gate the kingdom's Multi Moon: Cascade's "Our First Power Moon" (story 1→2); Seaside's 4 seals (each gates Mollusque spawn); Bowser's 4-step chain (Infiltrate → Smart Bombing → Big Broodal Battle → Showdown).
- **Intentionally NOT flagged**: Cap (no in-kingdom gate, leave-immediately), Lost (no Multi Moon per Mario Wiki — single-scenario kingdom), Cloud / Mushroom / Moon (transitional / post-game one-moon kingdoms), Dark / Darker Side (post-credits, AP-pool exclusion handled separately).

Audit fixed two false positives that the initial pass had carried: `Lost: A Propeller Pillar's Secret` (Lost has no scenario advance per Mario Wiki) and `Wooded: Make the Secret Flower Field Bloom` (post-Torkdrift spawn, not strictly needed since Torkdrift MM already advances to scenario 5). Both removals are guarded by [tests/test_progression_moons.py](../apworld/smo_archipelago/tests/test_progression_moons.py).

**Iteration history** (failed attempts before the working design):

1. **`Shine::get` as block point.** Picked because it's the smallest function in the Shine class that calls setGotShine. Built a probe hook; user collected "Chomp Through the Rocks" and the probe never fired. BL-target scan of `.text` against `GameDataFunction::setGotShine` revealed 5 Shine entry points, not 1. Switched to `GameDataFile::setGotShine` (universal chokepoint, already hooked). [Discarded code: `kShineGet` constant + symbol entry kept for reference only.]
2. **Vtable range 0x400 for `_ZTV7Poetter`.** Initial pick; later tightened to 0x200 in response to a "mangled moon name in blocked cutscene" report — attributed (wrongly) to the substitute hook firing for cutscene-state actors whose vptrs happened to fall in the 0x208-byte overshoot region. User pointed out the substitute hook wasn't involved (the mangle was the BLOCKED moon's vanilla name with `mLatestGetShineInfo` left unset by the blocked `setGotShine`). The tighten was reverted; the real fix was painting "Blocked by Talkatoo!" via the existing `pending_moon_label` pipeline before the block returns. The 0x400 range is provably safe — the overshoot covers only Poetter's own auxiliary symbols (`_ZTT7Poetter`, `_ZTC7Poetter*`, `_ZTI7Poetter`) which aren't object vptrs in normal operation.
3. **Bring-up hardcoded probe (`"ARCHIPELAGO TEST MOON #1/2/3"`).** Substitute hook had a `talkatoo_mode` gate that prevented it from firing during the first playtest because the bridge connection was failing (Switch dialing 127.0.0.1:17777 but SMOClient hadn't bound the listener — Launcher needed the `--` separator before component args, which the loopback-test skill DOES document but I didn't follow first time). The hardcoded probe fires unconditionally for Poetter callers, so substitution would be visible even with bridge offline. Kept in the codebase as the AP-pool-empty fallback so a player in Talkatoo% mode with no pool data sees "ARCHIPELAGO TEST MOON" instead of silently falling back to vanilla.

**Wire shape**: substitute hook + block hook + named-set are all Switch-side. Bridge ships `talkatoo_pool` messages per kingdom (existing Phase 3 plumbing) and consumes `Check` messages for un-blocked moons (existing M4 path). No new bridge-side wire types for Phase 4. The named set is in-memory only — save+quit drops it. Acceptable per the "must speak to Talkatoo first" invariant; persistence is a known Phase 4 follow-up but not blocking.

**Symbols added** (verified in 1.0.0 via [check_nso_symbols.py](../scripts/check_nso_symbols.py), 37/37 total):

- `_ZN16GameDataFunction19tryFindShineMessageEPKN2al9LiveActorEPKNS0_17IUseMessageSystemEii` — substitute hook target.
- `_ZTV7Poetter` — vtable address for the Poetter filter.
- `_ZN7Poetter7exeWaitEv` — kept for reference; not currently hooked (see iteration #1).
- `_ZN5Shine3getEv` — kept for reference (the spike target that turned out not to be the chokepoint).

**Known limitations + handoff list**: see [docs/handoff-talkatoo.md](handoff-talkatoo.md). Two open gaps: bridge-side talkatoo_pool filter for progression moons (small), and Phase 5 sphere-safe ordering for general soft-lock prevention (the big one).

## Phase 5 — Talkatoo% sphere-safe ordering (Gap #3)

2026-05-21: **DONE.** Closes the second of Phase 4's two known follow-ups (Gap #1, progression-moon bridge filter, landed the same day in a separate commit; see [docs/handoff-talkatoo.md](handoff-talkatoo.md)). Phase 4 left Talkatoo naming AP-pool moons effectively at random from the per-kingdom set — fresh-start seeds could soft-lock when all three Talkatoo picks were gated behind a Capture or Cap item the player hadn't received. Phase 5 fixes this by computing a per-kingdom sphere-safe order at generation time and shipping it to the bridge, which serves Talkatoo a 3-entry cursor window from that order rather than the full pool.

**Algorithm**: greedy with random tie-breaking. New module [apworld/.../talkatoo_order.py](../apworld/smo_archipelago/talkatoo_order.py). For each kingdom in this slot's AP-pool, build a `CollectionState`, sweep its advancement items over all of this slot's locations EXCEPT the kingdom's non-progression pool, then greedy: at each step pick a uniformly random reachable moon from the remaining pool, collect its placed item to advance state, repeat. The resulting order is window=1-safe (each position is reachable when the cursor reaches it), which trivially implies window=3-safe.

**Right pessimism level**: the state used to validate sphere-safety isn't `precollected` (the handoff doc's sketch — too strict; Bowser's depends on items collected in earlier kingdoms) and isn't `sweep_for_advancements()` over everything (too optimistic — would auto-collect this kingdom's own non-progression moons before validation). The middle ground is "sweep over non-pool locations": represents the player's state when they first interact with Talkatoo in this kingdom. Spike confirmed this with a default-option Talkatoo% seed — the `precollected`-only model raised TalkatooOrderError on Bowser's (34/34 moons unreachable from start), the swept-non-pool model orders all 15 kingdoms cleanly (Sand 60, Metro 51, Wooded 48, Seaside 45, Luncheon 47, Bowser's 34, ...).

**Wire**: validator runs from `after_fill_slot_data` in [hooks/World.py](../apworld/smo_archipelago/hooks/World.py); ships as `slot_data["talkatoo_order"] = {kingdom_short_name: [shine_id, ...]}`. Bridge consumes in [client/context.py](../apworld/smo_archipelago/client/context.py)'s `_derive_and_push_talkatoo_pool` — splits into two paths now: when `talkatoo_order` is in slot_data, ship a cursor-window of 3 per kingdom (`order[cursor:cursor+3]`); when absent (older apworld builds), fall back to the Phase 4 full-pool filter. Cursor is computed live from `checked_locations`: smallest index whose location isn't already checked. RoomUpdate handler re-ships the pool when the new check is a moon in any kingdom's order (short-circuits for unrelated checks like captures or other-game collects).

**Switch-side hook unchanged.** `TalkatooSpeechHook.cpp`'s existing `pickThreeUncollectedFromKingdom` + `index % n` picker handles n=3 from the bridge as a natural special case — no code change needed. The Switch keeps treating the bridge pool as authoritative; only the contents shrunk from N-per-kingdom to ≤3-per-kingdom.

**Tests**: 14 new validator unit tests in [tests/test_talkatoo_order.py](../apworld/smo_archipelago/tests/test_talkatoo_order.py) using stub reachability oracles (no Archipelago import needed) — exercise the greedy algorithm, the window=3 invariant verifier, capture-gated chains, start-inventory honoring, and the multi-seed variety check (ensures the rng tie-break actually varies). 6 new bridge consumer tests in [tests/test_commands.py](../apworld/smo_archipelago/tests/test_commands.py) — Connected-time cursor init, cursor-skips-already-checked, empty-window-on-full-collection, RoomUpdate cursor advance, RoomUpdate skip-reship for unrelated checks, talkatoo_mode-off no-op guard. Integration scenario in [test_apworld_generation.py](../apworld/smo_archipelago/tests/test_apworld_generation.py) (gated on SMOAP_LIVE_AP=1). Manual end-to-end: `scripts/ap_generate.py` on `smo_talkatoo.yaml` produces a multidata zip whose slot_data contains `talkatoo_order` with all 15 kingdoms (Bowser's 34 entries, Cascade 19, Cap 9, ...).

**Loud-fail mode**: `TalkatooOrderError` raised when a kingdom's pool has no sphere-safe permutation even with the swept-non-pool state. The error message names the kingdom and pool size, and points at option toggles that typically over-constrain (capturesanity off, peace toggles, annoying-cluster filters). Not silent: generation fails, the user sees an actionable error rather than getting a seed that soft-locks at runtime.

## PopTracker pack

2026-05-17: **DONE — user-verified.** Independent logic-graph tracker that connects directly to AP's websocket alongside SMOClient. Generated from apworld data by [scripts/build_poptracker_pack.py](../scripts/build_poptracker_pack.py) — single-file stdlib-only generator that mirrors the id-allocation algorithm in [apworld/.../Game.py](../apworld/smo_archipelago/Game.py) (verified: `Cap: Frog-Jumping Above the Fog`→`14481151500` and `Cascade: Our First Power Moon`→`14481151511` match the M5.7 playtest's observed AP ids exactly). Parser for the apworld `requires` mini-language (`|Name:N|`, `{Func(args)}`, `and`/`or`, paren grouping); translator produces PopTracker OR-of-AND access_rules. Per-region prereq chains flattened at build time via [regions.json](../apworld/smo_archipelago/data/regions.json)'s `connects_to` graph; per-category yaml-option gates pulled from [categories.json](../apworld/smo_archipelago/data/categories.json). Lua ports of all ~30 functions in [Rules.py](../apworld/smo_archipelago/hooks/Rules.py) live in [poptracker/pack-src/scripts/logic.lua](../poptracker/pack-src/scripts/logic.lua), guarded on the same `capturesanity` check the Python uses. Yaml options + goal selection live in a Lua `OPTIONS` table populated by `Archipelago:AddClearHandler` from `slot_data` (`fill_slot_data` in [__init__.py](../apworld/smo_archipelago/__init__.py) already exports every non-common option) — all 20 logic-affecting options snap into place automatically; defaults match apworld defaults so offline-mode is sane.

**UI**: PopTracker has NO built-in locations panel or location-tree widget — the documented widget set is `container/dock/array/tabbed/group/item/itemgrid/map/layout/recentpins/text/canvas` (no `tree` / `locationtree` / `locations`). Locations are ONLY visible when placed as pins on a `map` widget. Pack ships a 740×560 dark-gray placeholder PNG (generated stdlib-only via `struct` + `zlib` in `make_solid_png`; ~2.5 KB) with the 16 kingdom buckets pinned on a 4×4 grid (Cap top-left, Captures bottom-right, ordering loosely follows linear-chain progression). Each kingdom is one top-level location with all its moons as `sections` (the DBFZ reference pack uses this flat shape — nested `children + sections` is two levels deeper than PopTracker accepts and silently breaks the location panel). Click a pin → kingdom drawer with section list; sections color by access-rule state.

**Iteration history** (3 swing-and-a-miss before user-verified): (1) tried `tracker_default: {type: "locationtree"}` — invented widget type, broke main view entirely; (2) added kingdom-level layout grouping (`children` of locations holding nested sections) — too deep for PopTracker's location format; (3) stripped layout to a `text` widget telling user to open View > Locations — that menu item doesn't exist, locations need maps to be visible at all. Map+pins approach is the only one that worked.

Pack zip ~27 KB; output at `poptracker/build/smo-poptracker-v<version>.zip`, gitignored — rebuild after any apworld change. 20 internal parser/translator/region-prereq tests pass (`python scripts/build_poptracker_pack.py --self-test`). Release workflow ([release.yml](../.github/workflows/release.yml)) builds the zip alongside `meatballs.apworld` on every tagged release; both ship as GitHub release assets with their own sha256 checksums.

## M6 phase-A playtest loop

Item injection runs through the AP server console, the same way every other apworld does it. Connect a slot, then from the AP server's command prompt:

```
/send Mario Cascade Kingdom Power Moon
/send Mario Cascade Kingdom Multi-Moon
/send Mario Goomba
/send Mario Sand Kingdom         (or whatever the kingdom-unlock item is named)
/hint Mario Cap: Frog-Jumping Above the Fog
```

(The earlier `/grant`, `/capture`, `/kingdom` client-side commands were removed 2026-05-17. They duplicated `/send` and had a name-resolution bug on the AP-received path — items arrived with no `name` field and rendered as `?` in-game. Fix was a one-line change in [datapackage.py](../apworld/smo_archipelago/client/datapackage.py) `ClassifiedItem.to_ref()` to always populate `name`, regardless of ItemKind. Regression test in [test_commands.py](../apworld/smo_archipelago/tests/test_commands.py).)

The surviving SMOClient `/`-commands are debug-only and run inside the Kivy command bar:

```
/smo_status                            (read-only tracker state)
/inject_deathlink TestRig manual       (synthesize an inbound DeathLink, no AP needed)
/help
```

## M6.6 (deferred, next milestone)

Channel B — Cappy speech bubble for items arriving *outside* the moon-get cutscene window (other players' checks routing items to us; late echoes; kingdom-unlock items; capture-unlock items). The wire format and bridge generation logic were sketched in [i-wrote-a-plan-fluffy-otter.md](../../.claude/plans/i-wrote-a-plan-fluffy-otter.md); the unknown is the Switch UI mechanism. Three candidates to spike:

1. **Hook SMO's `CapMessenger`** if it exists — lowest effort. Grep `OdysseyDecomp/src/Player/` for a class that surfaces tutorial-style speech bubbles. Confirmed missing from `.romfs-cache/syms310.ld` (that file's a sparse subset) but should appear in the real `main.nso` dynsym — re-run `scripts/check_nso_symbols.py` with candidate symbols added inline.
2. **Hijack the tutorial-bubble pane** — overwrite its text via `al::setPaneStringFormat` (already used by M6-A.5) and trigger its appear-animation. Medium effort.
3. **Custom toast overlay** via `agl::DrawContext` + a hand-rolled layout — pushed to M8 unless 1 + 2 both bust.

Bridge-side `CappyMsg` could ship ahead of UI as Channel-B-prime "log only" — the mod just `SMOAP_LOG_INFO`s incoming `cappy` messages until the UI mechanism lands. Useful for proving the AP→Bridge→Switch path works end-to-end before committing to a UI choice.

## M9 — exlaunch → LibHakkun + OdysseyHeaders + sail migration

Landed in two PRs:

- **[#151](https://github.com/mdietz94/smo_archipelago/pull/151)** (2026-05-21) — landed the parallel tree at `switch-mod-hk/` (`subsdk8`) alongside the production `switch-mod/` (`subsdk9`) so main stayed shippable while validation ran. End-to-end ported all 26 production trampolines + ApClient + ApState + game/ + ui/ off `HOOK_DEFINE_TRAMPOLINE` and `nn::ro::LookupSymbol` onto `HkTrampoline + installAtSym<>` + sail's `.sym`-resolved symbol DB. CreditsStartHook was the one inline-at-offset hook in the project — preserved as a Strategy A inline-BL patch at `+0x4C54A4`.
- **Phase 6 cutover** (2026-05-21 follow-up) — renamed `switch-mod-hk/ → switch-mod/`, flipped `MODULE_BINARY` from subsdk8 to subsdk9, dropped the `lunakit-vendor` + `exlaunch` submodules, updated CI / skills / docs. Talkatoo% Phase 4 + UDP bridge discovery + host tests (all of which had landed in `switch-mod/` after #151 merged) were ported forward to Hakkun primitives during the cutover.

### Why migrate

LibHakkun is the actively maintained subsdk runtime under the SMO modding stack. Production exlaunch + lunakit-vendor had three escalating problems:

1. **libstdc++ allocator NULL-derefs in worker thread** (M6.1 invariant). Whole categories of `std::*` operations were unsafe — leading to the `FlatHashSet` / `LineBuffer` / `copyFixedFieldN` workarounds throughout the codebase. Hakkun's musl + LLVM libc++ + `HeapSourceDynamic` addon (which re-exports `operator new` / `malloc` / `free` from SMO's own thread-safe allocator) lifts the restriction entirely.
2. **Manual sockaddr construction**. `nn::socket` didn't expose Nintendo's 16-byte FreeBSD-derived sockaddr layout, so we hand-built it in `ApClient.cpp`. `hk::socket::SocketAddrIpv4::parse(host, port)` encapsulates it.
3. **OdysseyDecomp forward-decl + `aarch64-none-elf-g++ + nm`** is workable for symbol mangling but doesn't scale to vtables, sub-tables, RTTI nodes. Sail's `.sym` DB is more durable.

### Real bugs surfaced + fixed during the migration

1. **AArch64 PC-relative prologue relocator in `HkTrampoline`**. Upstream LibHakkun copied a function's first instruction verbatim into the trampoline pool. For `adrp / adr / b / bl / b.cond / cbz / tbz` the same bytes at a different PC compute wrong addresses. Patched via `scripts/patch_hakkun.py` patches 7a/b/c — expanded TrampolineBackup to 8 slots, page-aligned, and the relocator emits `movz/movk + indirect/direct branch` sequences as needed. Upstream-PR-ready.
2. **`sm::ServiceManager::initialize()` is non-lazy**. Calling `instance()->getServiceHandle<"bsd:u">()` without the init+pid handshake null-derefs. `ApClient::initNetworking` now explicitly initializes `sm::` before bringing up `hk::socket`.
3. **Mangled-symbol length-prefix typo trap**. Sail emits whatever you write into `fakesymbols.so`; the linker resolves the typo; the runtime null-derefs because the typo'd name isn't in `main.nso`'s dynsym. Caught StaffRollScene 15→14 and nifm 28→27 / 19→18. Captured in [memory/project_sail_mangling_length_trap.md](../../.claude/projects/C--Users-maxwe-Documents-smo-archipelago/memory/project_sail_mangling_length_trap.md).
4. **`IUseSceneObjHolder` multi-inheritance offset adjustment**. The load-bearing fix that took 19 bisect phases. `al::Scene` multiply-inherits from `NerveExecutor`, `IUseAudioKeeper`, `IUseCamera`, `IUseSceneObjHolder`; `rs::isActiveCapMessage` / `rs::tryShowCapMessagePriorityLow` take `IUseSceneObjHolder*` and vtable-dispatch on it. Production exlaunch `static_cast<IUseSceneObjHolder*>(al::Scene*)`'d so the compiler inserted the offset; the phase-3b port had a TODO comment "doesn't matter" — wrong. Surfaced as Ryujinx ARMeilleure 0xC0000005 because the null-deref happened inside JIT-translated guest code.
5. **Worker-thread → CappyMessenger non-atomic state race**. Worker calls `CappyMessenger::enqueueSystem` were writing to non-atomic `queue_[]/tail_/live_count_` from the worker thread while `tryPump` reads + writes them on the frame thread. Routed through a new `inbound_system_bubbles` SPSC ring drained from drawMain.
6. **CappyMessenger settle gate: frame counter only → frame + wallclock combined**. The frame counter was a 60fps proxy for wallclock; under Ryujinx GPU stalls drawMain pauses (counter freezes) AND the emulator runs guest frames faster than wallclock during catch-up. Gate now requires both 600 frames AND 10000ms wallclock since scene change.

### What stayed the same

The wire protocol between Switch and SMOClient is byte-equivalent. Apworld / SMOClient / PopTracker pack are untouched by M9. The mod's behavior, command surface, and gameplay rules are unchanged — only the build toolchain swapped.

### Followups deferred to phase 7

- Retire the `FlatHashSet` / `LineBuffer` / `copyFixedFieldN` / `snprintf-to-stack-char[]` patterns since the worker thread can now use `std::*` freely. Vestigial; not load-bearing.
- Multi-version SMO support (1.0.1+) via `@smo:101,110,120,130` blocks in `VersionList.sym`.
- In-game tracker overlay (deferred M8) via `hk::gfx::DebugRenderer` (the Hakkun addon).
- Upstream the 10 Windows-port patches in `scripts/patch_hakkun.py` to `fruityloops1/LibHakkun`.

## Other follow-ups for next agents

- **`docs/extract-moon-data.md` could mention the M6 A.5 dependency**. Today it documents how to generate `shine_map.json` for the M5/M5.7 use case; Channel A *also* hard-depends on it. A new agent might think "I don't need moons resolved, I just want the cutscene labels" and skip the extract step — that's the same fail mode as the M6 A.5 playtest above.
- **`MAX_MOON_LABEL_BYTES = 30` is empirical**. Playtest only confirmed that short labels (`Got Cap Power Moon!`, ~20 bytes) render. Longer labels at the 30-byte cap may overflow the SMO font's pane width. Worth a separate playtest with a deliberately long label (REPL: `label Sent Wooded Kingdom Power Moon -> VeryLongPlayerName`).
- **Scout-cache cold-warmup race** — first 100-500 ms after AP `Connected` the scout cache hasn't absorbed all 560 `LocationInfo` entries yet. If Mario collects a moon in that window, bridge sends the LocationCheck but compose_moon_label returns None (cache miss), so cutscene shows vanilla. Mild UX issue; the M4.5 state-replay path on disconnect/reconnect always hits this case (no labels for retroactively-applied checks). Could mitigate by waiting on the scout cache to fully warm before flipping `display_enabled = True`.
- **`AP-server KeyError on scout for missing locations`** — fix at [context.py](../apworld/smo_archipelago/client/context.py): the warmup scopes to `ctx.missing_locations | ctx.checked_locations` instead of the full datapackage. Otherwise a single not-in-this-slot location_id in the scout request kills the websocket connection → client reconnect loop. Burned ~30 minutes finding this one during playtest setup.
- **`apworld/.../data/{items,locations,regions}.json` invariant**: the Multi-Moon rework removed the kingdom-agnostic `Power Moon` item but it was referenced in 19 `|Power Moon:N| or ...` branches across `regions.json` + `locations.json`. The DataValidation pass at seed gen catches this loudly. **Future agents removing or renaming any item must grep both files for the bare name and update all `requires` strings.** Today this is a manual discipline; a CI lint would catch it.
- **PopTracker pack is visually plain**: ships a 740×560 dark-gray placeholder PNG with 16 kingdom buckets on a 4×4 grid (see `scripts/build_poptracker_pack.py::make_solid_png`). Functional but ugly. A polish pass — proper kingdom artwork (one map per kingdom or a single composite world-map background), themed pin icons, maybe a side-panel that groups moons by sub-region — would make the tracker feel like a real companion app rather than a wireframe.
