#!/usr/bin/env python3
"""Verify mangled hook symbols resolve in a SMO main.nso.

Decompresses NSO segments (LZ4 block) and searches the dynstr table for the
8 mangled names our switch-mod hooks against. Usage:
    python check_nso_symbols.py <path-to-main.nso>

Run only locally — main.nso is copyrighted game code, not checked in.
"""
import struct
import sys
from pathlib import Path

import lz4.block

SYMBOLS = [
    # M0-M4 hooks (verified 2026-05-15).
    "_ZNK16HakoniwaSequence8drawMainEv",
    "_ZN10GameSystem4initEv",
    "_ZN2al5Scene7endInitERKNS_13ActorInitInfoE",
    "_ZN12GameDataFile11setGotShineEPK9ShineInfo",
    "_ZN16PlayerHackKeeper9startHackEPN2al9HitSensorES2_PNS0_9LiveActorE",
    # M7: capture lock deny path — forceKillHack runs after the capture-entry
    # cinematic ends (gated on isActiveHackStartDemo) when AP hasn't unlocked
    # the capture. tryEscapeHack is the gentler release used for the 7
    # inanimate captures (Cactus, BazookaElectric, Tree, RockForest,
    # Guidepost, Manhole, HackFork) that have no intro state machine.
    # (cancelHack was tried first but proved to be a no-op when called from
    # within the startHack callback — see HookSymbols.hpp for the rationale.)
    "_ZN16PlayerHackKeeper13forceKillHackEv",
    "_ZN16PlayerHackKeeper13tryEscapeHackEv",
    "_ZNK16PlayerHackKeeper21isActiveHackStartDemoEv",
    "_ZN12GameDataFile17setMainScenarioNoEi",
    "_ZN12GameDataFile14initializeDataEv",
    # M6: shine-counter hooks (HUD substitution for AP credit display).
    "_ZN16GameDataFunction18getCurrentShineNumE22GameDataHolderAccessor",
    "_ZN16GameDataFunction14getGotShineNumE22GameDataHolderAccessori",
    # M6: capture grant + idempotency.
    "_ZN16GameDataFunction17addHackDictionaryE20GameDataHolderWriterPKc",
    "_ZN16GameDataFunction23isExistInHackDictionaryE22GameDataHolderAccessorPKc",
    # M6: snapshot enumerate support.
    "_ZN16GameDataFunction10isGotShineE22GameDataHolderAccessorPK9ShineInfo",
    "_ZN16GameDataFunction15getGameDataFileE20GameDataHolderWriter",
    # M6 phase C — snapshot enumeration uses GameDataFile::isGotShine(int)
    # directly. mShineHintList is walked and each HintInfo's mUniqueID is
    # passed straight to this overload. See switch-mod/src/game/MoonApply.cpp.
    "_ZNK12GameDataFile10isGotShineEi",
    # M6 phase A.5 — moon-get cutscene label substitution (Channel A).
    # All four verified against SMO 1.0.0 main.nso 2026-05-16.
    "_ZN23StageSceneStateGetShine10exeDemoGetEv",
    "_ZN27StageSceneStateGetShineMain15exeDemoGetStartEv",
    "_ZN28StageSceneStateGetShineGrand15exeDemoGetStartEv",
    "_ZN2al19setPaneStringFormatEPNS_10IUseLayoutEPKcS3_z",
    # Cappy Messenger: hook the 4 layout-used MSBT lookup pairs so our
    # reserved label resolves to our own UTF-16 buffer; call rs:: Cap-message
    # entry points to actually trigger the speech-bubble dispatch. See
    # switch-mod/src/ui/CappyMessenger.cpp and CappyMessageHook.cpp.
    "_ZN2al27isExistLabelInSystemMessageEPKNS_17IUseMessageSystemEPKcS4_",
    "_ZN2al22getSystemMessageStringEPKNS_17IUseMessageSystemEPKcS4_",
    "_ZN2al26isExistLabelInStageMessageEPKNS_17IUseMessageSystemEPKcS4_",
    "_ZN2al21getStageMessageStringEPKNS_17IUseMessageSystemEPKcS4_",
    "_ZN2rs28tryShowCapMessagePriorityLowEPKN2al18IUseSceneObjHolderEPKcii",
    "_ZN2rs18isActiveCapMessageEPKN2al18IUseSceneObjHolderE",
    # M-color (2026-05-20 rewrite): Shine::init trampoline + material-
    # parameter override via al::set*MaterialParameter*. al::isExistModel
    # is the null-safe model-keeper presence probe required before any
    # other model-touching call (the rest of these crash on actors whose
    # mModelKeeper is null — see Cascade scenario-reload crash 2026-05-20).
    "_ZN5Shine4initERKN2al13ActorInitInfoE",
    "_ZN2al23setMaterialProgrammableEPNS_9LiveActorE",
    "_ZN2al29setModelMaterialParameterRgbaEPKNS_9LiveActorEPKcS4_RKN4sead7Color4fE",
    "_ZN2al28setModelMaterialParameterF32EPKNS_9LiveActorEPKcS4_f",
    "_ZN2al15isExistMaterialEPKNS_9LiveActorEPKc",
    "_ZN2al12isExistModelEPKNS_9LiveActorE",
    # M6 phase D — moon-deposit debit (AP credit decremented on Odyssey toss).
    # Hook the GameDataFunction wrappers (not the inlined GameDataFile
    # members) — Phase 0 confirmed GameDataFile::addPayShine(s32) is fully
    # inlined into callers and not present in dynsym, but the public
    # GameDataFunction::addPayShine(GameDataHolderWriter, int) IS exposed
    # (same pattern as addHackDictionary). All game-side payment paths
    # (Odyssey-fueling, NPC payment, scripted scenario consumption) go
    # through the GameDataFunction layer per OdysseyDecomp.
    "_ZN16GameDataFunction11addPayShineE20GameDataHolderWriteri",
    "_ZN16GameDataFunction21addPayShineCurrentAllE20GameDataHolderWriter",
    "_ZN16GameDataFunction26getCurrentWorldIdNoDevelopE22GameDataHolderAccessor",
    # M6 phase D successor — derived outstanding (deposit-then-crash fix).
    # PaySnapshotMsg ships per-kingdom PayShineNum to the bridge, which
    # derives outstanding = lifetime_received - PayShineNum. Same Itanium
    # mangling pattern as getGotShineNum above: (Accessor, s32 worldId).
    "_ZN16GameDataFunction14getPayShineNumE22GameDataHolderAccessori",
    # M7 Path A — fork-cinematic kingdom-order gate (two-layer architecture;
    # see switch-mod/src/hooks/WorldMapSelectHook.cpp).
    # Layer 1: post-Multi-Moon FORK cinematic per-slot query (also fires on
    # the regular leave-kingdom map; released by visited bit + current-kingdom
    # OR-check in the gate, not by hook layering).
    "_ZN16GameDataFunction32calcNextLockedWorldIdForWorldMapEPKN2al11LayoutActorEi",
    "_ZN16GameDataFunction32calcNextLockedWorldIdForWorldMapEPKN2al5SceneEi",
    # Layer 2: cinematic stage-commit BACKSTOP (substitutes + sets visited).
    "_ZN16GameDataFunction35tryChangeNextStageWithDemoWorldWarpE20GameDataHolderWriterPKc",
    # Regular-map stage-commit (visited-only; NOT used for substitution).
    "_ZN16GameDataFunction35tryChangeNextStageWithWorldWarpHoleE20GameDataHolderWriterPKc",
]


