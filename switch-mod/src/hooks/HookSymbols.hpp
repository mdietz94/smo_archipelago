// Mangled symbol catalog for hooks.
//
// All HkTrampoline::installAtSym<>() and hk::ro::lookupSymbol calls pull
// their mangled name from here so version bumps are isolated to this
// single file. Sail's symbol DB (switch-mod/syms/*.sym) carries the same
// names; the two must stay in sync.
//
// Provenance:
//   - A handful of symbols (drawMain, GameSystem::init, Scene::endInit)
//     are byte-identical to the names lunakit hooks on SMO 1.0.0.
//   - The rest were computed from MonsterDruide1/OdysseyDecomp forward-
//     declarations passed through aarch64-none-elf-g++. Itanium ABI
//     mangling is deterministic from the signature alone, so these names
//     are 1.0.0-correct as long as the decomp signatures match the
//     runtime symbol.
//
// To add a new hook, follow the smo-symbol-discovery skill — it covers
// the OdysseyDecomp forward-decl path, sail .sym entry, and the
// llvm-nm fakesymbols.so verification step.

#pragma once

namespace smoap::sym {

// --- Frame pump ---
// HakoniwaSequence::drawMain() const  (override of al::Sequence)
// Source: lunakit src/program/main.cpp UpdateLunaKit hook (verified on 1.0.0)
inline constexpr const char* kHakoniwaSequenceDrawMain =
    "_ZNK16HakoniwaSequence8drawMainEv";

// --- Game system init ---
// GameSystem::init()
// Source: lunakit src/program/main.cpp GameSystemInit hook (verified on 1.0.0).
// NOTE: NOT sead::GameSystem — this is SMO's GameSystem in the global
// namespace. Lunakit hooks it for the same reason we want it: late-enough
// in init that the heap is up but early enough to set up our subsystems.
inline constexpr const char* kGameSystemInit =
    "_ZN10GameSystem4initEv";

// --- Scene init (kingdom transition signal) ---
// al::Scene::endInit(const al::ActorInitInfo&)
// Source: lunakit src/program/main.cpp SceneEndInitHook (verified on 1.0.0)
inline constexpr const char* kAlSceneEndInit =
    "_ZN2al5Scene7endInitERKNS_13ActorInitInfoE";

// --- Moon flag set ---
// GameDataFile::setGotShine(const ShineInfo*)
// Source: MonsterDruide1/OdysseyDecomp src/System/GameDataFile.h:252
// Rationale: this is THE chokepoint that flips the moon-collected bit. It's
// called from setGotShine(GameDataHolderAccessor, const ShineInfo*) in
// GameDataFunction.cpp:528 and from the ShineActor on collect.
inline constexpr const char* kGameDataFileSetGotShine =
    "_ZN12GameDataFile11setGotShineEPK9ShineInfo";

// --- Moon flag read (M6 phase C — reconciliation snapshot) ---
// GameDataFile::isGotShine(int unique_id) const
// Source: lunakit-vendor/src/game/GameData/GameDataFile.h:310 (const member,
// int parameter, returns bool). One of three isGotShine overloads on
// GameDataFile; the (int) variant takes the unique shine id we read out of
// each HintInfo entry while walking mShineHintList.
// Verify offline with scripts/check_nso_symbols.py against the real 1.0.0
// main.nso before depending on it (same procedure as the rest of M6).
inline constexpr const char* kGameDataFileIsGotShineByUid =
    "_ZNK12GameDataFile10isGotShineEi";

// --- Capture acquired (gates + check) ---
// PlayerHackKeeper::startHack(al::HitSensor*, al::HitSensor*, al::LiveActor*)
// Source: MonsterDruide1/OdysseyDecomp src/Player/PlayerHackKeeper.h:47
// HOOK MODE: TRAMPOLINE in M4 (read-only check), REPLACE-with-conditional in M7
// (refuse if cap not unlocked). Third arg is the LiveActor we're hacking —
// extract its name via the actor's hack-data table to identify the cap-type.
inline constexpr const char* kPlayerHackKeeperStartHack =
    "_ZN16PlayerHackKeeper9startHackEPN2al9HitSensorES2_PNS0_9LiveActorE";

// PlayerHackKeeper::forceKillHack()
// Source: MonsterDruide1/OdysseyDecomp src/Player/PlayerHackKeeper.h
// Used by M7 to abort an in-progress capture when AP hasn't unlocked it.
// Called from CaptureStartHook callback right after Orig has populated the
// keeper, so we can read the cap name (getCurrentHackName) and decide to
// cancel without ever needing to know the name pre-startHack.
//
// We chose forceKillHack over cancelHack: playtested 2026-05-16, cancelHack
// returned cleanly (no crash) but did NOT release Mario — likely because the
// hack state machine hasn't fully entered the dive-in demo at the moment
// startHack returns, and cancelHack is a soft "player wants out" path that
// is a no-op in that window. forceKillHack is the hardest teardown, used
// internally for kingdom transitions / boss-defeat resets. Body is also
// not decompiled but the name + caller pattern suggests it's the right tool.
inline constexpr const char* kPlayerHackKeeperForceKillHack =
    "_ZN16PlayerHackKeeper13forceKillHackEv";

// PlayerHackKeeper::tryEscapeHack() — gentler release used for the 7
// inanimate captures (Cactus, BazookaElectric, Tree, RockForest, Guidepost,
// Manhole, HackFork). Those have no intro state machine to race against
// teardown, so the actor-despawn cost of forceKillHack is pure visual
// noise. KGamer77's SuperMarioOdysseyArchipelago uses the same split for
// the same 7 caps. If resolution fails the affected caps fall back to
// forceKillHack with the actor-despawn visual (CaptureStartHook logs).
inline constexpr const char* kPlayerHackKeeperTryEscapeHack =
    "_ZN16PlayerHackKeeper13tryEscapeHackEv";

// PlayerHackKeeper::isActiveHackStartDemo() const — true while the
// capture-entry "dive in" cinematic is playing, false after. M7's
// tickPendingUncapture polls it per frame and fires the release the
// instant it returns false — no fixed wall-clock delay. Replaces the
// prior per-cap delay table (4s default, 6s TRex, 2s Killer/Fastener).
// If resolution fails the M7 deny path is disabled entirely (captures
// go ungated), failing closed beats firing forceKillHack mid-cinematic
// which crashes T-Rex (the original T-Rex failure mode that pushed
// earlier iterations to the fixed-delay design in the first place).
// Pattern lifted from KGamer77/SuperMarioOdysseyArchipelago
// Mod/source/main.cpp:73.
inline constexpr const char* kPlayerHackKeeperIsActiveHackStartDemo =
    "_ZNK16PlayerHackKeeper21isActiveHackStartDemoEv";

// --- Scenario flag set ---
// GameDataFile::setMainScenarioNo(s32)  (s32 = int on aarch64)
// Source: MonsterDruide1/OdysseyDecomp src/System/GameDataFile.h:456
// Useful for tracker UI ("Mario is on Mission 3 of Cap Kingdom").
inline constexpr const char* kGameDataFileSetMainScenarioNo =
    "_ZN12GameDataFile17setMainScenarioNoEi";

// --- Save data load ---
// GameDataFile::initializeData()
// Source: MonsterDruide1/OdysseyDecomp src/System/GameDataFile.h:202
// CAVEAT: this is one candidate; it's the post-load init pass. If it doesn't
// fire on every save reload, fall back to hooking GameDataFile's read(...)
// override (search Ghidra for ByamlSave::read overrides on GameDataFile).
inline constexpr const char* kGameDataFileInitializeData =
    "_ZN12GameDataFile14initializeDataEv";

// --- Mario death (DeathLink outbound) ---
// PlayerHitPointData::kill()
// Source: lunakit-vendor/src/game/GameData/PlayerHitPointData.h:25
// Single chokepoint: all death paths (PlayerStateDamageLife, fall area,
// drown, poison, abyss) converge here when HP transitions to 0. Idempotent
// guard via ApState::death_pending_send so respawn-area double-calls don't
// re-fire DeathLink bounces.
inline constexpr const char* kPlayerHitPointDataKill =
    "_ZN18PlayerHitPointData4killEv";

// =============================================================================
// M6 — moon counter HUD substitution (phase A).
// =============================================================================
//
// Goal: surface AP-credit counts in the in-game moon counter without flipping
// any actual shine flags. We hook the two getters SMO uses for HUD/menu
// rendering and return orig() + our AP-credit total.
//
// Provenance: forward-declared in lunakit-vendor/src/game/GameData/
// GameDataFunction.h:129,131 (cited from OdysseyDecomp). Mangled via
// aarch64-none-elf-g++ -c on a minimal forward-decl TU (see scripts/check_
// nso_symbols.py for the full symbol list verified against main.nso).

// GameDataFunction::getCurrentShineNum(GameDataHolderAccessor)
// Returns total moon count across all kingdoms (HUD top-left "x/N").
inline constexpr const char* kGameDataFunctionGetCurrentShineNum =
    "_ZN16GameDataFunction18getCurrentShineNumE22GameDataHolderAccessor";

// GameDataFunction::getGotShineNum(GameDataHolderAccessor, s32 file_id)
// CAVEAT (M6 phase D audit): the int parameter is `file_id` (save-slot index,
// default -1), NOT a world id, per OdysseyDecomp src/System/GameDataFunction.h.
// Returns global lifetime collected moons from the given save slot. SMO's
// per-kingdom HUD reads GameDataFile::getShineNum(world_id) directly via
// inlined field access — there's no symbol to hook for the per-kingdom view.
// CLAUDE.md's note that this hook never fires in normal Cascade play is
// consistent with that finding. The misleading "GotShineNum" reads as
// "per-kingdom" because that's the natural interpretation of "world" in SMO
// terminology, but the API is save-slot-scoped. Kept hooked as defense.
inline constexpr const char* kGameDataFunctionGetGotShineNum =
    "_ZN16GameDataFunction14getGotShineNumE22GameDataHolderAccessori";

// =============================================================================
// M6 phase D — moon-deposit observation (drives PayShineNum snapshot to bridge).
// =============================================================================
//
// When Mario hand-tosses moons at an Odyssey, vanilla SMO does NOT mutate
// `mShineNum` directly. It calls GameDataFile::addPayShine(s32) which grows
// a separate `PayShineNum` counter; the HUD's spendable-fuel total is
// `getCurrentShineNum() = ShineNum - PayShineNum` clamped to 0.
//
// GameDataFile::addPayShine(s32) IS INLINED into all callers in 1.0.0 main.nso
// and not present in dynsym. The PUBLIC WRAPPER GameDataFunction::addPayShine
// (GameDataHolderWriter, int) IS in dynsym — verified via
// scripts/check_nso_symbols.py. Same hookability pattern as addHackDictionary.
// All game-side payment paths route through this wrapper per OdysseyDecomp.
//
// addPayShineCurrentAll() is the "pay everything in current kingdom" variant
// (probably used in kingdom-complete celebrations). We hook it secondarily so
// the snapshot fires for that path too.
//
// getCurrentWorldIdNoDevelop is the safe `getCurrentWorldId` variant that
// clamps the develop-state `-1` return to 0. We resolve it via
// nn::ro::LookupSymbol once and call through a function pointer (same as
// addHackDictionary), since this is read-only inside our hook callback.
//
// getPayShineNum is the per-(worldId) reader; we resolve it the same way and
// call it from ApState::buildPaySnapshot to populate a per-kingdom totals
// array. The snapshot ships on every toss + on HELLO via PaySnapshotMsg —
// the bridge derives outstanding = moons_received − PayShineNum, which is
// crash-survivable (save rollback shrinks PayShineNum, outstanding rebounds).
inline constexpr const char* kGameDataFunctionAddPayShine =
    "_ZN16GameDataFunction11addPayShineE20GameDataHolderWriteri";
inline constexpr const char* kGameDataFunctionAddPayShineCurrentAll =
    "_ZN16GameDataFunction21addPayShineCurrentAllE20GameDataHolderWriter";
inline constexpr const char* kGameDataFunctionGetCurrentWorldIdNoDevelop =
    "_ZN16GameDataFunction26getCurrentWorldIdNoDevelopE22GameDataHolderAccessor";
// GameDataFunction::getPayShineNum(GameDataHolderAccessor, s32 worldId)
// Source: lunakit-vendor/src/game/GameData/GameDataFunction.h:175
// Same mangling pattern as kGameDataFunctionGetGotShineNum (the (Accessor,
// s32) shape). Verified HIT in real 1.0.0 main.nso via check_nso_symbols.py.
inline constexpr const char* kGameDataFunctionGetPayShineNumByWorld =
    "_ZN16GameDataFunction14getPayShineNumE22GameDataHolderAccessori";

// =============================================================================
// M6 phase B — capture grant (addHackDictionary + idempotency probe).
// =============================================================================
//
// Goal: when AP grants a capture item, the mod writes the cap's hack_name into
// SMO's hack dictionary so the capture compendium / gameplay treats it as
// owned. Idempotency uses isExistInHackDictionary to skip redundant calls.
//
// Provenance: same OdysseyDecomp forward-decls in lunakit-vendor/src/game/
// GameData/GameDataFunction.h:361,362. Mangled via aarch64-none-elf-g++ -c.

// GameDataFunction::addHackDictionary(GameDataHolderWriter, const char* hack_name)
inline constexpr const char* kGameDataFunctionAddHackDictionary =
    "_ZN16GameDataFunction17addHackDictionaryE20GameDataHolderWriterPKc";

// GameDataFunction::isExistInHackDictionary(GameDataHolderAccessor, const char* hack_name)
inline constexpr const char* kGameDataFunctionIsExistInHackDictionary =
    "_ZN16GameDataFunction23isExistInHackDictionaryE22GameDataHolderAccessorPKc";

// =============================================================================
// M6 phase A.5 — moon-get cutscene label substitution (Channel A).
// =============================================================================
//
// Goal: when Mario collects a moon, replace the cutscene's "TxtScenario" pane
// text with AP-aware text (e.g. "Sent Cap Power Moon -> P3" / "Got X!").
//
// All four target symbols verified in SMO 1.0.0 main.nso .dynsym via
// scripts/check_nso_symbols.py (HIT). Layout offsets + pane name derived
// from disassembling each call site (see plan
// i-wrote-a-plan-fluffy-otter.md "Phase 0 findings").
//
// Three cutscene-state functions cover all moon collects:
//   - StageSceneStateGetShine::exeDemoGet — regular moons
//       layout @ self+0x20, pane "TxtScenario"
//   - StageSceneStateGetShineMain::exeDemoGetStart — main story moons
//       layout @ self+0x40, pane "TxtScenario"
//   - StageSceneStateGetShineGrand::exeDemoGetStart — grand shines
//       layout @ self+0x40, pane "TxtScenario"
//
// (StageSceneStateGetShineGrand::exeDemoGetFirst exists but doesn't touch
// the title pane — multi-grand-shine intro state, no label work needed.)

// StageSceneStateGetShine::exeDemoGet()  — regular moon cutscene (every frame)
inline constexpr const char* kStageSceneStateGetShineExeDemoGet =
    "_ZN23StageSceneStateGetShine10exeDemoGetEv";

// StageSceneStateGetShineMain::exeDemoGetStart()  — story moon cutscene
inline constexpr const char* kStageSceneStateGetShineMainExeDemoGetStart =
    "_ZN27StageSceneStateGetShineMain15exeDemoGetStartEv";

// StageSceneStateGetShineGrand::exeDemoGetStart()  — grand shine cutscene
inline constexpr const char* kStageSceneStateGetShineGrandExeDemoGetStart =
    "_ZN28StageSceneStateGetShineGrand15exeDemoGetStartEv";

// al::setPaneStringFormat(al::IUseLayout*, char* paneName, char* fmt, ...)
// SDK helper that writes formatted text into a named pane. VARARG (the
// trailing `z` in the mangled name = `...`). We pass "%s" as the format
// and the AP text as the single arg so any non-`%` characters are safe.
inline constexpr const char* kAlSetPaneStringFormat =
    "_ZN2al19setPaneStringFormatEPNS_10IUseLayoutEPKcS3_z";

// =============================================================================
// "Cappy Messenger" — in-game speech bubble for AP item notifications.
// =============================================================================
//
// Goal: route AP item notifications through SMO's existing Cappy speech-bubble
// pipeline (rs::tryShowCapMessage*) so we get Nintendo's font, layout, and
// animation for free — sidestepping the sead::TextWriter font-init dead end.
//
// Mechanism: call rs::tryShowCapMessagePriorityLow with a magic label
// (kArchipelagoLabel below). The MSBT lookup for that label is intercepted by
// MessageHolderTryGetTextHook, which returns a pointer to our own UTF-16
// buffer holding "Got <name> from <sender>!". The rest of the CapMessage
// pipeline runs unmodified.
//
// Provenance: all 3 symbols mangled via aarch64-none-elf-g++ from forward
// decls matching MonsterDruide1/OdysseyDecomp lib/al/Library/Message/
// MessageHolder.h and src/MapObj/CapMessageShowInfo.h, and verified present in
// real 1.0.0 main.nso via scripts/check_nso_symbols.py.

// CapMessageLayout::exeDelay does:
//   if (isStageMessage)
//      if (isExistLabelInStageMessage(holder, mstxt, label))
//          text = getStageMessageString(holder, mstxt, label);
//   else
//      if (isExistLabelInSystemMessage(holder, mstxt, label))
//          text = getSystemMessageString(holder, mstxt, label);
//
// rs::tryShowCapMessagePriorityLow passes isStageMessage=false so only the
// system path fires in our scenario, but we hook all 4 for robustness in
// case future code paths use the stage variant. Each trampoline:
//   - returns our buffer / true when the label matches kArchipelagoLabel
//     and CappyMessenger has a buffer ready
//   - otherwise tail-calls Orig
//
// These DON'T go through al::MessageHolder::tryGetText — that's a deeper
// internal that some lookups bypass entirely. Hooking the top-level
// per-mstxt-file accessors above is what actually intercepts CapMessage's
// text resolution. The (string-existence-bool, get-string-text) pair maps
// 1:1 to our (in_use_flag, buffer_pointer) state.

inline constexpr const char* kAlIsExistLabelInSystemMessage =
    "_ZN2al27isExistLabelInSystemMessageEPKNS_17IUseMessageSystemEPKcS4_";
inline constexpr const char* kAlGetSystemMessageString =
    "_ZN2al22getSystemMessageStringEPKNS_17IUseMessageSystemEPKcS4_";
inline constexpr const char* kAlIsExistLabelInStageMessage =
    "_ZN2al26isExistLabelInStageMessageEPKNS_17IUseMessageSystemEPKcS4_";
inline constexpr const char* kAlGetStageMessageString =
    "_ZN2al21getStageMessageStringEPKNS_17IUseMessageSystemEPKcS4_";

// rs::tryShowCapMessagePriorityLow(const al::IUseSceneObjHolder*,
//                                  const char* label, s32 delay, s32 wait)
// The "polite" Cappy-speech entry point: queues behind any active high-
// priority message and returns false silently if the scene/state can't accept
// one (2D, cutscene, paused). We call this each frame from CappyMessenger::
// tryPump until it returns true.
//
// We do NOT trampoline this — we resolve the address via nn::ro::LookupSymbol
// and call through a function pointer (same pattern as M6 phase B's
// addHackDictionary).
inline constexpr const char* kRsTryShowCapMessagePriorityLow =
    "_ZN2rs28tryShowCapMessagePriorityLowEPKN2al18IUseSceneObjHolderEPKcii";

// rs::isActiveCapMessage(const al::IUseSceneObjHolder*)
// Probe: returns true while a CapMessage is on screen. CappyMessenger::tryPump
// checks this to keep our backing buffer stable for the duration of an active
// balloon (the buffer must not be overwritten while SMO is reading from it).
//
// Also resolved via LookupSymbol + function pointer.
inline constexpr const char* kRsIsActiveCapMessage =
    "_ZN2rs18isActiveCapMessageEPKN2al18IUseSceneObjHolderE";

// =============================================================================
// M-color — per-shine palette override (AP classification -> moon color).
// =============================================================================
//
// Implemented as a trampoline on Shine::init in hooks/ShineAppearanceHook.cpp.
// After Orig finishes the actor's model + material setup, we write the
// AP-classification tint directly into the body material's color slots
// via the SDK helpers below.
//
// Earlier approach (W2 substitution at 4 BL sites inside Shine::init
// targeting rs::setStageShineAnimFrame) was abandoned 2026-05-20: that
// path only animates the `Color_fcl` emission/glow matanim, which can't
// produce per-classification distinct body colors no matter what frame
// is selected. The material-parameter override below bypasses the
// matanim entirely.

// Shine::init — the function we trampoline.
inline constexpr const char* kShineInit =
    "_ZN5Shine4initERKN2al13ActorInitInfoE";

// al::setMaterialProgrammable(LiveActor*) — toggles the actor's materials
// from "stage-anim driven" to "code-driven". Precondition for runtime
// parameter writes.
inline constexpr const char* kAlSetMaterialProgrammable =
    "_ZN2al23setMaterialProgrammableEPNS_9LiveActorE";

// al::setModelMaterialParameterRgba(const LiveActor*, const char* mat,
//                                   const char* param, const sead::Color4f&)
// Writes an RGBA color directly into a named shader parameter on a named
// material. Same family lunakit uses for Puppet outfit recolor.
inline constexpr const char* kAlSetModelMaterialParameterRgba =
    "_ZN2al29setModelMaterialParameterRgbaEPKNS_9LiveActorEPKcS4_RKN4sead7Color4fE";

// al::setModelMaterialParameterF32(const LiveActor*, const char* mat,
//                                  const char* param, float value)
// Used to flip `enable_<X>_mul_color` shader gates that Nintendo's
// shaders use to enable/disable the corresponding color slot.
inline constexpr const char* kAlSetModelMaterialParameterF32 =
    "_ZN2al28setModelMaterialParameterF32EPKNS_9LiveActorEPKcS4_f";

// al::isExistMaterial(const LiveActor*, const char* name) — probe-before-
// set guard so a renamed material in a future SMO build logs and skips
// instead of crashing (the parameter setters do NOT bounds-check).
inline constexpr const char* kAlIsExistMaterial =
    "_ZN2al15isExistMaterialEPKNS_9LiveActorEPKc";

// al::isExistModel(const LiveActor*) — null-safe model-keeper presence
// probe. Required GUARD before any model-touching call (isExistMaterial,
// setMaterialProgrammable, setModelMaterialParameter*) — those deref
// mModelKeeper unconditionally and crash if the actor hasn't allocated
// its model. Hit during Cascade reload after the first multi-moon: an
// already-collected linked-Shine inside an AppearSwitchTimer is init'd
// by rs::tryInitLinkShine without a model archive. OdysseyDecomp uses
// this same probe in AppearSwitchTimer/CapTargetInfo for the same reason.
inline constexpr const char* kAlIsExistModel =
    "_ZN2al12isExistModelEPKNS_9LiveActorE";

// =============================================================================
// M7 Path A — fork-cinematic kingdom-order gate (two-layer architecture).
// =============================================================================
//
// Forces linear progression at SMO's two world-map bifurcations only at the
// FORK CINEMATIC moment by substituting gated worldIds with their prerequisite
// kingdom's worldId. The regular (post-fork) world map is intentionally NOT
// hooked — once the cinematic has flown Mario to the prereq kingdom, both
// kingdoms are unlocked on the regular map and the player can travel freely.
//
// Three symbols total, organized in two layers:
//
//   Layer 1 — post-Multi-Moon FORK cinematic per-slot query (2 overloads):
//     The one-time "newly unlocked" presentation that plays right after
//     collecting a kingdom's Multi-Moon. Verified firing as the Scene overload
//     in the 2026-05-17 fork playtest.
//
//   Layer 2 — cinematic stage-commit BACKSTOP (1 function):
//     `tryChangeNextStageWithDemoWorldWarp` — "Demo" = cutscene; this is the
//     cinematic flight path. If Layer 1 misses, this rewrites the stage arg.
//     Substitution may produce broken cutscene visuals (per CLAUDE.md M7
//     section's prior-iteration failure log), so the WARN log on a fired
//     backstop is a signal that an upstream catch needs adding.
//
// All 3 verified against SMO 1.0.0 main.nso via scripts/check_nso_symbols.py.
//
// (The regular-map equivalents — getUnlockWorldIdForWorldMap (4 overloads) and
// tryChangeNextStageWithWorldWarpHole — were intentionally removed; their
// substitutions blocked free travel after the fork and combined with a stale
// threshold gate produced a soft-lock where players with high lifetime Snow
// AP-receipts but no Snow visit got trapped in Seaside.)

// ---- Layer 1: post-Multi-Moon FORK cinematic ----
inline constexpr const char* kGameDataFunctionCalcNextLockedWorldIdForWorldMap_LayoutActor =
    "_ZN16GameDataFunction32calcNextLockedWorldIdForWorldMapEPKN2al11LayoutActorEi";
inline constexpr const char* kGameDataFunctionCalcNextLockedWorldIdForWorldMap_Scene =
    "_ZN16GameDataFunction32calcNextLockedWorldIdForWorldMapEPKN2al5SceneEi";

// ---- Layer 2: cinematic stage-commit BACKSTOP (substitutes + sets visited) ----
inline constexpr const char* kGameDataFunctionTryChangeNextStageWithDemoWorldWarp =
    "_ZN16GameDataFunction35tryChangeNextStageWithDemoWorldWarpE20GameDataHolderWriterPKc";

// ---- Regular-map stage-commit (visited-only; NOT used for substitution) ----
// Hooked to set the sticky visited bit when Mario actively flies between
// kingdoms via the regular world map. Distinguishes "actually traveled here"
// from "save-data load happened to place Mario here" — save load doesn't go
// through tryChange, so reloading into Lake leaves visited[Lake] cold (the
// gate's current-kingdom OR-check handles that case without polluting the
// sticky mask).
inline constexpr const char* kGameDataFunctionTryChangeNextStageWithWorldWarpHole =
    "_ZN16GameDataFunction35tryChangeNextStageWithWorldWarpHoleE20GameDataHolderWriterPKc";

// =============================================================================
// Talkatoo% mode — speech-bubble substitution.
// =============================================================================
//
// Hook target + filter symbol for the Phase 3 substitution. See
// hooks/TalkatooSpeechHook.cpp for the design rationale (vtable filter rather
// than per-callsite hook so cutscenes / pause menu / hint UI fall through).

// Talkatoo's vtable. Read via hk::ro::lookupSymbol once at install time;
// the trampoline compares actor*->vtable (offset 0) against this to confirm
// the caller is a Poetter before substituting.
inline constexpr const char* kPoetterVTable =
    "_ZTV7Poetter";

// GameDataFunction::tryFindShineMessage(const al::LiveActor* actor,
//                                       const al::IUseMessageSystem* sys,
//                                       s32 world_id, s32 index)
// Returns a `const char16_t*` to the per-shine display message string.
// Hooked in TalkatooSpeechHook.cpp — substitute return value with our own
// UTF-16 buffer holding an AP-pool moon name when caller is Poetter.
inline constexpr const char* kGameDataFunctionTryFindShineMessage =
    "_ZN16GameDataFunction19tryFindShineMessageEPKN2al9LiveActorEPKNS0_17IUseMessageSystemEii";

// Note (2026-05-23): we tried hooking the rs:: wrappers Talkatoo actually
// calls (`rs::tryUnlockShineName(LiveActor*, s32)` and
// `rs::calcShineIndexTableNameAvailable(s32)`). Both manglings (const and
// non-const actor variants) froze SMO's init thread inside
// HkTrampoline::installAtSym — the symbols are inlined into
// `Poetter::exeWait` and not exported in SMO 1.0.0 main.nso, so sail's
// fakelib stub resolved to a bogus address that the prologue relocator
// infinite-looped on. Fix lives at the read side instead — see the
// "force-false isOpenShineName under talkatoo_mode" strategy in
// hooks/TalkatooMenuMarkHook.cpp. Do not re-add rs::tryUnlockShineName or
// rs::calcShineIndexTableNameAvailable to this catalog without first
// verifying the symbol exists via llvm-nm against the real main.nso.

// =============================================================================
// Talkatoo% mode — pause-menu mark fix.
// =============================================================================
//
// Phase 4 substitutes Talkatoo's speech bubble with AP-pool moon names but
// leaves SMO's per-(world, idx) "this moon's name is revealed" state pointing
// at the vanilla picker's choice. The pause-menu Power Moon list then marks
// the wrong row. TalkatooMenuMarkHook.cpp fixes that with two trampolines:
//
//   1. GameDataFile::isOpenShineName(s32 world_id, s32 index) const — the
//      getter the menu queries. We OR-in our AP-pool named set so the row
//      corresponding to the moon Talkatoo actually said is the one marked.
//
//   2. GameDataFile::tryUnlockShineName(s32 world_id, s32 index) — the
//      vanilla setter Talkatoo calls. We suppress writes in Talkatoo% mode
//      so the vanilla picker's pick doesn't pollute the menu.
//
// Translating (world_id, index) → shine_uid uses GameDataFile::findShine,
// resolved at install time as a function pointer (called with `this` as the
// implicit first arg per the Itanium ABI; same pattern as addHackDictionary).
// All three symbols verified present in SMO 1.0.0 main.nso 2026-05-22.

// GameDataFile::isOpenShineName(s32 world_id, s32 index) const
inline constexpr const char* kGameDataFileIsOpenShineName =
    "_ZNK12GameDataFile15isOpenShineNameEii";

// GameDataFile::tryUnlockShineName(s32 world_id, s32 index)  (non-const)
inline constexpr const char* kGameDataFileTryUnlockShineName =
    "_ZN12GameDataFile18tryUnlockShineNameEii";

// GameDataFile::findShine(s32 world_id, s32 index) const → const HintInfo*
inline constexpr const char* kGameDataFileFindShine =
    "_ZNK12GameDataFile9findShineEii";

// =============================================================================
// OdysseyRescue — Lost + Ruined Kingdom softlock prevention.
// =============================================================================
//
// Both kingdoms physically ground the Odyssey on arrival and block
// backtracking. In our randomizer the next-kingdom-required moons can land
// anywhere in the pre-arrival reachable set, so a player who rushes in may
// arrive with 0 AP credits and no way back to grab the unswept upstream
// checks → permanent softlock. Fix mirrors Kgamer77/SuperMarioOdysseyArchipelago
// v1.2's updatePlayerInfo(): per-frame sweep on drawMain (throttled ~60
// frames) that detects the broken state and force-repairs via SMO's own
// named GameDataFunction:: entry points.
//
// All 10 manglings verified via aarch64-none-elf-g++ -c on forward-decls
// matching MonsterDruide1/OdysseyDecomp src/System/GameDataFunction.h, and
// the unlockWorld / isUnlockedWorld names were already in the project
// pre-c85a27b cleanup with the same manglings.

inline constexpr const char* kGameDataFunctionIsCrashHome =
    "_ZN16GameDataFunction11isCrashHomeE22GameDataHolderAccessor";
inline constexpr const char* kGameDataFunctionRepairHome =
    "_ZN16GameDataFunction10repairHomeE20GameDataHolderWriter";
inline constexpr const char* kGameDataFunctionCrashHome =
    "_ZN16GameDataFunction9crashHomeE20GameDataHolderWriter";
inline constexpr const char* kGameDataFunctionUnlockWorld =
    "_ZN16GameDataFunction11unlockWorldE20GameDataHolderWriteri";
inline constexpr const char* kGameDataFunctionIsBossAttackedHome =
    "_ZN16GameDataFunction18isBossAttackedHomeE22GameDataHolderAccessor";
inline constexpr const char* kGameDataFunctionRepairHomeByCrashedBoss =
    "_ZN16GameDataFunction23repairHomeByCrashedBossE20GameDataHolderWriter";
inline constexpr const char* kGameDataFunctionIsRepairHomeByCrashedBoss =
    "_ZN16GameDataFunction25isRepairHomeByCrashedBossE22GameDataHolderAccessor";
inline constexpr const char* kGameDataFunctionGetWorldIndexClash =
    "_ZN16GameDataFunction18getWorldIndexClashEv";
inline constexpr const char* kGameDataFunctionGetWorldIndexSky =
    "_ZN16GameDataFunction16getWorldIndexSkyEv";
inline constexpr const char* kGameDataFunctionGetCurrentStageName =
    "_ZN16GameDataFunction19getCurrentStageNameE22GameDataHolderAccessor";

// =============================================================================
// Instant seed growth — bypass the wait on seed-flower moons.
// =============================================================================
//
// SMO's seed-pot moons (Sand Tostarena, Wooded Steam Gardens, etc.) compare
// (now - planted_time) against a bloom threshold. GrowSeedInstantHook.cpp
// trampolines this getter to return 1 for planted pots (orig==0 passes
// through), making the elapsed delta enormous regardless of timebase
// (Unix-epoch seconds, nn::time ticks, etc.). Verified in Ryujinx + Sand
// Kingdom Tostarena: the planted-then-reload cycle blooms the moon on
// stage re-entry (the actor caches its visible level at spawn, so it's
// not mid-frame instant).
//
// Why the rs:: wrapper and not GameDataFile::getGrowFlowerTime directly:
// the GameDataFile method is inlined in 1.0.0 main.nso (no dynsym entry).
// The rs:: namespace wrapper IS in dynsym — cross-checked against
// MrKatzenGaming/BTT-Studio syms/game/System/GameDataUtil.sym which pins
// it at 0x004dd230. BTT-Studio's "Refresh Seeds" toggle returns 0 from the
// same wrapper as a deliberate RESET (clears the pot's planted state +
// the seed item on save). We want the opposite — keep the planted bit
// set while lying about *when* it was planted — so we substitute 1 for
// real timestamps and pass orig==0 through untouched.
inline constexpr const char* kRsGetGrowFlowerTime =
    "_ZN2rs17getGrowFlowerTimeEPKN2al9LiveActorEPKNS0_11PlacementIdE";

// =============================================================================
// Legacy / aliasing — kept so existing call sites don't break.
// =============================================================================
inline constexpr const char* kSeadGameSystemCtor       = kGameSystemInit;
inline constexpr const char* kShineGetSetter           = kGameDataFileSetGotShine;
inline constexpr const char* kScenarioNoSetter         = kGameDataFileSetMainScenarioNo;
inline constexpr const char* kSaveDataLoad             = kGameDataFileInitializeData;

}  // namespace smoap::sym
