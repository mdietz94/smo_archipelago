"""Auto-loop wrapper around scripts/ap_generate.py.

Runs ap_generate.py N times with rotating --seed values, isolates each in its
own output subdirectory so the spoiler files don't collide. Failures
(unfillable placements, etc.) are caught + logged, generation continues with
the next seed.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class GeneratedSeed:
    seed: int
    spoiler_path: Path
    run_dir: Path


def generate_many(
    *,
    repo_root: Path,
    player_files_path: Path,
    output_root: Path,
    num_seeds: int,
    base_seed: int,
    timeout_per_seed_sec: float = 120.0,
) -> list[GeneratedSeed]:
    """Run ap_generate.py N times. Returns successful (seed, spoiler) pairs."""
    output_root.mkdir(parents=True, exist_ok=True)
    ap_generate = repo_root / "scripts" / "ap_generate.py"
    if not ap_generate.exists():
        raise RuntimeError(f"missing {ap_generate}")
    if not (repo_root / "vendor" / "Archipelago" / "Generate.py").exists():
        raise RuntimeError(
            "vendor/Archipelago is not initialised. Run: "
            "git submodule update --init vendor/Archipelago"
        )

    results: list[GeneratedSeed] = []
    for i in range(num_seeds):
        seed = base_seed + i
        run_dir = output_root / f"seed_{seed:08d}"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [
                    sys.executable, str(ap_generate),
                    "--player_files_path", str(player_files_path),
                    "--outputpath", str(run_dir),
                    "--seed", str(seed),
                ],
                capture_output=True, text=True,
                timeout=timeout_per_seed_sec,
            )
        except subprocess.TimeoutExpired:
            log.warning("seed %d timed out after %ds", seed, timeout_per_seed_sec)
            continue
        elapsed = time.monotonic() - t0

        if proc.returncode != 0:
            log.warning("seed %d failed (rc=%d, %.1fs): %s",
                        seed, proc.returncode, elapsed,
                        proc.stderr.strip().splitlines()[-1] if proc.stderr else "")
            (run_dir / "gen.stderr.log").write_text(proc.stderr or "")
            continue

        # Find the spoiler. Modern AP versions output it as a sibling .txt;
        # older versions or some configurations bundle it inside the player
        # AP_*.zip. Extract any zip that's present so the spoiler is on disk.
        for z in run_dir.glob("AP_*.zip"):
            try:
                with zipfile.ZipFile(z) as zf:
                    zf.extractall(run_dir)
            except zipfile.BadZipFile:
                log.warning("seed %d: bad zip at %s", seed, z)

        spoilers = list(run_dir.glob("AP_*_Spoiler.txt"))
        if not spoilers:
            spoilers = list(run_dir.rglob("*_Spoiler.txt"))
        if not spoilers:
            log.warning("seed %d: gen ok but no spoiler at %s", seed, run_dir)
            continue
        results.append(GeneratedSeed(seed=seed, spoiler_path=spoilers[0], run_dir=run_dir))
        log.info("seed %d ok (%.1fs) -> %s", seed, elapsed, spoilers[0].name)

    return results
