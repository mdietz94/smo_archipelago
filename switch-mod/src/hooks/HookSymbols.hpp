// Mangled symbol catalog for hooks.
//
// All InstallAtSymbol() calls pull their mangled name from here so version
// bumps are isolated to this single file. exlaunch resolves these via
// nn::ro::LookupSymbol() at module load.
//
// Provenance:
//   - 3 of 8 symbols (drawMain, GameSystem::init, Scene::endInit) are byte-
//     identical to the names lunakit hooks in src/program/main.cpp on SMO
//     1.0.0. Verified working.
//   - The other 5 (moon setter, capture startHack, scenario setter, save
//     init, wedding demo) were computed from MonsterDruide1/OdysseyDecomp
//     forward-declarations passed through aarch64-none-elf-g++. Itanium ABI
//     mangling is deterministic from the signature alone, so these names
//     are 1.0.0-correct as long as the decomp signatures match the runtime
//     symbol — verify by nm against the combined cart+SMO-Downgrade-overlay
//     binary before depending on them in M4+.

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

// --- Capture acquired (gates + check) ---
// PlayerHackKeeper::startHack(al::HitSensor*, al::HitSensor*, al::LiveActor*)
// Source: MonsterDruide1/OdysseyDecomp src/Player/PlayerHackKeeper.h:47
// HOOK MODE: TRAMPOLINE in M4 (read-only check), REPLACE-with-conditional in M7
// (refuse if cap not unlocked). Third arg is the LiveActor we're hacking —
// extract its name via the actor's hack-data table to identify the cap-type.
inline constexpr const char* kPlayerHackKeeperStartHack =
    "_ZN16PlayerHackKeeper9startHackEPN2al9HitSensorES2_PNS0_9LiveActorE";

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

// --- Goal trigger (Bowser-defeat wedding cutscene fires) ---
// DemoPeachWedding::makeActorAlive()  (override of al::LiveActor)
// Source: MonsterDruide1/OdysseyDecomp src/Demo/DemoPeachWedding.h:8
// This is the precise moment the wedding ending demo activates. Idempotent
// guard via ApState::goal_sent so a "watch credits twice" scenario doesn't
// re-fire.
inline constexpr const char* kDemoPeachWeddingMakeActorAlive =
    "_ZN16DemoPeachWedding14makeActorAliveEv";

// =============================================================================
// Legacy / aliasing — kept so existing call sites don't break.
// =============================================================================
inline constexpr const char* kSeadGameSystemCtor       = kGameSystemInit;
inline constexpr const char* kShineGetSetter           = kGameDataFileSetGotShine;
inline constexpr const char* kScenarioNoSetter         = kGameDataFileSetMainScenarioNo;
inline constexpr const char* kSaveDataLoad             = kGameDataFileInitializeData;
inline constexpr const char* kEndingDemoStart          = kDemoPeachWeddingMakeActorAlive;

}  // namespace smoap::sym
