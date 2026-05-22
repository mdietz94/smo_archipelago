#!/usr/bin/env python3
"""Initialise + pin the LibHakkun imgui branch + ocornut/imgui submodules.

The on-Switch debug overlay (ui::ApDebugConsole) depends on two upstream
components that ship outside the main LibHakkun tree:

  * `switch-mod/sys` — LibHakkun on its `imgui` branch (not `main`).
    Provides `addons/ImGui/` (NVN backend + shaders) and `addons/DebugRenderer/`
    (lightweight text rendering, currently unused but harmless to compile).

  * `switch-mod/lib/imgui` — Dear ImGui itself. Vendored upstream.

Both are listed in `.gitmodules`, but the pinned commit + first-time init
need a one-shot dance because:

  * Bumping `switch-mod/sys` to the imgui branch from whatever commit it
    was last pinned at requires checkout-then-restage.
  * `lib/imgui` is a brand-new submodule path — `git submodule update --init`
    won't populate it unless it's in the index (which it won't be until
    after `git submodule add` or this script does the equivalent).

Run after `git pull` + `git submodule update --init --recursive`. Idempotent:
re-running on a tree that's already pinned to the right commits is a no-op
besides a small `git fetch`.

Both pins are intentionally targets-of-branches rather than tags:
fruityloops1/LibHakkun's `imgui` branch is the live development line for the
addon, and we want to follow it. The user can `git -C switch-mod/sys log`
to inspect what they're getting.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Pinned references — bump these when LibHakkun's imgui branch / ocornut/imgui
# release a fix we want.
HAKKUN_REMOTE = "https://github.com/fruityloops1/LibHakkun.git"
HAKKUN_BRANCH = "imgui"
HAKKUN_PIN    = "e92ac56466102d3692fb98170dcb62f185070849"  # imgui branch HEAD 2026-05

IMGUI_REMOTE  = "https://github.com/ocornut/imgui.git"
IMGUI_PIN     = "8936b58fe26e8c3da834b8f60b06511d537b4c63"  # v1.92.8

SYS_DIR   = REPO_ROOT / "switch-mod" / "sys"
IMGUI_DIR = REPO_ROOT / "switch-mod" / "lib" / "imgui"


def run(cmd, cwd=None, check=True):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


def ensure_submodule_initialized(path: Path, remote: str, branch: str | None) -> None:
    """If `path` is empty or not a git dir, clone the remote into it."""
    if (path / ".git").exists():
        return
    print(f"[setup-imgui] cloning {remote} -> {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["git", "clone"]
    if branch:
        args += ["--branch", branch]
    args += [remote, str(path)]
    run(args)


def checkout_pinned(path: Path, pin: str) -> None:
    print(f"[setup-imgui] fetching + checking out {pin[:10]} in {path.name}")
    run(["git", "fetch", "--depth", "200", "origin", pin + ":refs/setup-imgui-pin"],
        cwd=path, check=False)  # if the SHA is reachable from default refs, fetch is a no-op
    run(["git", "checkout", "--detach", pin], cwd=path)


def main() -> int:
    if not (REPO_ROOT / ".gitmodules").exists():
        sys.exit("[setup-imgui] no .gitmodules found — wrong cwd?")

    # 1. Make sure switch-mod/sys exists.
    ensure_submodule_initialized(SYS_DIR, HAKKUN_REMOTE, HAKKUN_BRANCH)
    checkout_pinned(SYS_DIR, HAKKUN_PIN)

    # 2. Same for switch-mod/lib/imgui.
    ensure_submodule_initialized(IMGUI_DIR, IMGUI_REMOTE, None)
    checkout_pinned(IMGUI_DIR, IMGUI_PIN)

    # 3. Stage the submodule SHAs in the index so `git status` is clean.
    # Use git update-index instead of git add, since `git add submodule-path/`
    # would try to add the file contents and complain.
    print("[setup-imgui] staging submodule SHAs in index")
    run(["git", "submodule", "absorbgitdirs"], cwd=REPO_ROOT, check=False)
    run(["git", "add", "switch-mod/sys", "switch-mod/lib/imgui"], cwd=REPO_ROOT, check=False)

    print("[setup-imgui] done. Next: run scripts/patch_hakkun.py + scripts/build_switchmod.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
