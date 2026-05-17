"""Generate shine_map.json + capture_map.json from a SMO 1.0.0 dump.

ONE COMMAND from a fresh checkout (Nintendo IP, so each user runs locally):

    python scripts/extract_shine_map.py --nsp <SMO_1.0.0.nsp>

On first run the script:
  1. Bootstraps a side venv at scripts/.extract-venv (Python 3.12 + oead).
  2. Runs hactool to extract the NSP -> RomFS (~5 GB, into a gitignored cache).
  3. Walks SystemData/ShineInfo.szs + LocalizedData/USen/MessageData/StageMessage.szs
     (moon names) AND SystemData/HackObjList.szs + .../SystemMessage/HackList.msbt
     (capture names).
  4. Writes shine_map.json + capture_map.json + their *_review.json companions
     (all gitignored).

Subsequent runs skip 1-2 if the venv and RomFS cache already exist (~5 s total).

Prereqs the user must have:
  - Python 3.12 on the `py` launcher (`winget install -e --id Python.Python.3.12`)
  - hactool on PATH or at C:/Users/maxwe/Desktop/Switch/hactool.exe
  - prod.keys at C:/Users/maxwe/.switch/prod.keys (override with --keys)
  - SMO 1.0.0 NSP file (override location with --nsp)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / "scripts" / ".extract-venv"
VENV_PY = VENV_DIR / "Scripts" / "python.exe"

DEFAULT_NSP = Path(r"C:\Users\maxwe\Desktop\Switch\SMO_1.0.0.nsp")
DEFAULT_KEYS = Path.home() / ".switch" / "prod.keys"
DEFAULT_HACTOOL_FALLBACK = Path(r"C:\Users\maxwe\Desktop\Switch\hactool.exe")
DEFAULT_ROMFS_CACHE = REPO_ROOT / ".romfs-cache"
DEFAULT_OUT = REPO_ROOT / "bridge" / "smo_ap_bridge" / "data" / "shine_map.json"
DEFAULT_REVIEW = REPO_ROOT / "bridge" / "smo_ap_bridge" / "data" / "shine_map_review.json"
DEFAULT_CAP_OUT = REPO_ROOT / "bridge" / "smo_ap_bridge" / "data" / "capture_map.json"
DEFAULT_CAP_REVIEW = REPO_ROOT / "bridge" / "smo_ap_bridge" / "data" / "capture_map_review.json"
APWORLD_LOCATIONS = REPO_ROOT / "apworld" / "smo_archipelago" / "data" / "locations.json"
APWORLD_ITEMS = REPO_ROOT / "apworld" / "smo_archipelago" / "data" / "items.json"


# -------- self-bootstrap: ensure we're in the 3.12 venv with oead ----------

def _bootstrap_and_reexec() -> None:
    """Create scripts/.extract-venv (Python 3.12 + oead) and re-exec into it.

    Only invoked when `import oead` fails. After re-exec we're inside the venv
    and the second `import oead` at the top of this file succeeds. Idempotent:
    if the venv already exists we skip creation and only re-exec.

    All prints use `flush=True` and pip runs without `--quiet` so the wizard
    that captures our stdout sees real-time progress during the otherwise-
    silent ~30-90s of venv creation + oead install. Without this the wizard's
    log box stays blank until the extraction step proper starts, and users
    reasonably conclude the whole thing has hung.
    """
    if not VENV_PY.exists():
        print(f"[bootstrap] creating Python 3.12 venv at {VENV_DIR}",
              file=sys.stderr, flush=True)
        try:
            subprocess.run(["py", "-3.12", "-m", "venv", str(VENV_DIR)], check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            sys.exit(
                f"ERROR: Python 3.12 not available via `py -3.12` ({e}).\n"
                f"Install:  winget install -e --id Python.Python.3.12"
            )
        print(f"[bootstrap] installing oead in {VENV_DIR} (one-time, ~30-60s)",
              file=sys.stderr, flush=True)
        # No --quiet: pip's "Collecting / Downloading / Installing" lines are
        # the only signal that anything is happening during the install.
        subprocess.run(
            [str(VENV_PY), "-m", "pip", "install", "oead"],
            check=True,
        )
        print(f"[bootstrap] venv ready; re-executing under {VENV_PY}",
              file=sys.stderr, flush=True)
    # Re-exec ourselves in the venv. os.execv on Windows replaces the current
    # process; the next `import oead` will succeed. Preserve `-u` so the
    # post-execv child stays unbuffered for the wizard's log capture.
    os.execv(str(VENV_PY), [str(VENV_PY), "-u", __file__] + sys.argv[1:])


# Eagerly tell the caller we've started — without this the wizard's log box
# can sit empty for several seconds while Python imports the stdlib graph
# above before we even reach the oead check. flush=True so the line isn't
# swallowed by stdout buffering in case the caller forgot -u/PYTHONUNBUFFERED.
print("[extract] starting; checking for oead Python package...",
      file=sys.stderr, flush=True)

try:
    import oead  # type: ignore
except ImportError:
    _bootstrap_and_reexec()
    sys.exit("unreachable")  # pragma: no cover

# UTF-8 stdout for Japanese strings (ScenarioName, ObjectName are JP)
import io  # noqa: E402
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="backslashreplace")

import json  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402


# -------- HomeStage <-> apworld kingdom mapping ---------------------------

# Each ShineList_<HomeStage>.byml inside SystemData/ShineInfo.szs is exactly
# one kingdom. We map on HomeStage (not on per-shine StageName) so that
# sub-stages like PushBlockExStage inherit the right kingdom.
KINGDOM_FOR_HOMESTAGE: dict[str, str] = {
    "CapWorldHomeStage":       "Cap",
    "WaterfallWorldHomeStage": "Cascade",
    "SandWorldHomeStage":      "Sand",
    "LakeWorldHomeStage":      "Lake",
    "ForestWorldHomeStage":    "Wooded",
    "CloudWorldHomeStage":     "Cloud",
    "ClashWorldHomeStage":     "Lost",
    "CityWorldHomeStage":      "Metro",
    "SnowWorldHomeStage":      "Snow",
    "SeaWorldHomeStage":       "Seaside",
    "LavaWorldHomeStage":      "Luncheon",
    "AttackWorldHomeStage":    "Ruined",
    "SkyWorldHomeStage":       "Bowser's",
    "MoonWorldHomeStage":      "Moon",
    "PeachWorldHomeStage":     "Mushroom",
    "Special1WorldHomeStage":  "Dark Side",
    "Special2WorldHomeStage":  "Darker Side",
}


@dataclass
class RawShine:
    stage_name: str
    object_id: str
    unique_id: int
    is_grand: bool
    is_moon_rock: bool
    is_achievement: bool
    home_stage: str  # the HomeStage whose ShineList contained this entry


@dataclass
class ResolvedShine:
    stage_name: str
    object_id: str
    kingdom: str
    shine_id: str
    shine_uid: int


@dataclass
class ReviewReport:
    unmatched_extracted: list[dict] = field(default_factory=list)
    unmatched_apworld: list[str] = field(default_factory=list)
    msbt_misses: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# -------- minimal MSBT (`MsgStdBn`) reader --------------------------------

def parse_msbt(data: bytes) -> dict[str, str]:
    """Return {label: text} for an MSBT v1 (UTF-8 or UTF-16) file.

    Strips Nintendo control sequences (0x0E group/type/datasize+payload and
    0x0F group/type) so what comes out is the plain user-facing text. msyt
    (the standard tool) chokes on SMO's control code 6; this reader skips all
    control codes generically and works fine for moon names which are plain.
    """
    if data[:8] != b"MsgStdBn":
        raise ValueError("not an MSBT (bad magic)")
    bom = data[8:10]
    endian = "little" if bom == b"\xff\xfe" else "big" if bom == b"\xfe\xff" else None
    if endian is None:
        raise ValueError(f"bad MSBT BOM {bom!r}")
    encoding_byte = data[12]
    if encoding_byte not in (0x00, 0x01):
        raise ValueError(f"unsupported MSBT encoding {encoding_byte:#x}")
    section_count = int.from_bytes(data[14:16], endian)

    sections: dict[str, bytes] = {}
    pos = 32
    for _ in range(section_count):
        magic = bytes(data[pos:pos+4]).decode("ascii", errors="replace")
        size = int.from_bytes(data[pos+4:pos+8], endian)
        sections[magic] = bytes(data[pos+16:pos+16+size])
        pos += 16 + size
        pos = (pos + 0x0F) & ~0x0F

    if "LBL1" not in sections or "TXT2" not in sections:
        raise ValueError(f"MSBT missing LBL1/TXT2 (have {list(sections)})")

    lbl = sections["LBL1"]
    slot_count = int.from_bytes(lbl[0:4], endian)
    labels: dict[str, int] = {}
    for s in range(slot_count):
        slot_off = 4 + s * 8
        n_in_slot = int.from_bytes(lbl[slot_off:slot_off+4], endian)
        cursor = int.from_bytes(lbl[slot_off+4:slot_off+8], endian)
        for _ in range(n_in_slot):
            ll = lbl[cursor]
            label = lbl[cursor+1:cursor+1+ll].decode("ascii")
            idx = int.from_bytes(lbl[cursor+1+ll:cursor+1+ll+4], endian)
            labels[label] = idx
            cursor += 1 + ll + 4

    txt = sections["TXT2"]
    msg_count = int.from_bytes(txt[0:4], endian)
    offsets = [int.from_bytes(txt[4 + i*4 : 8 + i*4], endian) for i in range(msg_count)]
    offsets.append(len(txt))
    messages = [_decode_msbt_text(txt[offsets[i]:offsets[i+1]], encoding_byte, endian)
                for i in range(msg_count)]

    return {label: messages[idx] for label, idx in labels.items() if 0 <= idx < len(messages)}


def _decode_msbt_text(raw: bytes, encoding_byte: int, endian: str) -> str:
    """Strip MSBT control codes and return the plain string."""
    if encoding_byte == 0x01:  # UTF-16
        unit = 2
        codec = "utf-16-le" if endian == "little" else "utf-16-be"
        ctrl_open  = (b"\x0E\x00" if endian == "little" else b"\x00\x0E")
        ctrl_close = (b"\x0F\x00" if endian == "little" else b"\x00\x0F")
    else:  # UTF-8
        unit = 1
        codec = "utf-8"
        ctrl_open  = b"\x0E"
        ctrl_close = b"\x0F"

    out = bytearray()
    i = 0
    n = len(raw)
    while i + unit <= n:
        chunk = raw[i:i+unit]
        if chunk == ctrl_open:
            # 0x0E + group + type + datasize + datasize bytes
            if encoding_byte == 0x01:
                if i + 8 > n: break
                datasize = int.from_bytes(raw[i+6:i+8], endian)
                i += 8 + datasize
            else:
                if i + 4 > n: break
                datasize = raw[i+3]
                i += 4 + datasize
            continue
        if chunk == ctrl_close:
            if encoding_byte == 0x01:
                if i + 6 > n: break
                i += 6
            else:
                if i + 3 > n: break
                i += 3
            continue
        out.extend(chunk)
        i += unit
    return bytes(out).decode(codec, errors="replace").rstrip("\x00").rstrip()


# -------- hactool: NSP -> RomFS -------------------------------------------

def resolve_hactool(arg: Path | None) -> Path:
    if arg is not None:
        if arg.exists():
            return arg
        sys.exit(f"ERROR: --hactool path {arg} does not exist")
    on_path = shutil.which("hactool") or shutil.which("hactool.exe")
    if on_path:
        return Path(on_path)
    if DEFAULT_HACTOOL_FALLBACK.exists():
        return DEFAULT_HACTOOL_FALLBACK
    sys.exit(
        f"ERROR: hactool.exe not found on PATH or at {DEFAULT_HACTOOL_FALLBACK}.\n"
        f"Pass --hactool <path>."
    )


def _run_hactool(hactool: Path, keys: Path, *args: str) -> None:
    cmd = [str(hactool), "--disablekeywarns", "-k", str(keys), *args]
    print(f"[hactool] {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        sys.exit(f"ERROR: hactool exited {proc.returncode}")


def extract_romfs(nsp: Path, keys: Path, hactool: Path, romfs_dir: Path) -> None:
    """Extract NSP -> PFS0 -> program NCA -> RomFS at `romfs_dir`. Idempotent."""
    if (romfs_dir / "SystemData" / "ShineInfo.szs").exists():
        print(f"[romfs] cache hit at {romfs_dir}, skipping extract", file=sys.stderr)
        return
    if not nsp.exists():
        sys.exit(f"ERROR: NSP not found at {nsp}. Pass --nsp <path>.")
    if not keys.exists():
        sys.exit(f"ERROR: prod.keys not found at {keys}. Pass --keys <path>.")
    pfs0_dir = romfs_dir.parent / (romfs_dir.name + ".pfs0")
    pfs0_dir.mkdir(parents=True, exist_ok=True)
    romfs_dir.mkdir(parents=True, exist_ok=True)

    _run_hactool(hactool, keys, "-t", "pfs0", f"--pfs0dir={pfs0_dir}", str(nsp))
    ncas = sorted(pfs0_dir.glob("*.nca"), key=lambda p: p.stat().st_size, reverse=True)
    if not ncas:
        sys.exit(f"ERROR: no .nca produced in {pfs0_dir}")
    program_nca = ncas[0]
    print(f"[romfs] program NCA: {program_nca.name} ({program_nca.stat().st_size/(1<<30):.2f} GB)",
          file=sys.stderr)
    _run_hactool(hactool, keys, "-t", "nca", f"--romfsdir={romfs_dir}", str(program_nca))

    shutil.rmtree(pfs0_dir, ignore_errors=True)


# -------- BYML walking + MSBT join ----------------------------------------

def walk_shine_lists(romfs: Path) -> list[RawShine]:
    sarc = oead.Sarc(oead.yaz0.decompress(
        (romfs / "SystemData" / "ShineInfo.szs").read_bytes()))
    files = {f.name: bytes(f.data) for f in sarc.get_files()}

    out: list[RawShine] = []
    for home in KINGDOM_FOR_HOMESTAGE:
        key = f"ShineList_{home}.byml"
        if key not in files:
            print(f"WARN: {key} not in ShineInfo.szs", file=sys.stderr)
            continue
        doc = oead.byml.from_binary(files[key])
        shines = doc["ShineList"] if "ShineList" in doc else doc
        for s in shines:
            try:
                stage = str(s["StageName"])
                obj   = str(s["ObjId"])
                uid   = int(s["UniqueId"])
            except (KeyError, ValueError):
                continue
            out.append(RawShine(
                stage_name=stage,
                object_id=obj,
                unique_id=uid,
                is_grand=bool(s["IsGrand"]) if "IsGrand" in s else False,
                is_moon_rock=bool(s["IsMoonRock"]) if "IsMoonRock" in s else False,
                is_achievement=bool(s["IsAchievement"]) if "IsAchievement" in s else False,
                home_stage=home,
            ))
    return out


def load_all_stage_msbts(romfs: Path) -> dict[str, dict[str, str]]:
    """Return {stage_name: {label: text}} for every <StageName>.msbt."""
    sarc = oead.Sarc(oead.yaz0.decompress(
        (romfs / "LocalizedData" / "USen" / "MessageData" / "StageMessage.szs").read_bytes()))
    out: dict[str, dict[str, str]] = {}
    for f in sarc.get_files():
        if not f.name.endswith(".msbt"):
            continue
        try:
            out[f.name[:-5]] = parse_msbt(bytes(f.data))
        except Exception as e:
            print(f"WARN: parse_msbt({f.name}) failed: {e}", file=sys.stderr)
            out[f.name[:-5]] = {}
    return out


def load_apworld_moon_names() -> set[str]:
    entries = json.loads(APWORLD_LOCATIONS.read_text(encoding="utf-8"))
    prefixes = tuple(f"{k}: " for k in KINGDOM_FOR_HOMESTAGE.values())
    return {e["name"] for e in entries
            if isinstance(e.get("name"), str) and e["name"].startswith(prefixes)}


def load_apworld_capture_names() -> set[str]:
    items = json.loads(APWORLD_ITEMS.read_text(encoding="utf-8"))
    return {it["name"] for it in items if "Capture" in (it.get("category") or [])}


# -------- captures: HackObjList.byml + HackList.msbt ----------------------

# Nintendo MSBT name -> apworld capture name. Used when the apworld
# deliberately diverged from Nintendo's English string — either by collapsing
# multiple Nintendo variants into one randomizable item (Picture Match Part,
# Puzzle Part) or by renaming for clarity (Snow Cheep Cheep). Without these
# the cross-validator reports false-positive misses.
CAPTURE_NAME_ALIASES: dict[str, str] = {
    # Nintendo lowercases this in HackList.msbt (apparent pause-menu
    # sub-label convention); apworld uses title case for AP chat readability.
    "Bowser statue":                "Bowser Statue",
    # apworld preferred prefix form over Nintendo's parenthetical
    "Cheep Cheep (Snow Kingdom)":   "Snow Cheep Cheep",
    # apworld collapses Nintendo's per-piece variants into one randomizable
    "Picture Match Part (Mario)":   "Picture Match Part",
    "Picture Match Part (Goomba)":  "Picture Match Part",
    # apworld collapses Nintendo's per-kingdom variants into one randomizable
    "Puzzle Part (Lake Kingdom)":   "Puzzle Part",
    "Puzzle Part (Metro Kingdom)":  "Puzzle Part",
}

def walk_hack_obj_list(romfs: Path) -> list[str]:
    """Return every `HackName` string in SystemData/HackObjList.byml."""
    sarc = oead.Sarc(oead.yaz0.decompress(
        (romfs / "SystemData" / "HackObjList.szs").read_bytes()))
    files = list(sarc.get_files())
    if not files:
        return []
    doc = oead.byml.from_binary(bytes(files[0].data))
    out: list[str] = []
    for entry in doc:
        if "HackName" in entry:
            out.append(str(entry["HackName"]))
    return out


def load_hack_msbt(romfs: Path) -> dict[str, str]:
    """Return {internal_hack_name: english_display_name} from HackList.msbt."""
    sarc = oead.Sarc(oead.yaz0.decompress(
        (romfs / "LocalizedData" / "USen" / "MessageData" / "SystemMessage.szs").read_bytes()))
    for f in sarc.get_files():
        if f.name == "HackList.msbt":
            return parse_msbt(bytes(f.data))
    return {}


def extract_captures(romfs: Path) -> tuple[list[dict], dict]:
    """Build {hack_name -> apworld cap name} entries + a review report.

    Pass-through model: bridge defaults to identity for unmapped hack names.
    We only emit entries whose internal -> English mapping actually differs
    OR whose English form appears in the apworld (so the bridge has a
    canonical match for any hack the AP server might ship as an item).
    """
    hack_names = walk_hack_obj_list(romfs)
    hack_msbt = load_hack_msbt(romfs)
    apworld_caps = load_apworld_capture_names()

    review = {
        "stats": {},
        "no_msbt": [],
        "msbt_match_apworld": 0,
        "msbt_no_apworld": [],
        "apworld_unhit": [],
    }
    seen_hack: set[str] = set()
    seen_apworld: set[str] = set()
    out: list[dict] = []

    for hack in hack_names:
        if hack in seen_hack:
            continue
        seen_hack.add(hack)
        english = hack_msbt.get(hack)
        if english is None:
            review["no_msbt"].append(hack)
            continue
        # Apply apworld alias normalization (Cheep Cheep (Snow Kingdom) -> Snow Cheep Cheep, etc.)
        cap_name = CAPTURE_NAME_ALIASES.get(english, english)
        if cap_name in apworld_caps:
            seen_apworld.add(cap_name)
            review["msbt_match_apworld"] += 1
        else:
            review["msbt_no_apworld"].append({"hack_name": hack, "english": english})
        # Always emit -- bridge identity-fallbacks handle the unmapped case
        # but a committed mapping makes resolution explicit.
        out.append({"hack_name": hack, "cap": cap_name})

    review["apworld_unhit"] = sorted(apworld_caps - seen_apworld)
    review["stats"] = {
        "raw_hacks": len(hack_names),
        "unique_hacks": len(seen_hack),
        "emitted": len(out),
        "no_msbt": len(review["no_msbt"]),
        "apworld_caps": len(apworld_caps),
        "apworld_matched": review["msbt_match_apworld"],
        "apworld_unhit": len(review["apworld_unhit"]),
        "out_of_apworld_scope": len(review["msbt_no_apworld"]),
    }
    return out, review


# -------- main pipeline ---------------------------------------------------

def extract(romfs: Path) -> tuple[list[ResolvedShine], ReviewReport]:
    raw = walk_shine_lists(romfs)
    msbts = load_all_stage_msbts(romfs)
    apworld_names = load_apworld_moon_names()

    review = ReviewReport()
    resolved: list[ResolvedShine] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_names: set[str] = set()

    for r in raw:
        kingdom = KINGDOM_FOR_HOMESTAGE.get(r.home_stage)
        if kingdom is None:
            review.unmatched_extracted.append({
                "reason": "unknown home_stage",
                "stage_name": r.stage_name, "object_id": r.object_id,
                "home_stage": r.home_stage, "unique_id": r.unique_id,
            })
            continue
        text = msbts.get(r.stage_name, {}).get(f"ScenarioName_{r.object_id}")
        if not text:
            review.msbt_misses.append({
                "stage_name": r.stage_name, "object_id": r.object_id,
                "home_stage": r.home_stage, "unique_id": r.unique_id,
            })
            continue
        candidate = f"{kingdom}: {text}"
        in_apworld = candidate in apworld_names
        if not in_apworld:
            review.unmatched_extracted.append({
                "reason": "name not in apworld",
                "stage_name": r.stage_name, "object_id": r.object_id,
                "home_stage": r.home_stage, "unique_id": r.unique_id,
                "kingdom": kingdom, "msbt_text": text,
                "candidate_name": candidate,
            })
            # Still emit -- bridge handles unknowns gracefully and future
            # apworld expansion would pick them up without re-extraction.
        if (r.stage_name, r.object_id) in seen_keys:
            review.unmatched_extracted.append({
                "reason": "duplicate (stage, object_id) key",
                "stage_name": r.stage_name, "object_id": r.object_id,
                "home_stage": r.home_stage, "unique_id": r.unique_id,
                "kingdom": kingdom, "msbt_text": text,
            })
            continue
        seen_keys.add((r.stage_name, r.object_id))
        seen_names.add(candidate)
        resolved.append(ResolvedShine(
            stage_name=r.stage_name, object_id=r.object_id,
            kingdom=kingdom, shine_id=text, shine_uid=r.unique_id,
        ))

    review.unmatched_apworld = sorted(apworld_names - seen_names)
    review.stats = {
        "raw_shines": len(raw),
        "resolved": len(resolved),
        "msbt_misses": len(review.msbt_misses),
        "name_mismatches": sum(1 for u in review.unmatched_extracted if u.get("reason") == "name not in apworld"),
        "unknown_home_stage": sum(1 for u in review.unmatched_extracted if u.get("reason") == "unknown home_stage"),
        "duplicate_keys": sum(1 for u in review.unmatched_extracted if u.get("reason") == "duplicate (stage, object_id) key"),
        "apworld_moon_names": len(apworld_names),
        "apworld_unhit": len(review.unmatched_apworld),
    }
    return resolved, review


def write_outputs(resolved: list[ResolvedShine], review: ReviewReport,
                  out: Path, review_path: Path) -> None:
    out_data = [
        {"stage_name": r.stage_name, "object_id": r.object_id,
         "kingdom": r.kingdom, "shine_id": r.shine_id, "shine_uid": r.shine_uid}
        for r in resolved
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    review_data = {
        "stats": review.stats,
        "unmatched_extracted": review.unmatched_extracted,
        "msbt_misses": review.msbt_misses,
        "unmatched_apworld": review.unmatched_apworld,
    }
    review_path.write_text(json.dumps(review_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__.split("\n\n", 1)[0],
        epilog=__doc__.split("\n\n", 1)[1] if "\n\n" in __doc__ else "",
    )
    ap.add_argument("--nsp", type=Path, default=DEFAULT_NSP,
                    help=f"SMO 1.0.0 NSP (default: {DEFAULT_NSP})")
    ap.add_argument("--keys", type=Path, default=DEFAULT_KEYS,
                    help=f"prod.keys (default: {DEFAULT_KEYS})")
    ap.add_argument("--hactool", type=Path, default=None,
                    help=f"hactool path (default: PATH, then {DEFAULT_HACTOOL_FALLBACK})")
    ap.add_argument("--romfs-cache", type=Path, default=DEFAULT_ROMFS_CACHE,
                    help=f"romfs extract dir (default: {DEFAULT_ROMFS_CACHE}, gitignored)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"output shine_map.json (default: {DEFAULT_OUT})")
    ap.add_argument("--review", type=Path, default=DEFAULT_REVIEW,
                    help=f"output mismatch report (default: {DEFAULT_REVIEW})")
    ap.add_argument("--cap-out", type=Path, default=DEFAULT_CAP_OUT,
                    help=f"output capture_map.json (default: {DEFAULT_CAP_OUT})")
    ap.add_argument("--cap-review", type=Path, default=DEFAULT_CAP_REVIEW,
                    help=f"output capture review (default: {DEFAULT_CAP_REVIEW})")
    ap.add_argument("--romfs", type=Path, default=None,
                    help="skip NSP extract; use pre-extracted RomFS directory")
    args = ap.parse_args(argv)

    if not APWORLD_LOCATIONS.exists():
        return _fail(f"apworld locations.json not found at {APWORLD_LOCATIONS}")

    if args.romfs is not None:
        romfs = args.romfs
        if not (romfs / "SystemData" / "ShineInfo.szs").exists():
            return _fail(f"{romfs} is not a romfs (no SystemData/ShineInfo.szs)")
    else:
        hactool = resolve_hactool(args.hactool)
        extract_romfs(args.nsp, args.keys, hactool, args.romfs_cache)
        romfs = args.romfs_cache

    resolved, review = extract(romfs)
    write_outputs(resolved, review, args.out, args.review)

    s = review.stats
    print(f"== moons ==")
    print(f"raw shines:           {s['raw_shines']}")
    print(f"resolved entries:     {s['resolved']}  -> {args.out}")
    print(f"  msbt misses:        {s['msbt_misses']}")
    print(f"  unknown home_stage: {s['unknown_home_stage']}")
    print(f"  duplicate keys:     {s['duplicate_keys']}")
    print(f"apworld moons:        {s['apworld_moon_names']}")
    print(f"  name mismatches:    {s['name_mismatches']} (out-of-apworld-scope; still emitted)")
    print(f"  apworld unhit:      {s['apworld_unhit']}")
    print(f"review report:        {args.review}")

    cap_entries, cap_review = extract_captures(romfs)
    args.cap_out.parent.mkdir(parents=True, exist_ok=True)
    args.cap_out.write_text(json.dumps(cap_entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.cap_review.write_text(json.dumps(cap_review, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    cs = cap_review["stats"]
    print()
    print(f"== captures ==")
    print(f"raw HackObjList:      {cs['raw_hacks']}")
    print(f"emitted entries:      {cs['emitted']}  -> {args.cap_out}")
    print(f"  no MSBT match:      {cs['no_msbt']}")
    print(f"apworld captures:     {cs['apworld_caps']}")
    print(f"  apworld matched:    {cs['apworld_matched']}")
    print(f"  apworld unhit:      {cs['apworld_unhit']}")
    print(f"  out-of-scope hacks: {cs['out_of_apworld_scope']} (still emitted)")
    print(f"review report:        {args.cap_review}")
    return 0


def _fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
