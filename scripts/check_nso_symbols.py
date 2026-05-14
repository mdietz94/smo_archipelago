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
    "_ZNK16HakoniwaSequence8drawMainEv",
    "_ZN10GameSystem4initEv",
    "_ZN2al5Scene7endInitERKNS_13ActorInitInfoE",
    "_ZN12GameDataFile11setGotShineEPK9ShineInfo",
    "_ZN16PlayerHackKeeper9startHackEPN2al9HitSensorES2_PNS0_9LiveActorE",
    "_ZN12GameDataFile17setMainScenarioNoEi",
    "_ZN12GameDataFile14initializeDataEv",
    "_ZN16DemoPeachWedding14makeActorAliveEv",
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
