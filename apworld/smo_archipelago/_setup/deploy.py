"""Copy build outputs to the user's chosen deploy target.

Two targets exist:

  - **Real Switch (SD card)**: files land at
    `<drive>:/atmosphere/contents/0100000000010000/{exefs,romfs}/`
    The same layout `switch-mod/CMakeLists.txt`'s `install` target
    produces in `sd-overlay/`. The wizard probes for removable drives
    that already contain an `atmosphere/` directory (signal that this
    drive is currently a Switch SD card) and offers them as picks;
    the user can also browse to any path.

  - **Ryujinx (emulator)**: files land at
    `%APPDATA%/Ryujinx/mods/contents/0100000000010000/smo-archipelago/exefs/`
    (for `subsdk9` + `main.npdm`). Identical paths to the existing
    `-DRYU_PATH=...` post-build hook in `switch-mod/CMakeLists.txt`, so
    this is the well-known dev target.

Both deploy paths take the same `build_dir` argument (the
`<bundled>/switch_mod/build/exefs/` produced by `build.py`) so
switching between targets after a build doesn't require a rebuild —
the bytes are identical, only the destination differs.

NOTE: `ap_config.json` used to ship alongside the binaries (legacy
exefs-runtime SD-read path) but the Hakkun cutover retired that read
path — bridge IP is baked into `subsdk9` at compile time via the
wizard's BRIDGE_HOST cmake arg, and runtime UDP discovery handles the
IP-changes case. So the deploy layouts only ship the two real
artifacts: subsdk9 + main.npdm.
"""

from __future__ import annotations

import os
import shutil
import string
import sys
from dataclasses import dataclass
from pathlib import Path

# SMO's Atmosphere title id — never changes for SMO 1.0.0.
SMO_TITLE_ID = "0100000000010000"
# Module name under Ryujinx's mods/contents — matches the directory the
# CMakeLists.txt `RYU_PATH` post-build hook writes into.
RYU_MOD_NAME = "smo-archipelago"


@dataclass
class DeployResult:
    """Per-deploy summary returned to the wizard for the "Copied N files
    to ..." summary line. `files` is in source→dest tuple form so the
    wizard can render a small table if it wants."""
    ok: bool
    target: str           # human-readable target description ("SD card at D:\\", "Ryujinx")
    files: list[tuple[Path, Path]]
    error: str = ""


def detect_sd_candidates() -> list[Path]:
    """Return all currently-mounted drive roots that look like a Switch
    SD card (i.e. have an `atmosphere/` directory at the root).

    Windows-only for v1 (the plan scopes Linux/Mac as a follow-up). On
    non-Windows we return [] — the user can still browse-to-path on the
    wizard's Deploy page.
    """
    if sys.platform != "win32":
        return []
    candidates: list[Path] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:/")
        if not root.exists():
            continue
        atmo = root / "atmosphere"
        if atmo.is_dir():
            candidates.append(root)
    return candidates


def detect_ryujinx_path() -> Path | None:
    """Return `%APPDATA%/Ryujinx/` if it exists, else None.

    Matches the location Ryujinx itself defaults to on Windows — the same
    one our existing `-DRYU_PATH=...` cmake post-build hook targets. The
    wizard's Deploy page also lets the user browse to a non-default
    install via "Browse for Ryujinx folder"; this function is just the
    auto-detect hint.
    """
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    p = Path(appdata) / "Ryujinx"
    return p if p.is_dir() else None


def _sd_layout(sd_root: Path) -> dict[str, Path]:
    """Destination paths for the two artifacts on a Switch SD card."""
    base = sd_root / "atmosphere" / "contents" / SMO_TITLE_ID
    return {
        "subsdk9": base / "exefs" / "subsdk9",
        "main.npdm": base / "exefs" / "main.npdm",
    }


def _ryujinx_layout(ryujinx_root: Path) -> dict[str, Path]:
    """Destination paths under a Ryujinx install root."""
    mods = ryujinx_root / "mods" / "contents" / SMO_TITLE_ID / RYU_MOD_NAME
    return {
        "subsdk9": mods / "exefs" / "subsdk9",
        "main.npdm": mods / "exefs" / "main.npdm",
    }


class DeployCopyError(OSError):
    """`_copy_files` raises this in place of a bare OSError so the wizard's
    error handler can display the source/destination context the user
    needs to diagnose the failure (and so the OSError catch in the deploy
    wrappers can dispatch on it without losing context)."""


