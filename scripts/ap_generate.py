"""Run Archipelago's Generate.py with auto-pip suppressed.

Generate.py's first action is `ModuleUpdate.update()` which iterates through
the full requirements.txt and tries to pip-install every missing dep — even
world-specific deps we don't need (dolphin-memory-engine, kivy, etc.). We
ship our own minimal network-only dep set in the bridge venv, so suppress
the auto-pip by short-circuiting `update_ran` before Generate imports it.

Usage (same args as Generate.py):

    bridge/.venv/Scripts/python scripts/ap_generate.py \
        --player_files_path bridge/test_seeds \
        --outputpath bridge/test_seeds/out
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AP_ROOT = REPO / "vendor" / "Archipelago"

# Archipelago expects to be the cwd so it can find world / template paths.
os.chdir(AP_ROOT)
sys.path.insert(0, str(AP_ROOT))

import ModuleUpdate  # type: ignore[import-not-found]
ModuleUpdate.update_ran = True

# Reshape argv as if Generate.py was invoked directly.
sys.argv = [str(AP_ROOT / "Generate.py")] + sys.argv[1:]
runpy.run_path(str(AP_ROOT / "Generate.py"), run_name="__main__")
