"""SMO Archipelago seed simulator — auto-generate N spoilers + simulate pacing.

Usage (default: generate 20 seeds, 5 timing sims each, vs an ALttP coplayer):

    bridge/.venv/Scripts/python scripts/simulate_seeds.py \\
        --num-seeds 20 --sims-per-seed 5 \\
        --coplayer alttp \\
        --output charts/

Iteration modes (skip generation, reuse spoilers from a previous run):

    python scripts/simulate_seeds.py \\
        --spoiler-glob 'bridge/test_seeds/sim_out/*/seed_*/AP_*_Spoiler.txt' \\
        --sims-per-seed 10 --coplayer kh --output charts/

Single-spoiler debug:

    python scripts/simulate_seeds.py \\
        --spoiler bridge/test_seeds/out/AP_12345_Spoiler.txt \\
        --sims-per-seed 1 --coplayer smo --output /tmp/sim-test/

Output is 5 PNG charts: reachable_over_time, kingdom_dwell, bk_heatmap,
completion_when_leaving (the headline metric), coplayer_gap_hist.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bridge"))

from smo_ap_bridge.seed_sim import charts, generate, sim, spoiler as spoiler_mod
from smo_ap_bridge.seed_sim import coplayer as coplayer_mod
from smo_ap_bridge.seed_sim import timing as timing_mod


def main(argv: list[str] | None = None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # 1) Resolve spoilers — either glob/file (iteration mode) or auto-generate.
    if args.spoiler or args.spoiler_glob:
        spoiler_paths = _collect_existing_spoilers(args)
    else:
        spoiler_paths = _autogen_spoilers(args)

    if not spoiler_paths:
        print("ERROR: no spoilers to simulate.", file=sys.stderr)
        return 2

    print(f"loaded {len(spoiler_paths)} spoiler(s); running "
          f"{args.sims_per_seed} sim(s) each = {len(spoiler_paths) * args.sims_per_seed} runs")

    # 2) Build coplayer specs.
    coplayer_specs = [coplayer_mod.parse_coplayer_spec(s) for s in args.coplayer]

    # 3) Build time profile + overrides.
    profile = timing_mod.get_profile(args.time_profile)
    if args.time_override:
        profile = timing_mod.apply_overrides(
            profile, timing_mod.parse_overrides(args.time_override)
        )

    # 4) Register capture names so the progression classifier knows them.
    _register_capture_names()

    # 5) Parse spoilers.
    parsed: list[spoiler_mod.SpoilerData] = []
    for sp_path in spoiler_paths:
        try:
            parsed.append(spoiler_mod.parse_spoiler(sp_path))
        except spoiler_mod.SpoilerParseError as e:
            print(f"WARN: {sp_path.name}: {e}", file=sys.stderr)
    if not parsed:
        print("ERROR: every spoiler failed to parse.", file=sys.stderr)
        return 3

    # 6) Run simulations.
    t0 = time.monotonic()
    results: list[sim.SimResult] = []
    for i, r in enumerate(sim.run_many(
        parsed, profile, coplayer_specs,
        base_seed=args.base_seed,
        sims_per_spoiler=args.sims_per_seed,
        time_cap_sec=args.time_cap_hours * 3600,
        bk_threshold_sec=args.bk_threshold_sec,
    )):
        results.append(r)
        if (i + 1) % 10 == 0:
            print(f"  sim {i + 1}/{len(parsed) * args.sims_per_seed}", flush=True)
    print(f"all sims done in {time.monotonic() - t0:.1f}s")

    # 7) Render charts.
    out_dir = Path(args.output)
    written = charts.render_all(results, out_dir)
    for path in written:
        print(f"  wrote {path.relative_to(REPO) if path.is_relative_to(REPO) else path}")

    # 8) Tiny stdout summary of the headline metric. Not the deliverable, but
    #    useful for quick CI/regression sanity checks.
    print("\n--- summary ---")
    finished = sum(1 for r in results if r.finished)
    print(f"finished runs: {finished}/{len(results)}")
    avg_h = sum(r.final_time_sec for r in results) / len(results) / 3600
    print(f"avg final sim time: {avg_h:.1f} h")
    bk_count = sum(len(r.soft_bk_intervals) for r in results)
    print(f"total soft-BK windows: {bk_count}")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_argument_group("spoiler source (default: auto-generate)")
    src.add_argument("--num-seeds", type=int, default=20,
                     help="number of seeds to generate (default 20)")
    src.add_argument("--base-seed", type=int, default=1000,
                     help="first --seed value passed to ap_generate.py")
    src.add_argument("--player-file", type=Path,
                     default=REPO / "bridge" / "test_seeds",
                     help="passed as --player_files_path to ap_generate.py")
    src.add_argument("--gen-output-root", type=Path,
                     default=REPO / "bridge" / "test_seeds" / "sim_out",
                     help="where generated spoilers go; one subdir per seed")
    src.add_argument("--spoiler", type=Path,
                     help="reuse a single existing spoiler (skips generation)")
    src.add_argument("--spoiler-glob",
                     help="reuse existing spoilers matching a glob (skips generation)")

    sim_g = p.add_argument_group("simulation")
    sim_g.add_argument("--sims-per-seed", type=int, default=5,
                       help="timing simulations per spoiler (default 5)")
    sim_g.add_argument("--coplayer", action="append", default=[],
                       help="coplayer profile, e.g. 'alttp', 'kh:PlayerB', or "
                            "'custom:checks=300,mean=150,std=40,name=Friend'. "
                            "Repeat for multi-coplayer seeds.")
    sim_g.add_argument("--time-profile", default="default",
                       choices=sorted(timing_mod.PROFILES),
                       help="per-kingdom check time table (default 'default')")
    sim_g.add_argument("--time-override",
                       help="override means, e.g. 'Sand=180,Metro=200'")
    sim_g.add_argument("--time-cap-hours", type=float, default=80.0,
                       help="simulated time cap per run (default 80h)")
    sim_g.add_argument("--bk-threshold-sec", type=float, default=1800.0,
                       help="soft-BK threshold (default 1800s = 30min)")

    out = p.add_argument_group("output")
    out.add_argument("--output", default="charts",
                     help="directory for PNG charts (default ./charts/)")
    out.add_argument("-v", "--verbose", action="store_true")
    return p


def _collect_existing_spoilers(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if args.spoiler:
        paths.append(args.spoiler)
    if args.spoiler_glob:
        paths.extend(Path(p) for p in glob.glob(args.spoiler_glob, recursive=True))
    return sorted(set(paths))


def _autogen_spoilers(args: argparse.Namespace) -> list[Path]:
    print(f"auto-generating {args.num_seeds} seed(s) via scripts/ap_generate.py...")
    run_root = args.gen_output_root / f"run_{int(time.time())}"
    seeds = generate.generate_many(
        repo_root=REPO,
        player_files_path=args.player_file,
        output_root=run_root,
        num_seeds=args.num_seeds,
        base_seed=args.base_seed,
    )
    print(f"  generated {len(seeds)}/{args.num_seeds} successful spoilers in {run_root}")
    return [s.spoiler_path for s in seeds]


def _register_capture_names() -> None:
    """Pull the bare-enemy capture names out of the apworld items.json so the
    progression detector knows e.g. 'Goomba' is a meaningful unlock item."""
    items_json = REPO / "apworld" / "smo_archipelago" / "data" / "items.json"
    if not items_json.exists():
        return
    data = json.loads(items_json.read_text(encoding="utf-8"))
    names = {
        entry["name"]
        for entry in data
        if "Capture" in (entry.get("category") or [])
    }
    sim.register_capture_names(names)


if __name__ == "__main__":
    sys.exit(main())