def _copy_files(
    sources: dict[str, Path],
    dests: dict[str, Path],
) -> list[tuple[Path, Path]]:
    """Copy each (source, dest) pair, creating parent dirs.

    Returns the list of (source, dest) actually copied for the wizard
    summary. Raises `DeployCopyError` on any IO error, with the failing
    pair embedded in the message — `shutil.copy2`'s default OSError
    sometimes elides the destination path, which is the most useful
    diagnostic on a Switch SD card deploy (wrong drive picked, drive
    yanked mid-copy, AV write block).

    After a successful copy each destination's size is asserted equal
    to the source's size — `shutil.copy2` doesn't fsync and some Windows
    file system filters can return early before the bytes have landed.
    A size mismatch is treated as a copy failure so the wizard reports
    the partial write instead of marking deploy "complete".
    """
    copied: list[tuple[Path, Path]] = []
    for key, src in sources.items():
        dst = dests[key]
        try:
            src_size = src.stat().st_size
        except OSError as e:
            raise DeployCopyError(
                f"Source file unreadable before copy: {src} ({e}). "
                f"Re-run the Build step to regenerate it."
            ) from e
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DeployCopyError(
                f"Could not create destination directory {dst.parent} "
                f"for {key}: {e}. Check that the target drive is "
                f"writable and has free space."
            ) from e
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            raise DeployCopyError(
                f"Failed to copy {src.name} to {dst}: {e}. "
                f"If this is an SD card, check it's still inserted and "
                f"not write-protected; if Ryujinx, check it's not "
                f"running with the mod file locked."
            ) from e
        try:
            actual = dst.stat().st_size
        except OSError as e:
            raise DeployCopyError(
                f"Copied {src.name} to {dst} but couldn't stat the "
                f"result: {e}. The destination may have been deleted "
                f"or the drive disconnected during the copy."
            ) from e
        if actual != src_size:
            raise DeployCopyError(
                f"Partial write: {src.name} is {src_size} bytes but "
                f"{dst} is {actual} bytes. The drive ran out of space "
                f"mid-copy or was disconnected. Free space (or "
                f"reconnect the SD card) and re-run Deploy."
            )
        copied.append((src, dst))
    return copied


def deploy_to_sd(sd_root: Path, build_outputs: dict[str, Path]) -> DeployResult:
    """Copy build outputs to a Switch SD card root.

    `sd_root` should be the drive root (e.g. `D:/`), not a deeper path.
    Caller validates the path; we just lay the files out under it.
    """
    try:
        dests = _sd_layout(sd_root)
        copied = _copy_files(build_outputs, dests)
        return DeployResult(
            ok=True,
            target=f"SD card at {sd_root}",
            files=copied,
        )
    except (OSError, PermissionError) as e:
        # Preserve the underlying exception class name (PermissionError,
        # FileNotFoundError, OSError) alongside the DeployCopyError
        # context so the user sees both "what kind of OS failure" and
        # "which copy step it was".
        cause = e.__cause__ if isinstance(e, DeployCopyError) and e.__cause__ else e
        return DeployResult(
            ok=False,
            target=f"SD card at {sd_root}",
            files=[],
            error=f"{type(cause).__name__}: {e}",
        )


def deploy_to_ryujinx(
    ryujinx_root: Path,
    build_outputs: dict[str, Path],
) -> DeployResult:
    """Copy build outputs to a Ryujinx install root."""
    try:
        dests = _ryujinx_layout(ryujinx_root)
        copied = _copy_files(build_outputs, dests)
        return DeployResult(
            ok=True,
            target=f"Ryujinx at {ryujinx_root}",
            files=copied,
        )
    except (OSError, PermissionError) as e:
        cause = e.__cause__ if isinstance(e, DeployCopyError) and e.__cause__ else e
        return DeployResult(
            ok=False,
            target=f"Ryujinx at {ryujinx_root}",
            files=[],
            error=f"{type(cause).__name__}: {e}",
        )


def deploy_to_custom_folder(
    custom_root: Path,
    build_outputs: dict[str, Path],
) -> DeployResult:
    """Copy build outputs to an arbitrary folder using the SD-card layout.

    Useful when the user wants to manage SD-card sync themselves —
    e.g. UMS later, or copy via DBI / Goldleaf, or stage on a Dropbox
    folder before a manual transfer. We write the same `atmosphere/
    contents/0100000000010000/exefs/` subtree the SD-card deploy
    produces, just under the user's chosen folder root, so they can
    drop the entire subtree onto a Switch SD card and have it work
    without any path-rewriting.
    """
    try:
        dests = _sd_layout(custom_root)
        copied = _copy_files(build_outputs, dests)
        return DeployResult(
            ok=True,
            target=f"Custom folder at {custom_root}",
            files=copied,
        )
    except (OSError, PermissionError) as e:
        cause = e.__cause__ if isinstance(e, DeployCopyError) and e.__cause__ else e
        return DeployResult(
            ok=False,
            target=f"Custom folder at {custom_root}",
            files=[],
            error=f"{type(cause).__name__}: {e}",
        )
