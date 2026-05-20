"""Generate shine_map.json + capture_map.json from a SMO 1.0.0 dump.

ONE COMMAND from a fresh checkout (Nintendo IP, so each user runs locally):

    python scripts/extract_shine_map.py --nsp <SMO_1.0.0.nsp>
    python scripts/extract_shine_map.py --xci <SMO_1.0.0.xci>

On first run the script:
  1. Bootstraps a side venv at scripts/.extract-venv (Python 3.12 + oead).
  2. Runs hactool to extract the dump -> RomFS (~5 GB, into a gitignored cache).
     NSP -> PFS0 partition; XCI -> HFS0 secure partition. Same NCA layout
     afterwards.
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
  - SMO 1.0.0 NSP **or** XCI file (override location with --nsp / --xci)
  - For XCI dumps: title.keys *alongside* prod.keys (i.e. derived from
    --keys's parent dir) with the SMO entry. Override the auto-derived
    path with --titlekey. NSPs ship their own .tik so this isn't needed.
"""
from __future__ import annotations

# Pre-import diagnostic line: proves the script even started executing.
# When the AP frozen Launcher's wizard captures our stdout, "no output for
# 60s" usually means we never got here (path resolution broke, stdout pipe
# closed). Print to stderr (wizard merges stderr->stdout) and flush so the
# line appears even if the parent forgot -u / PYTHONUNBUFFERED.
import sys
print(f"[extract] script invoked: __file__={__file__!r}", file=sys.stderr, flush=True)
print(f"[extract] python={sys.executable!r} argv={sys.argv!r}", file=sys.stderr, flush=True)

