# seed-sim handoff — pick this up after capturing a real spoiler

This doc is a fast-load brief for the next session. The simulator
(`scripts/simulate_seeds.py` + `bridge/smo_ap_bridge/seed_sim/`) is code-
complete and tested against a hand-trimmed fixture, but the AP spoiler
*format* used by the parser is **inferred, not verified against a real
spoiler**. Validating + fixing that is the first task once a real spoiler
is available.

## Status snapshot (commit `01e94a4`)

- 14 files, ~2k LoC across module + CLI + 4 test files + 1 fixture.
- 31 new tests, full bridge suite 102 passed / 10 skipped.
- CLI smoke-tested end-to-end against the fixture: all 5 PNGs produced.
- Branch: `claude/archipelago-seed-simulator-hiI0W` (already pushed).

## The one task that gates "ship": validate the spoiler parser

1. Generate one real spoiler against the existing loopback yaml:
   ```pwsh
   cd C:\Users\maxwe\SMOArchipelago
   bridge\.venv\Scripts\python scripts\ap_generate.py `
       --player_files_path bridge\test_seeds `
       --outputpath bridge\test_seeds\out
   ```
   This needs `vendor/Archipelago/` initialised (`git submodule update --init
   vendor/Archipelago` if not) and the apworld zip installed (`bridge\.venv\
   Scripts\python scripts\install_apworld.py`).

2. Confirm the parser handles it:
   ```pwsh
   bridge\.venv\Scripts\python -c "from smo_ap_bridge.seed_sim.spoiler import parse_spoiler; import glob; p = glob.glob('bridge/test_seeds/out/AP_*_Spoiler.txt')[0]; d = parse_spoiler(p); print(f'spheres={len(d.spheres)} slots={list(d.slots)} smo_locs={sum(1 for s in d.spheres if s.finder_slot == d.smo_slot().slot)}')"
   ```
   Expected: positive sphere count, a `Mario` (or whatever the player_name is) slot
   marked as `Manual_SMO_archipelago`, hundreds of SMO locations.

3. **If `parse_spoiler` raises `SpoilerParseError("no Playthrough block")`**: the
   spoiler was generated with `output_spoiler` < 3. Edit
   `vendor/Archipelago/host.yaml` (or pass it via env to ap_generate.py) to
   set `general_options.output_spoiler: 3`.

4. **If it parses but sphere count is 0, or `smo_locs` is way off (e.g. < 100
   for a real seed)**: the regex isn't matching real placement lines. Open
   the spoiler in an editor and grep for a known SMO location line (e.g.
   `Cap: Frog-Jumping`). Compare its actual format vs the two regex
   expectations in `bridge/smo_ap_bridge/seed_sim/spoiler.py`:
   - `_PLACE_LINE_RE` (Playthrough lines)
   - `_LOC_DETAIL_RE` (per-slot Locations lines)
   - `_split_middle` (which delimiter to use between location and item)

   The parser already accepts BOTH ` -> ` and `: ` as the location/item
   delimiter inside `_split_middle`. The most likely remaining issues are:
   - The slot-header format if it doesn't look like `Mario (Manual_SMO_archipelago):`
   - Indentation differences in the playthrough block (currently
     `_SPHERE_OPEN_RE` matches optional leading whitespace; brace lines are
     skipped)
   - The Locations section terminator — I whitelist known section names
     (Playthrough, Paths, Hints, Starting Items, etc.) in
     `_KNOWN_SECTION_HEADERS`. If the real spoiler has a section header I
     missed, add it.

5. Once parsing is verified, trim the real spoiler down to ~50 lines (drop
   most spheres / most slots) and **replace** `bridge/tests/fixtures/
   sample_spoiler.txt` with the trimmed version. Update
   `tests/test_seed_sim_spoiler.py` counts (line 22: `assert len(data.spheres)
   == 11` — bump to whatever the trimmed real spoiler has). The
   `test_run_one_*` tests in `test_seed_sim_sim.py` will need similar
   adjustment if the trimmed fixture has different sphere structure.

## Then: the headline run

Once the parser handles real spoilers, run the auto-generate flow:

```pwsh
bridge\.venv\Scripts\python scripts\simulate_seeds.py `
    --num-seeds 20 --sims-per-seed 5 `
    --coplayer alttp `
    --output charts\
