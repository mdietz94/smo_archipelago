"""Run Archipelago's MultiServer.py with auto-pip suppressed.

Same trick as scripts/ap_generate.py: short-circuit ModuleUpdate.update_ran
so MultiServer doesn't refuse to start over missing world-specific deps we
don't need (e.g. dolphin-memory-engine).

Usage (same args as MultiServer.py):

    bridge/.venv/Scripts/python scripts/ap_server.py \
        --port 38281 bridge/test_seeds/out/AP_*.archipelago
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AP_ROOT = REPO / "vendor" / "Archipelago"

os.chdir(AP_ROOT)
sys.path.insert(0, str(AP_ROOT))

import ModuleUpdate  # type: ignore[import-not-found]
ModuleUpdate.update_ran = True

sys.argv = [str(AP_ROOT / "MultiServer.py")] + sys.argv[1:]
runpy.run_path(str(AP_ROOT / "MultiServer.py"), run_name="__main__")