def parse_and_decompress(path: Path) -> dict[str, bytes]:
    """Return {'text': bytes, 'rodata': bytes, 'data': bytes} fully decompressed,
    plus 'dynstr' (slice of rodata)."""
    raw = path.read_bytes()
    assert raw[:4] == b"NSO0", f"not an NSO: {raw[:4]!r}"

    # Header layout (offsets in bytes within the 256-byte header).
    flags = struct.unpack_from("<I", raw, 0xC)[0]
    text_file_off, _text_mem_off, text_decomp_size = struct.unpack_from("<III", raw, 0x10)
    rod_file_off, _rod_mem_off, rod_decomp_size = struct.unpack_from("<III", raw, 0x20)
    data_file_off, _data_mem_off, data_decomp_size = struct.unpack_from("<III", raw, 0x30)
    text_comp_size, rod_comp_size, data_comp_size = struct.unpack_from("<III", raw, 0x60)
    dynstr_off, dynstr_size = struct.unpack_from("<II", raw, 0x90)
    dynsym_off, dynsym_size = struct.unpack_from("<II", raw, 0x98)

    def decomp(file_off: int, comp_size: int, decomp_size: int, compressed: bool) -> bytes:
        blob = raw[file_off : file_off + comp_size]
        if not compressed:
            return blob
        return lz4.block.decompress(blob, uncompressed_size=decomp_size)

    text = decomp(text_file_off, text_comp_size, text_decomp_size, bool(flags & 0x1))
    rodata = decomp(rod_file_off, rod_comp_size, rod_decomp_size, bool(flags & 0x2))
    data = decomp(data_file_off, data_comp_size, data_decomp_size, bool(flags & 0x4))

    dynstr = rodata[dynstr_off : dynstr_off + dynstr_size]
    return {
        "text": text,
        "rodata": rodata,
        "data": data,
        "dynstr": dynstr,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <main.nso>", file=sys.stderr)
        return 2
    nso = Path(argv[1])
    if not nso.exists():
        print(f"not found: {nso}", file=sys.stderr)
        return 2

    segments = parse_and_decompress(nso)
    print(f"NSO {nso.name}: text={len(segments['text'])} rodata={len(segments['rodata'])} "
          f"data={len(segments['data'])} dynstr={len(segments['dynstr'])}")
    print()

    dynstr = segments["dynstr"]
    misses = []
    for sym in SYMBOLS:
        needle = sym.encode("ascii") + b"\x00"
        if needle in dynstr:
            print(f"  HIT   {sym}")
        else:
            misses.append(sym)
            print(f"  MISS  {sym}")

    if misses:
        print()
        print(f"{len(misses)}/{len(SYMBOLS)} symbols missing from dynstr.", file=sys.stderr)
        return 1
    print()
    print(f"All {len(SYMBOLS)} symbols resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