```

Expected runtime: 5-10 min (gen at ~10s/seed * 20 = ~3 min, then sims are
sub-second per run = ~1 min, then chart rendering).

Inspect the 5 PNGs in `charts/`:
- **`completion_when_leaving.png`** — the headline. Each kingdom is a
  box-plot of "% checked when player first left." Boxes whose median is
  above the 70% red line indicate the seed forces the player to ~100%
  that kingdom before they can move on. Sand, Wooded, Metro are the
  likely culprits — they have the most locations and gate progression.
- **`kingdom_dwell.png`** — per-run stacked bars. Healthy: a tall stack
  with many colors. Bad: one color (kingdom) dominates total time.
- **`bk_heatmap.png`** — bands of color = stretches of soft-BK. Solid
  yellow band across many runs = pattern, not noise.
- **`reachable_over_time.png`** — long flat-near-zero plateaus = stuck.
- **`coplayer_gap_hist.png`** — long right tail = coplayer is the
  bottleneck.

## Tuning knobs you'll want once you have real charts

All these are CLI flags; no code edits needed.

| Knob | Default | When to change |
|---|---|---|
| `--time-profile` | `default` | Try `speedrun` / `casual` to bracket pacing |
| `--time-override 'Sand=180,Metro=200'` | (none) | If a kingdom feels off |
| `--bk-threshold-sec` | 1800 (30 min) | Lower to flag pacing issues more aggressively |
| `--time-cap-hours` | 80 | Bump if many runs hit the cap unfinished |
| `--coplayer kh` | n/a (must pass) | Compare slow-game coplayer (KH) vs fast (ALttP) |

Per-kingdom Gaussian means in
`bridge/smo_ap_bridge/seed_sim/timing.py:DEFAULT_PROFILE` are educated
guesses anchored to two real datapoints (speedrun WR ~31 s/moon, HLTB
~250 s/moon). After you've watched yourself play a few kingdoms, edit
these means to match your actual pace — that's the single largest
correctness lever for the headline chart.

Coplayer presets in `bridge/smo_ap_bridge/seed_sim/coplayer.py:PRESETS`
are similarly judgment. If you've watched a specific friend play, their
real per-check rate beats anything I guessed.

## Modeling assumptions worth knowing

The simulator's playstyle: **stay in current kingdom until allowed to
leave; fastest-first within kingdom; when truly BK, return to fastest
unchecked anywhere visited**. This matches your stated playstyle
preference and the "save the stuff I don't want to do for last" pattern.

A few things the sim does NOT model:
- **Coplayer BK**. Coplayer faucet keeps producing items at constant
  rate; we don't ask whether *they* have something to check. Fine for
  "is this SMO seed pathological", less fine for absolute multi-world
  duration prediction.
- **Death/restart cost**. No DeathLink modeling, no save-restart loops.
- **Kingdom geometry**. Travel cost between kingdoms is a flat 120s
  regardless of which Odyssey route is faster.
- **Per-check variance from skill curve**. The Gaussian is by kingdom,
  not by location difficulty within a kingdom — so we can't distinguish
  "Moon Pipe Room" from "Boss Reward" in Sand. The `per_location_time`
  pre-sampling at run start is what gives "fastest first" meaning, but
  each location's draw is i.i.d.

These are all M9-level refinements; the current model is enough to
answer "is this seed likely to be bad."

## File map

```
bridge/smo_ap_bridge/seed_sim/
  __init__.py         package marker + docstring
  spoiler.py          parser. THIS IS WHAT MAY NEED ADJUSTMENT after real spoiler.
  timing.py           per-kingdom Gaussian; default/speedrun/casual profiles
  coplayer.py         CoplayerProfile + PRESETS + parse_coplayer_spec
  sim.py              discrete-event loop + _Simulator + run_one / run_many
  charts.py           5 matplotlib renderers + render_all()
  generate.py         subprocess wrapper around scripts/ap_generate.py
scripts/simulate_seeds.py    CLI entry point
bridge/tests/
  fixtures/sample_spoiler.txt      hand-trimmed; REPLACE with real spoiler trim
  test_seed_sim_spoiler.py         parser tests
  test_seed_sim_sim.py             discrete-event loop tests
  test_seed_sim_units.py           timing + coplayer unit tests
  test_seed_sim_charts.py          PNG render smoke tests
```

The whole module is self-contained — no edits to existing bridge code.
Removing the `seed_sim/` directory and the 4 test files would revert
the simulator without affecting the M0-M6 runtime.

## If `ap_generate.py` errors out on auto-gen

Pre-existing issue, not seed-sim's fault: `scripts/ap_generate.py` needs
several Python deps installed in the bridge venv (PyYAML,
`setuptools<81`, `websockets==13.1`). See `bridge/tests/test_ap_loopback.py`
docstring for the exact pip line. The auto-gen mode will surface this as
"seed N failed (rc=1, ...): ModuleNotFoundError: No module named 'yaml'"
in the log — fix by installing the missing deps and re-running.