import argparse
import shutil
import subprocess
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
    """Create scripts/.extract-venv (Python 3.12 + oead) and re-launch into it.

    Only invoked when `import oead` fails. After we re-launch the venv's
    Python via subprocess and exit with its returncode, the parent script
    is done; the relaunched child's `import oead` at the top of this file
    succeeds. Idempotent: if the venv already exists we skip creation
    and only re-launch.

    All prints use `flush=True` and pip runs without `--quiet` so the wizard
    that captures our stdout sees real-time progress during the otherwise-
    silent ~30-90s of venv creation + oead install. Without this the wizard's
    log box stays blank until the extraction step proper starts, and users
    reasonably conclude the whole thing has hung.

    Why subprocess.run instead of os.execv: on Windows `os.execv` is NOT a
    true process replacement — it calls Microsoft's `_wspawnv` which (a)
    does NOT quote argv entries containing spaces (so a path like
    `super mario odyssey.nsp` arrives at the relaunched Python as three
    separate argv tokens and argparse fails) and (b) returns control to
    the caller, which then exits with code 0 regardless of the child's
    real exit code (so the wizard sees rc=0 even when the child failed).
    subprocess.run uses `list2cmdline` which properly quotes, and we
    forward its returncode via sys.exit so wrapping callers see the real
    outcome.
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
        print(f"[bootstrap] venv ready; relaunching under {VENV_PY}",
              file=sys.stderr, flush=True)
    proc = subprocess.run(
        [str(VENV_PY), "-u", __file__, *sys.argv[1:]],
    )
    sys.exit(proc.returncode)


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


# -------- title.keys derivation -------------------------------------------
#
# hactool's "[WARN] Unable to match rights id to titlekey. Update title.keys?"
# fires when the user has prod.keys but not title.keys — common, because
# title.keys is per-game and most users never populate it. The NSP's .tik
# file already contains the encrypted titlekey block; we lift those 16 bytes
# straight out of the .tik and pass them via hactool's `--titlekey=` flag,
# which hactool decrypts internally with `titlekek_XX` from prod.keys.
# This skips touching the user's `title.keys`.
#
# IMPORTANT: hactool's `--titlekey=` expects the *encrypted* titlekey
# block, not the plaintext. hactool's NCA dump labels it "Titlekey
# (Encrypted) (From CLI)" and then derives "Titlekey (Decrypted) (From
# CLI)" itself. Earlier this code pre-decrypted with titlekek_XX before
# passing the result to hactool, which then re-decrypted — producing
# garbage and "Error: section N is corrupted!". Do not reintroduce a
# pre-decrypt step here.
#
# Caveat: this only works for COMMON tickets (TitleKeyType=0). Personalized
# tickets (TitleKeyType=1) bind the titlekey to the dumping console's eticket
# RSA key and can't be decrypted from prod.keys — we detect and error
# cleanly in that case so the user knows to re-dump.


def _parse_keys_file(path: Path) -> dict[str, bytes]:
    """Parse a Switch keys file (prod.keys / title.keys / etc.) into {name_lower: bytes}.

    Tolerates comments (# or ;), blank lines, and malformed hex by skipping.
    Returns an empty dict if the file doesn't exist or isn't readable.
    """
    out: dict[str, bytes] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].split("#", 1)[0].strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        try:
            out[k.strip().lower()] = bytes.fromhex(v.strip())
        except ValueError:
            continue
    return out


def _read_ticket(tik_path: Path) -> tuple[bytes, bytes, int, int]:
    """Parse a Switch ticket (signature type RSA-2048-SHA256, 0x2C0 bytes).

    Returns (rights_id, encrypted_titlekey, titlekek_index, titlekey_type).
    Offsets per Switchbrew (https://switchbrew.org/wiki/Ticket) measured
    from the start of the file — the 0x140-byte signature block is included
    in these offsets:
      0x180  TitleKeyBlock (0x100 bytes; first 0x10 = encrypted titlekey)
      0x281  TitleKeyType (0 = common, 1 = personalized)
      0x285  CommonKeyVersion
      0x2A0  RightsId (0x10 bytes; last byte = NCA crypto_type / "master key rev")

    `titlekek_index` is what the caller looks up as `titlekek_XX` in prod.keys.
    NCA crypto_type maps to titlekek index by `max(c, 1) - 1` — values 0 and 1
    both share master key 0 (firmware 1.0.0 - 3.0.0), 2 -> master key 1
    (firmware 3.0.0 - 3.0.1 boundary, sometimes), 3 -> master key 2
    (firmware 3.0.1 - 3.0.2, e.g. SMO 1.0.0). This matches hactool's
    `find_titlekey` and the NCA header's reported "Master Key Revision".
    Belt-and-braces against malformed tickets: take the max of rights_id[15]
    and CommonKeyVersion before applying the off-by-one.
    """
    data = tik_path.read_bytes()
    if len(data) < 0x2B0:
        raise ValueError(f"{tik_path.name} too small ({len(data)} bytes)")
    enc_key = data[0x180:0x190]
    titlekey_type = data[0x281]
    crypto_type = max(data[0x2AF], data[0x285])
    titlekek_index = crypto_type - 1 if crypto_type > 0 else 0
    rights_id = data[0x2A0:0x2B0]
    return rights_id, enc_key, titlekek_index, titlekey_type


def _derive_title_key(tik_path: Path, keys_path: Path) -> tuple[str, str]:
    """Return (rights_id_hex, enc_titlekey_hex) — the encrypted titlekey
    block lifted straight from the .tik, in the form hactool's `--titlekey=`
    expects (it does the titlekek decryption itself).

    We also check that `titlekek_XX` is present in prod.keys so we can fail
    with an actionable message *before* hactool fails with a generic
    "section corrupted" error; the value isn't otherwise used.

    Raises with an actionable message if the ticket is personalized or
    prod.keys is missing the relevant `titlekek_XX`.
    """
    rights_id, enc_key, key_gen, tk_type = _read_ticket(tik_path)
    if tk_type != 0:
        raise RuntimeError(
            f"{tik_path.name} is a personalized ticket (TitleKeyType=0x{tk_type:02x}); "
            "its title key is bound to the dumping console's eticket RSA key and "
            "cannot be decrypted from prod.keys. Re-dump from a clean retail source "
            "(NXDumpTool with the 'common ticket' option, or a known-good NSP)."
        )
    keys = _parse_keys_file(keys_path)
    titlekek_name = f"titlekek_{key_gen:02x}"
    titlekek = keys.get(titlekek_name)
    if titlekek is None:
        raise RuntimeError(
            f"{keys_path} is missing {titlekek_name} (master key revision 0x{key_gen:02x}). "
            "Update prod.keys to a complete current set."
        )
    if len(titlekek) != 16:
        raise RuntimeError(
            f"{titlekek_name} is {len(titlekek)} bytes in {keys_path}, expected 16"
        )
    return rights_id.hex(), enc_key.hex()


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


@dataclass
class _HactoolResult:
    """Outcome of one hactool invocation. Caller decides what's fatal.

    section_corrupt_lines: every `Error: section X is corrupted!` line, in
        order. hactool 1.4.0 prints these when the IVFC superblock hash check
        fails on the decrypted section data — but it does NOT prevent hactool
        from writing the (possibly partially-valid) files to disk. Trimmed
        NSPs and partial downloads commonly produce extractable RomFS dumps
        that fail this check; the files we actually need (~four SZS/MSBT
        files) often survive. `extract_romfs` treats this as a non-fatal
        warning and lets downstream oead parsing be the real integrity check.
    """
    section_corrupt_lines: list[str] = field(default_factory=list)
    returncode: int = 0


def _run_hactool(
    hactool: Path, keys: Path, *args: str, title_keys: Path | None = None,
) -> _HactoolResult:
    # hactool exits 0 even when it failed to decrypt: a missing titlekey
    # produces "[WARN] Unable to match rights id to titlekey. Update
    # title.keys?" followed by "Error: section 0 is corrupted!" lines, but
    # the process still returns 0 and we'd happily continue into a romfs
    # walk that then dies with FileNotFoundError on ShineInfo.szs. Capture
    # output line-by-line, forward it to our stderr (so the wizard log
    # keeps showing the live hactool stream), and treat unknown "Error:"
    # lines or the WARN as a hard failure. `section X is corrupted` is
    # the one Error: line we let through — see `_HactoolResult` and
    # `extract_romfs` for the recovery flow.
    #
    # `title_keys`: if given AND the file exists, pass `--titlekey=` to
    # hactool. Without this, hactool defaults the title-keys lookup to
    # `$HOME/.switch/title.keys` regardless of the `-k` path — surprising
    # for users who keep prod.keys elsewhere and reasonably expect their
    # title.keys to live next to it.
    cmd = [str(hactool), "--disablekeywarns", "-k", str(keys)]
    if title_keys is not None and title_keys.is_file():
        cmd.append(f"--titlekey={title_keys}")
    cmd.extend(args)
    print(f"[hactool] {' '.join(cmd)}", file=sys.stderr, flush=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    titlekey_missing = False
    section_corrupt: list[str] = []
    other_errors: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        print(line, file=sys.stderr, flush=True)
        if "Unable to match rights id to titlekey" in line:
            titlekey_missing = True
        if line.startswith("Error:"):
            if "is corrupted" in line:
                section_corrupt.append(line)
            else:
                other_errors.append(line)
    rc = proc.wait()
    if titlekey_missing:
        expected = title_keys if title_keys is not None else keys.parent / "title.keys"
        sys.exit(
            "ERROR: hactool could not decrypt the dump — your title.keys is\n"
            "missing the entry for SMO's rights ID\n"
            "(01000000000100000000000000000003). NSPs ship their own ticket so\n"
            "this typically only happens with XCI cartridge dumps. Update\n"
            f"title.keys at {expected} (derived from --keys; override with\n"
            "--titlekey) with the Super Mario Odyssey titlekey and rerun\n"
            "the extract."
        )
    if other_errors:
        joined = "\n  ".join(other_errors)
        sys.exit(f"ERROR: hactool reported failures while extracting:\n  {joined}")
    if rc != 0:
        sys.exit(f"ERROR: hactool exited {rc}")
    return _HactoolResult(section_corrupt_lines=section_corrupt, returncode=rc)


def extract_romfs(
    dump: Path, dump_kind: str, keys: Path, hactool: Path, romfs_dir: Path,
    *, title_keys: Path | None = None,
) -> None:
    """Extract NSP/XCI -> program NCA -> RomFS at `romfs_dir`. Idempotent.

    `dump_kind` is "nsp" or "xci". NSPs unpack as a PFS0 partition; XCI
    cartridge images expose an HFS0 "secure" partition. The NCA layout
    after unpacking is the same — pick the largest NCA, lift the
    titlekey from any .tik present (NSPs always include one; XCIs
    almost never do, so we fall back to hactool's title.keys lookup),
    and extract its RomFS.
    """
    if (romfs_dir / "SystemData" / "ShineInfo.szs").exists():
        print(f"[romfs] cache hit at {romfs_dir}, skipping extract", file=sys.stderr)
        return
    if dump_kind not in ("nsp", "xci"):
        sys.exit(f"ERROR: unsupported dump kind {dump_kind!r}; expected nsp or xci")
    if not dump.exists():
        flag = "--xci" if dump_kind == "xci" else "--nsp"
        sys.exit(f"ERROR: {dump_kind.upper()} not found at {dump}. Pass {flag} <path>.")
    if not keys.exists():
        sys.exit(f"ERROR: prod.keys not found at {keys}. Pass --keys <path>.")
    # Common work dir for the unpacked partition contents (NCAs ± a .tik).
    # Suffix encodes the partition type so a leftover from a prior run on
    # the wrong dump kind doesn't get mistakenly reused.
    suffix = ".pfs0" if dump_kind == "nsp" else ".xci-secure"
    work_dir = romfs_dir.parent / (romfs_dir.name + suffix)
    work_dir.mkdir(parents=True, exist_ok=True)
    romfs_dir.mkdir(parents=True, exist_ok=True)

    if dump_kind == "nsp":
        container_result = _run_hactool(
            hactool, keys, "-t", "pfs0", f"--pfs0dir={work_dir}", str(dump),
            title_keys=title_keys)
    else:  # xci
        # `--securedir=` is the "actual game NCAs" partition. The XCI also
        # has a "normal" partition (mostly metadata) and an optional
        # "update" partition (FW deltas); we don't need either.
        container_result = _run_hactool(
            hactool, keys, "-t", "xci", f"--securedir={work_dir}", str(dump),
            title_keys=title_keys)
    if container_result.section_corrupt_lines:
        # Section-corrupt at the container layer is rare but plausible — the
        # PFS0/HFS0 metadata hashes can fail on heavily modified dumps. Don't
        # exit here; the NCA pick below will fail naturally if the work_dir
        # is empty, and we want the corruption diagnostic to come from the
        # NCA→RomFS step where the user can act on it.
        print("[hactool] container reported section corruption; continuing "
              "(NCA extraction will surface the real diagnostic)",
              file=sys.stderr, flush=True)

    # Derive the title key from the .tik so we don't depend on a populated
    # title.keys. We always do this (rather than only as a fallback after a
    # failed hactool run) because hactool exits 0 even when titlekey lookup
    # silently fails — passing --titlekey= explicitly is the only reliable
    # way to know the NCA call will actually decrypt. XCI dumps usually
    # have no .tik in the secure partition and fall through to hactool's
    # title.keys lookup.
    titlekey_args: list[str] = []
    tiks = sorted(work_dir.glob("*.tik"))
    if tiks:
        try:
            rights_id_hex, enc_titlekey_hex = _derive_title_key(tiks[0], keys)
            print(
                f"[titlekey] lifted encrypted title key for rights id {rights_id_hex} "
                f"from {tiks[0].name}; hactool will decrypt with titlekek",
                file=sys.stderr, flush=True,
            )
            titlekey_args = [f"--titlekey={enc_titlekey_hex}"]
        except Exception as e:
            # Don't fail hard yet — fall through to the NCA call and let
            # _run_hactool's WARN/Error detection surface the diagnostic.
            # We still want this line in the log so the user can see why
            # the derivation didn't help.
            print(f"[titlekey] could not derive ({e}); falling back to title.keys",
                  file=sys.stderr, flush=True)
    else:
        # The XCI path almost always lands here — flag it so the user
        # knows where to look if hactool fails with the WARN line.
        hint = (" (XCI dumps don't carry a ticket — populate title.keys "
                "with the SMO entry)" if dump_kind == "xci" else "")
        print(f"[titlekey] no .tik in {work_dir}; falling back to title.keys{hint}",
              file=sys.stderr, flush=True)

    ncas = sorted(work_dir.glob("*.nca"), key=lambda p: p.stat().st_size, reverse=True)
    if not ncas:
        sys.exit(f"ERROR: no .nca produced in {work_dir}")
    program_nca = ncas[0]
    print(f"[romfs] program NCA: {program_nca.name} ({program_nca.stat().st_size/(1<<30):.2f} GB)",
          file=sys.stderr)
    romfs_result = _run_hactool(
        hactool, keys, "-t", "nca", f"--romfsdir={romfs_dir}",
        *titlekey_args, str(program_nca), title_keys=title_keys)
    if romfs_result.section_corrupt_lines:
        # hactool's IVFC check is over every 16KB block of the RomFS — a
        # single bad block flips this even though hactool already wrote the
        # extracted files to disk. We need only ~4 small files; if those
        # extracted intact, the broader corruption is irrelevant. Defer the
        # verdict to oead's parsing in main()'s try-block (which catches the
        # exception and surfaces the actionable "re-dump" message).
        joined = "\n  ".join(romfs_result.section_corrupt_lines)
        print(
            f"[romfs] hactool flagged section corruption:\n  {joined}\n"
            "[romfs] this is a strict whole-section hash check; we only need\n"
            "[romfs] ~4 SZS/MSBT files. Continuing — oead parsing will be the\n"
            "[romfs] real integrity check.",
            file=sys.stderr, flush=True,
        )

    shutil.rmtree(work_dir, ignore_errors=True)


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
    # `global` must precede any use of the name; the --locations / --items
    # defaults below reference these module-level constants, so the
    # declaration has to come first.
    global APWORLD_LOCATIONS, APWORLD_ITEMS

    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__.split("\n\n", 1)[0],
        epilog=__doc__.split("\n\n", 1)[1] if "\n\n" in __doc__ else "",
    )
    ap.add_argument("--nsp", type=Path, default=None,
                    help=f"SMO 1.0.0 NSP (default: {DEFAULT_NSP})")
    ap.add_argument("--xci", type=Path, default=None,
                    help="SMO 1.0.0 XCI cartridge dump. Mutually exclusive "
                         "with --nsp. Requires title.keys (NSPs ship a .tik; "
                         "XCIs do not).")
    ap.add_argument("--keys", type=Path, default=DEFAULT_KEYS,
                    help=f"prod.keys (default: {DEFAULT_KEYS})")
    ap.add_argument("--titlekey", type=Path, default=None,
                    help="title.keys (default: derived from --keys's parent "
                         "dir as <keys-parent>/title.keys, matching hactool's "
                         "convention that prod.keys + title.keys live "
                         "together)")
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
    # Overrides for the apworld data files used in cross-validation. When
    # invoked from the wizard inside AP's frozen Launcher, the data files
    # have been extracted out of the .apworld zip to a separate location
    # (see _setup.build.bundled_data_file) — the REPO_ROOT-relative
    # defaults below only resolve on a dev source checkout.
    ap.add_argument("--locations", type=Path, default=APWORLD_LOCATIONS,
                    help=f"apworld locations.json (default: {APWORLD_LOCATIONS})")
    ap.add_argument("--items", type=Path, default=APWORLD_ITEMS,
                    help=f"apworld items.json (default: {APWORLD_ITEMS})")
    args = ap.parse_args(argv)

    # Re-bind the module globals the rest of the script reads. Cheaper
    # than threading args through every callsite — these are read-only
    # path constants downstream. (The `global` declaration is above, at
    # the top of main(), because Python requires it before any use.)
    APWORLD_LOCATIONS = args.locations
    APWORLD_ITEMS = args.items

    if not APWORLD_LOCATIONS.exists():
        return _fail(f"apworld locations.json not found at {APWORLD_LOCATIONS}")

    if args.romfs is not None:
        romfs = args.romfs
        if not (romfs / "SystemData" / "ShineInfo.szs").exists():
            return _fail(f"{romfs} is not a romfs (no SystemData/ShineInfo.szs)")
    else:
        # Resolve which dump to extract. Explicit --xci wins; explicit --nsp
        # next; neither falls back to the historical default NSP location.
        if args.nsp is not None and args.xci is not None:
            return _fail("pass only one of --nsp or --xci, not both")
        if args.xci is not None:
            dump_path, dump_kind = args.xci, "xci"
        elif args.nsp is not None:
            dump_path, dump_kind = args.nsp, "nsp"
        else:
            dump_path, dump_kind = DEFAULT_NSP, "nsp"
        print(f"[extract] dump: kind={dump_kind} path={dump_path}",
              file=sys.stderr, flush=True)
        hactool = resolve_hactool(args.hactool)
        # Default title.keys to the same directory as prod.keys — that's
        # where Lockpick_RCM drops both in a single run, and where users
        # who relocate prod.keys typically expect title.keys to live too.
        # hactool itself defaults to $HOME/.switch/title.keys regardless
        # of -k, which silently breaks XCI decode when --keys points
        # elsewhere; passing --titlekey= explicitly closes that gap.
        title_keys = args.titlekey if args.titlekey is not None \
            else args.keys.parent / "title.keys"
        extract_romfs(dump_path, dump_kind, args.keys, hactool, args.romfs_cache,
                      title_keys=title_keys)
        romfs = args.romfs_cache

    try:
        resolved, review = extract(romfs)
    except Exception as e:
        # Any parse failure here on a freshly-extracted romfs almost always
        # means the source NSP/XCI was damaged — hactool's "section X is
        # corrupted" warning (which we tolerate during extraction; see
        # extract_romfs) is the most common upstream signal but a clean
        # decrypt of a corrupt SZS would land here too. Surface an
        # actionable diagnostic instead of letting oead's low-level error
        # leak through.
        return _fail(
            f"failed to parse RomFS files ({type(e).__name__}: {e}).\n"
            "  This usually means the dump is damaged, truncated, or has\n"
            "  been modified (e.g. a trimmed NSP, an incomplete download,\n"
            "  or an XCI→NSP conversion that broke section hashes).\n"
            "  Re-dump SMO 1.0.0 with NXDumpTool from a clean retail source\n"
            "  and rerun the extract."
        )
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

    try:
        cap_entries, cap_review = extract_captures(romfs)
    except Exception as e:
        # Same rationale as the moons extract() above — convert oead's
        # low-level parse error into a "your dump is damaged" diagnostic.
        return _fail(
            f"failed to parse capture data ({type(e).__name__}: {e}).\n"
            "  This usually means the dump is damaged, truncated, or has\n"
            "  been modified. Re-dump SMO 1.0.0 with NXDumpTool from a\n"
            "  clean retail source and rerun the extract."
        )
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
