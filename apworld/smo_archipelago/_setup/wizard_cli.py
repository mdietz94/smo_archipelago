"""Headless orchestrator for the setup pipeline.

The Kivy wizard (`wizard.py`) drives the same five phases through user-
clicked pages: **probe** (prereqs) → **install** (silent installers for
missing tools) → **extract** (RomFS → moon + capture maps) → **build**
(sync_capture_table + cmake/ninja cross-compile) → **deploy** (copy
artifacts to Ryujinx or an SD card or a custom folder).

This module exposes the *same* sequencing as a stateless, callback-driven
API so pytest / CI / a future packaged headless installer can drive the
whole pipeline without booting Kivy. Each phase is one function that:

- Takes its parameters as explicit arguments (no module globals).
- Streams progress through a single `EventCallback` so the caller can
  surface logs in whatever shape it wants (JSON Lines on stdout for CI,
  Kivy widget updates for the GUI, captured `list[dict]` for tests).
- Returns a typed `*Outcome` dataclass with `ok: bool`. A failed phase
  short-circuits the pipeline; the orchestrator never silently swallows
  errors.

The module also serves as a `python -m apworld.smo_archipelago._setup.wizard_cli`
entry point. With `--json-events` each event becomes one JSON object on
stdout (line-buffered, so a tail-f or a `pytest`-captured subprocess sees
events live). Without the flag, events are rendered as human-readable
log lines for terminal use.

Kivy is NEVER imported here — wizard.py stays the only module that
touches it. The wizard's per-page workers can call into these functions
to drop their own bespoke sequencing logic; until that refactor lands,
the wizard's per-page workers and this orchestrator are intentionally
parallel calls into the same `_setup.{prereqs,installers,build,deploy}`
primitives so they cannot drift.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


# ---------------------------------------------------------------------------
# Event stream
# ---------------------------------------------------------------------------

# One callback invocation per orchestrator event. The payload always
# contains an `event` (string discriminator) and `ts` (seconds since the
# pipeline started). Phase-scoped events also carry `phase`. The remaining
# fields are event-specific; see `_emit` call sites for the per-event
# schema.
EventCallback = Callable[[dict[str, Any]], None]


# Phase identifiers. Surfaced as module constants so callers / tests don't
# inline string literals that could drift.
PHASE_PROBE = "probe"
PHASE_INSTALL = "install"
PHASE_EXTRACT = "extract"
PHASE_BUILD = "build"
PHASE_DEPLOY = "deploy"

ALL_PHASES: tuple[str, ...] = (
    PHASE_PROBE,
    PHASE_INSTALL,
    PHASE_EXTRACT,
    PHASE_BUILD,
    PHASE_DEPLOY,
)


def canonical_maps_present() -> bool:
    """True iff both extracted maps live in `%APPDATA%/SMOArchipelago/data/`
    AND every map's SHA-256 matches the canonical SMO 1.0.0 USen
    fingerprint in `EXPECTED_MAP_SHA256`.

    Exposed at the wizard_cli layer (rather than imported from `.build`
    directly by the GUI) to keep the "is extraction needed?" predicate
    in one place — wizard.py's NSP page short-circuits past the dump
    pick when this returns True, and `run_extract` short-circuits past
    the subprocess when it returns True at extract time. The drift
    guards in `test_wizard_delegates_to_cli.py` forbid the wizard from
    calling `maps_ready` / `verify_map_hashes` directly for the same
    reason — the predicate has to stay symmetrical with the gate
    inside `run_extract`.
    """
    from .build import maps_ready, verify_map_hashes
    if not maps_ready():
        return False
    try:
        checks = verify_map_hashes()
    except Exception:
        return False
    return bool(checks) and all(c.match for c in checks)


def _emit(cb: EventCallback | None, event: str, *, t0: float, **fields: Any) -> None:
    """Build an event dict and hand it to the callback (if any).

    Centralized so every emitted event carries the same `event` + `ts`
    shape and the t0 anchor stays consistent across the pipeline.
    """
    if cb is None:
        return
    payload: dict[str, Any] = {
        "event": event,
        "ts": round(time.monotonic() - t0, 6),
    }
    payload.update(fields)
    cb(payload)


# ---------------------------------------------------------------------------
# Per-phase outcome types
# ---------------------------------------------------------------------------

@dataclass
class ProbeOutcome:
    """Result of the prereqs phase. `results` keeps the raw PrereqResult
    list so callers can render the same row-by-row status the Kivy
    prereqs page does; `missing_keys` is the subset whose `ok=False` and
    `auto_installable=True` (i.e. installable in the install phase)."""
    ok: bool
    results: list[Any]            # list[prereqs.PrereqResult]
    missing_keys: list[str]


@dataclass
class InstallOutcome:
    """Result of the install phase. `installed` / `failed` partition the
    keys we actually attempted (skipped keys land in neither). `ok` is
    True iff every attempted installer reported `ok=True`."""
    ok: bool
    installed: list[str]
    failed: list[str]


@dataclass
class ExtractOutcome:
    """Result of the extract phase.

    `returncode` is from the underlying `extract_shine_map.py` subprocess.
    `maps_present` flips True iff both `shine_map.json` and
    `capture_map.json` landed in `%APPDATA%/SMOArchipelago/data/`.
    `hash_ok` is True iff every map's SHA-256 matches the canonical
    SMO 1.0.0 USen fingerprint (`build.EXPECTED_MAP_SHA256`). All three
    have to be True for the pipeline to advance.
    """
    ok: bool
    returncode: int
    maps_present: bool
    hash_ok: bool
    hash_checks: list[Any] = field(default_factory=list)  # list[build.MapHashCheck]


@dataclass
class BuildOutcome:
    """Result of the build phase. `step_results` is keyed by step name
    (`sync_capture`, `build_switchmod`) so callers can blame the specific
    subprocess on failure. `outputs` is the dict of artifact paths the
    deploy phase consumes; populated iff `ok=True`."""
    ok: bool
    step_results: dict[str, Any] = field(default_factory=dict)   # dict[str, build.BuildResult]
    outputs: dict[str, Path] = field(default_factory=dict)


@dataclass
class DeployOutcome:
    """Result of the deploy phase. Wraps the underlying `DeployResult`
    so the CLI / tests see `ok`, the resolved target string, and the
    list of (src, dst) tuples in one shape regardless of which deploy
    function (`deploy_to_ryujinx` / `_sd` / `_custom_folder`) ran."""
    ok: bool
    target: str
    files: list[tuple[Path, Path]] = field(default_factory=list)
    error: str = ""


@dataclass
class PipelineOutcome:
    """Aggregate result of the full pipeline. `phases_run` is the ordered
    list of phases actually executed (a subset of ALL_PHASES — caller
    can request a subset via `phases=`). `failed_phase` is the first
    phase that returned `ok=False`, or None when every phase passed."""
    ok: bool
    phases_run: list[str]
    failed_phase: str | None = None
    probe: ProbeOutcome | None = None
    install: InstallOutcome | None = None
    extract: ExtractOutcome | None = None
    build: BuildOutcome | None = None
    deploy: DeployOutcome | None = None


# ---------------------------------------------------------------------------
# Per-phase orchestrators
# ---------------------------------------------------------------------------

def run_probe(
    *,
    hactool_override: Path | None = None,
    prod_keys_override: Path | None = None,
    callback: EventCallback | None = None,
    t0: float | None = None,
) -> ProbeOutcome:
    """Run every prereq detector and surface per-row results.

    Pure delegation to `_setup.prereqs.check_all`; the value-add here is
    the `prereq` event emission per row so a `--json-events` consumer
    can render row-by-row status without re-running detection. `t0` is
    the pipeline anchor for the timestamps in emitted events; pass the
    pipeline's t0 when calling from inside `run_pipeline`, omit otherwise.
    """
    from .prereqs import all_ok, check_all

    anchor = t0 if t0 is not None else time.monotonic()
    _emit(callback, "phase_start", phase=PHASE_PROBE, t0=anchor)
    results = check_all(
        hactool_override=hactool_override,
        prod_keys_override=prod_keys_override,
    )
    missing_keys: list[str] = []
    for r in results:
        _emit(
            callback, "prereq",
            t0=anchor,
            key=r.key,
            name=r.name,
            ok=r.ok,
            detail=r.detail,
            auto_installable=r.auto_installable,
        )
        if not r.ok and r.auto_installable:
            missing_keys.append(r.key)
    ok = all_ok(results)
    _emit(callback, "phase_end", phase=PHASE_PROBE, t0=anchor, ok=ok)
    return ProbeOutcome(ok=ok, results=list(results), missing_keys=missing_keys)


def run_install(
    keys: Sequence[str],
    *,
    preflight: bool = True,
    callback: EventCallback | None = None,
    t0: float | None = None,
) -> InstallOutcome:
    """Run silent installers for the given prereq keys, in the
    `installers.INSTALL_ORDER` sequence (not whatever order `keys` came in).

    `preflight=True` runs `check_internet` once + `check_winget` once
    when any winget-installable key is in the batch, before the first
    installer. Mirrors the Kivy "Install all missing" button's preflight.
    A failed preflight short-circuits with `ok=False` and an empty
    `installed` list.

    Each installer's stdout/stderr stream becomes `log` events with
    `phase=install` + `key=<prereq>` so a `--json-events` consumer can
    multiplex per-tool output. A failed installer stops the run (so
    later keys aren't attempted after Python 3.12 fails, since sail-deps
    needs Python).
    """
    from .installers import (
        INSTALL_ORDER, INSTALLERS, check_internet, check_winget,
    )

    anchor = t0 if t0 is not None else time.monotonic()
    _emit(callback, "phase_start", phase=PHASE_INSTALL, t0=anchor, keys=list(keys))

    ordered = [k for k in INSTALL_ORDER if k in set(keys)]
    # Any keys not recognized by INSTALL_ORDER fall through silently —
    # the caller probably wanted a no-op for them. Log so a typo is
    # surfaced without erroring out.
    unknown = [k for k in keys if k not in set(INSTALL_ORDER)]
    for k in unknown:
        _emit(
            callback, "log", phase=PHASE_INSTALL, t0=anchor,
            line=f"[wizard_cli] no installer registered for {k!r}; skipping",
        )

    if not ordered:
        _emit(callback, "phase_end", phase=PHASE_INSTALL, t0=anchor, ok=True)
        return InstallOutcome(ok=True, installed=[], failed=[])

    if preflight:
        _emit(callback, "log", phase=PHASE_INSTALL, t0=anchor,
              line="[wizard_cli] preflight: checking internet...")
        r = check_internet(
            lambda line: _emit(callback, "log",
                               phase=PHASE_INSTALL, t0=anchor, line=line),
        )
        if not r.ok:
            _emit(callback, "preflight",
                  t0=anchor, kind="internet", ok=False, detail=r.detail)
            _emit(callback, "phase_end", phase=PHASE_INSTALL, t0=anchor, ok=False)
            return InstallOutcome(ok=False, installed=[], failed=list(ordered))
        _emit(callback, "preflight",
              t0=anchor, kind="internet", ok=True, detail=r.detail)
        winget_keys = {"cmake", "ninja", "python312"}
        if any(k in winget_keys for k in ordered):
            _emit(callback, "log", phase=PHASE_INSTALL, t0=anchor,
                  line="[wizard_cli] preflight: checking winget...")
            r = check_winget(
                lambda line: _emit(callback, "log",
                                   phase=PHASE_INSTALL, t0=anchor, line=line),
            )
            if not r.ok:
                _emit(callback, "preflight",
                      t0=anchor, kind="winget", ok=False, detail=r.detail)
                _emit(callback, "phase_end",
                      phase=PHASE_INSTALL, t0=anchor, ok=False)
                return InstallOutcome(ok=False, installed=[], failed=list(ordered))
            _emit(callback, "preflight",
                  t0=anchor, kind="winget", ok=True, detail=r.detail)

    installed: list[str] = []
    failed: list[str] = []
    for key in ordered:
        fn = INSTALLERS.get(key)
        if fn is None:
            # Belt-and-braces: INSTALL_ORDER and INSTALLERS are defined
            # in the same module, but a future bug that drops a key
            # from one dict shouldn't crash the pipeline.
            _emit(callback, "log", phase=PHASE_INSTALL, t0=anchor,
                  line=f"[wizard_cli] no installer fn for {key!r}; skipping")
            continue
        _emit(callback, "install_start", t0=anchor, key=key)
        result = fn(
            lambda line, _k=key: _emit(
                callback, "log",
                phase=PHASE_INSTALL, t0=anchor, key=_k, line=line,
            ),
        )
        _emit(
            callback, "install_end",
            t0=anchor, key=key, ok=result.ok,
            returncode=result.returncode, detail=result.detail,
        )
        if result.ok:
            installed.append(key)
        else:
            failed.append(key)
            # Stop on first failure — installers depend on each other
            # (e.g. sail_python_deps needs python312).
            break

    ok = not failed
    _emit(callback, "phase_end", phase=PHASE_INSTALL, t0=anchor, ok=ok)
    return InstallOutcome(ok=ok, installed=installed, failed=failed)


def run_extract(
    dump_path: Path,
    *,
    hactool_override: Path | None = None,
    prod_keys_override: Path | None = None,
    verify_hash: bool = True,
    callback: EventCallback | None = None,
    t0: float | None = None,
) -> ExtractOutcome:
    """Run the extractor + (optionally) verify the canonical hash gate.

    `verify_hash=True` is the production path: a hash mismatch is a hard
    fail because it signals a non-1.0.0 / non-USen / corrupted dump.
    Tests sometimes need `verify_hash=False` to drive the extract on
    synthetic data; production CI should never set this.

    Short-circuit: if `verify_hash=True` AND the maps already live in
    `%APPDATA%/SMOArchipelago/data/` AND every map's SHA-256 already
    matches `EXPECTED_MAP_SHA256`, we skip the (slow) subprocess and
    return success immediately. Saves 2-5 minutes on every wizard re-run
    against an unchanged dump and lets users navigate past the extract
    page without re-picking the dump when the maps survived from a
    prior install.
    """
    from .build import maps_ready, run_extract_maps, verify_map_hashes

    anchor = t0 if t0 is not None else time.monotonic()
    _emit(callback, "phase_start", phase=PHASE_EXTRACT, t0=anchor,
          dump=str(dump_path))

    if verify_hash and maps_ready():
        try:
            pre_checks = verify_map_hashes()
        except Exception as e:
            _emit(callback, "log", phase=PHASE_EXTRACT, t0=anchor,
                  line=f"[wizard_cli] pre-extract hash check crashed: "
                       f"{type(e).__name__}: {e}")
            pre_checks = []
        if pre_checks and all(c.match for c in pre_checks):
            for c in pre_checks:
                _emit(
                    callback, "hash_check",
                    t0=anchor, filename=c.filename, match=c.match,
                    present=c.present,
                    expected=c.expected, actual=c.actual,
                )
            _emit(callback, "log", phase=PHASE_EXTRACT, t0=anchor,
                  line="[wizard_cli] maps already present and hashes "
                       "match canonical SMO 1.0.0 USen fingerprint — "
                       "skipping extraction")
            _emit(callback, "maps_present", t0=anchor, present=True)
            _emit(callback, "phase_end", phase=PHASE_EXTRACT, t0=anchor, ok=True)
            return ExtractOutcome(
                ok=True,
                returncode=0,
                maps_present=True,
                hash_ok=True,
                hash_checks=list(pre_checks),
            )

    if not dump_path.is_file():
        _emit(callback, "log", phase=PHASE_EXTRACT, t0=anchor,
              line=f"[wizard_cli] dump file does not exist: {dump_path}")
        _emit(callback, "phase_end", phase=PHASE_EXTRACT, t0=anchor, ok=False)
        return ExtractOutcome(
            ok=False, returncode=2, maps_present=False, hash_ok=False,
        )

    result = run_extract_maps(
        dump_path,
        keys_path=prod_keys_override,
        hactool_path=hactool_override,
        on_line=lambda line: _emit(
            callback, "log", phase=PHASE_EXTRACT, t0=anchor, line=line,
        ),
    )
    _emit(callback, "extract_subprocess",
          t0=anchor, ok=result.ok, returncode=result.returncode)

    maps_present = maps_ready()
    _emit(callback, "maps_present", t0=anchor, present=maps_present)

    hash_ok = False
    hash_checks: list[Any] = []
    if verify_hash and maps_present:
        try:
            hash_checks = verify_map_hashes()
        except Exception as e:
            _emit(callback, "log", phase=PHASE_EXTRACT, t0=anchor,
                  line=f"[wizard_cli] hash check crashed: "
                       f"{type(e).__name__}: {e}")
            hash_checks = []
        for c in hash_checks:
            _emit(
                callback, "hash_check",
                t0=anchor, filename=c.filename, match=c.match,
                present=c.present,
                expected=c.expected, actual=c.actual,
            )
        hash_ok = bool(hash_checks) and all(c.match for c in hash_checks)
    elif not verify_hash:
        # Pretend hashes pass; explicit opt-out path. The caller has
        # told us not to gate.
        hash_ok = True

    ok = result.ok and maps_present and hash_ok
    _emit(callback, "phase_end", phase=PHASE_EXTRACT, t0=anchor, ok=ok)
    return ExtractOutcome(
        ok=ok,
        returncode=result.returncode,
        maps_present=maps_present,
        hash_ok=hash_ok,
        hash_checks=list(hash_checks),
    )


def run_build(
    bridge_host: str,
    *,
    callback: EventCallback | None = None,
    t0: float | None = None,
) -> BuildOutcome:
    """Sync the generated capture table, then run the Switch-mod build.

    `bridge_host` is baked into subsdk9 at compile time and used by
    ApDiscovery as the SEED for the unicast /24 sweep — the actual
    SMOClient might be on a neighbouring octet after DHCP renumber, the
    sweep covers that. The wizard normally fills this from
    `client.net_util.detect_lan_ip()`; the CLI does the same when
    `--bridge-host` is omitted.

    Pre-warms the prereq detectors that populate the resolved-bin
    caches `run_build_switchmod` reads when exporting `SMOAP_*_BIN` env
    vars to the build subprocess. The Kivy wizard's page order
    guarantees the prereq page ran before build, but wizard_cli's
    `--phases build` (alone) doesn't have that natural sequencing.
    Without the prewarm, each unset cache falls through to
    `build_switchmod.py`'s hardcoded default -- and per upstream PR
    #171's audit, those defaults are not safe for end-user machines
    (literal-username Ninja path, wrong-Python `sys.executable` under
    Archipelago's 3.13/3.14 launcher fallback, cmake bin-vs-exe slot
    mismatch). Mirrors upstream commits c9a3a54 + 1bec0e0; the bug
    class is "resolver/consumer inconsistency", and every cache the
    build consumes has to be aligned.
    """
    from .build import collect_build_outputs, run_build_switchmod, run_sync_capture_table
    from .prereqs import (
        check_all, resolved_cmake, resolved_llvm_bin, resolved_mingw_bin,
        resolved_ninja_bin, resolved_python312_bin,
    )

    anchor = t0 if t0 is not None else time.monotonic()
    _emit(callback, "phase_start", phase=PHASE_BUILD, t0=anchor,
          bridge_host=bridge_host)

    # Every resolved-bin cache `run_build_switchmod` consumes:
    #   python312 -> SMOAP_PYTHON_BIN (PR #169 / c9a3a54)
    #   ninja     -> SMOAP_NINJA_BIN  (PR #171 / 1bec0e0)
    #   cmake     -> SMOAP_CMAKE_BIN  (PR #171 / 1bec0e0)
    #   llvm19    -> SMOAP_LLVM_BIN
    #   winlibs   -> SMOAP_MINGW_BIN
    # If ANY is unset (cmake's bare-name "cmake" sentinel counts as
    # unset for the env-var-set check), run check_all to align every
    # resolver with the build's consumer slots. Cheap (~1s of detector
    # subprocesses) and idempotent — when probe phase already ran, the
    # caches are populated and this branch no-ops.
    needs_warm = (
        resolved_python312_bin() is None
        or resolved_ninja_bin() is None
        or resolved_llvm_bin() is None
        or resolved_mingw_bin() is None
        or resolved_cmake() == "cmake"
    )
    if needs_warm:
        _emit(callback, "log", phase=PHASE_BUILD, t0=anchor,
              line="[wizard_cli] prewarming prereq detectors so the "
                   "build subprocess gets the wizard-verified toolchain "
                   "dirs pinned via SMOAP_*_BIN env vars (probe phase "
                   "did not run in this invocation)")
        check_all()

    step_results: dict[str, Any] = {}
    steps: list[tuple[str, Callable[[], Any]]] = [
        ("sync_capture",
         lambda: run_sync_capture_table(
             on_line=lambda line: _emit(
                 callback, "log",
                 phase=PHASE_BUILD, t0=anchor, step="sync_capture", line=line,
             ),
         )),
        ("build_switchmod",
         lambda: run_build_switchmod(
             bridge_host,
             on_line=lambda line: _emit(
                 callback, "log",
                 phase=PHASE_BUILD, t0=anchor, step="build_switchmod", line=line,
             ),
         )),
    ]
    for step_name, fn in steps:
        _emit(callback, "build_step_start", t0=anchor, step=step_name)
        try:
            r = fn()
        except FileNotFoundError as e:
            _emit(callback, "build_step_end", t0=anchor,
                  step=step_name, ok=False, returncode=-1,
                  error=f"{type(e).__name__}: {e}")
            _emit(callback, "phase_end", phase=PHASE_BUILD, t0=anchor, ok=False)
            return BuildOutcome(ok=False, step_results=step_results)
        step_results[step_name] = r
        _emit(callback, "build_step_end",
              t0=anchor, step=step_name,
              ok=r.ok, returncode=r.returncode)
        if not r.ok:
            _emit(callback, "phase_end", phase=PHASE_BUILD, t0=anchor, ok=False)
            return BuildOutcome(ok=False, step_results=step_results)

    try:
        outputs = collect_build_outputs()
    except FileNotFoundError as e:
        _emit(callback, "log", phase=PHASE_BUILD, t0=anchor,
              line=f"[wizard_cli] build outputs missing: {e}")
        _emit(callback, "phase_end", phase=PHASE_BUILD, t0=anchor, ok=False)
        return BuildOutcome(ok=False, step_results=step_results)

    _emit(callback, "build_outputs", t0=anchor,
          files={k: str(v) for k, v in outputs.items()})
    _emit(callback, "phase_end", phase=PHASE_BUILD, t0=anchor, ok=True)
    return BuildOutcome(ok=True, step_results=step_results, outputs=outputs)


# Deploy target kinds the CLI accepts. Kept here so the argparse choices
# and the dispatcher stay in lockstep.
DEPLOY_TARGETS: tuple[str, ...] = ("ryujinx", "sd", "custom", "none")


def run_deploy(
    target: str,
    target_path: Path | None,
    build_outputs: dict[str, Path],
    *,
    callback: EventCallback | None = None,
    t0: float | None = None,
) -> DeployOutcome:
    """Copy build outputs to the chosen target.

    `target` is one of "ryujinx", "sd", "custom", "none". The "none"
    target is a no-op success path used by CI to skip the deploy step
    cleanly when only earlier phases need exercising.

    `target_path` is required for sd / custom (no auto-detect at the
    CLI layer; the GUI handles defaults). For ryujinx, None means
    `deploy.detect_ryujinx_path()` provides the default.
    """
    from .deploy import (
        deploy_to_custom_folder, deploy_to_ryujinx, deploy_to_sd,
        detect_ryujinx_path,
    )

    anchor = t0 if t0 is not None else time.monotonic()
    _emit(callback, "phase_start", phase=PHASE_DEPLOY, t0=anchor,
          target=target, target_path=str(target_path) if target_path else None)

    if target == "none":
        _emit(callback, "phase_end", phase=PHASE_DEPLOY, t0=anchor, ok=True)
        return DeployOutcome(ok=True, target="none (skipped)")

    if target == "ryujinx":
        resolved = target_path or detect_ryujinx_path()
        if resolved is None or not Path(resolved).is_dir():
            err = (
                f"Ryujinx folder not found "
                f"(passed={target_path!r}, auto={detect_ryujinx_path()!r}). "
                f"Pass --deploy-path explicitly."
            )
            _emit(callback, "phase_end", phase=PHASE_DEPLOY, t0=anchor,
                  ok=False, error=err)
            return DeployOutcome(ok=False, target="ryujinx", error=err)
        result = deploy_to_ryujinx(Path(resolved), build_outputs)
    elif target == "sd":
        if target_path is None:
            err = "--deploy-path is required for sd target"
            _emit(callback, "phase_end", phase=PHASE_DEPLOY, t0=anchor,
                  ok=False, error=err)
            return DeployOutcome(ok=False, target="sd", error=err)
        if not target_path.exists():
            err = f"SD card path does not exist: {target_path}"
            _emit(callback, "phase_end", phase=PHASE_DEPLOY, t0=anchor,
                  ok=False, error=err)
            return DeployOutcome(ok=False, target="sd", error=err)
        result = deploy_to_sd(target_path, build_outputs)
    elif target == "custom":
        if target_path is None:
            err = "--deploy-path is required for custom target"
            _emit(callback, "phase_end", phase=PHASE_DEPLOY, t0=anchor,
                  ok=False, error=err)
            return DeployOutcome(ok=False, target="custom", error=err)
        # Reject typo'd parents -- a `Path("C:/totally/made/up").mkdir(
        # parents=True)` would silently create the entire tree, which
        # is rarely what the user wants (they typed a wrong path, not
        # a request to materialize a four-deep folder hierarchy). The
        # leaf may not exist (we'll mkdir it), but the parent must.
        if not target_path.parent.exists():
            err = f"Custom folder parent does not exist: {target_path.parent}"
            _emit(callback, "phase_end", phase=PHASE_DEPLOY, t0=anchor,
                  ok=False, error=err)
            return DeployOutcome(ok=False, target="custom", error=err)
        target_path.mkdir(parents=True, exist_ok=True)
        result = deploy_to_custom_folder(target_path, build_outputs)
    else:
        err = f"unknown deploy target {target!r} (expected one of {DEPLOY_TARGETS})"
        _emit(callback, "phase_end", phase=PHASE_DEPLOY, t0=anchor,
              ok=False, error=err)
        return DeployOutcome(ok=False, target=target, error=err)

    _emit(
        callback, "deploy_result",
        t0=anchor,
        ok=result.ok, target=result.target,
        file_count=len(result.files), error=result.error,
    )
    _emit(callback, "phase_end", phase=PHASE_DEPLOY, t0=anchor, ok=result.ok)
    return DeployOutcome(
        ok=result.ok, target=result.target,
        files=list(result.files), error=result.error,
    )


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

@dataclass
class PipelineOptions:
    """Inputs to `run_pipeline`. Kept as a dataclass so the wizard layer
    and the CLI builder can construct it with named fields (matching
    intent to argparse output) without confusing positional-arg slips."""
    phases: tuple[str, ...] = ALL_PHASES
    dump_path: Path | None = None
    bridge_host: str = ""
    deploy_target: str = "none"
    deploy_path: Path | None = None
    hactool_override: Path | None = None
    prod_keys_override: Path | None = None
    install_missing: bool = False
    install_preflight: bool = True
    verify_hash: bool = True


def run_pipeline(
    opts: PipelineOptions,
    *,
    callback: EventCallback | None = None,
) -> PipelineOutcome:
    """Run the requested phases in order; short-circuit on first failure.

    The orchestrator decides which phases to skip based on `opts.phases`
    + the install/extract/build/deploy-specific dependencies (e.g. you
    can't deploy without `build.outputs`). Skipped-but-listed phases
    emit a `phase_skip` event for traceability — caller can tell "we
    didn't run the install phase because no prereqs were missing" apart
    from "user didn't request install in phases".
    """
    from .net import detect_lan_ip

    t0 = time.monotonic()
    _emit(callback, "pipeline_start", t0=t0, phases=list(opts.phases))

    outcome = PipelineOutcome(ok=True, phases_run=[])

    if PHASE_PROBE in opts.phases:
        probe = run_probe(
            hactool_override=opts.hactool_override,
            prod_keys_override=opts.prod_keys_override,
            callback=callback, t0=t0,
        )
        outcome.probe = probe
        outcome.phases_run.append(PHASE_PROBE)
        if not probe.ok and not opts.install_missing:
            # Prereqs failed and the caller didn't authorize install —
            # fail fast so we don't run extract with broken tools.
            outcome.ok = False
            outcome.failed_phase = PHASE_PROBE
            _emit(callback, "pipeline_end", t0=t0,
                  ok=False, failed_phase=PHASE_PROBE)
            return outcome
    else:
        probe = None

    if PHASE_INSTALL in opts.phases:
        if not opts.install_missing:
            _emit(callback, "phase_skip", phase=PHASE_INSTALL, t0=t0,
                  reason="install_missing not requested")
        elif probe is None:
            # Caller asked for install without probe — we'd have to
            # re-run check_all to know what's missing. Cleanest is to
            # surface a clear error.
            _emit(callback, "phase_skip", phase=PHASE_INSTALL, t0=t0,
                  reason="probe phase not run; nothing to install")
        elif not probe.missing_keys:
            _emit(callback, "phase_skip", phase=PHASE_INSTALL, t0=t0,
                  reason="no missing auto-installable prereqs")
        else:
            inst = run_install(
                probe.missing_keys,
                preflight=opts.install_preflight,
                callback=callback, t0=t0,
            )
            outcome.install = inst
            outcome.phases_run.append(PHASE_INSTALL)
            if not inst.ok:
                outcome.ok = False
                outcome.failed_phase = PHASE_INSTALL
                _emit(callback, "pipeline_end", t0=t0,
                      ok=False, failed_phase=PHASE_INSTALL)
                return outcome

    if PHASE_EXTRACT in opts.phases:
        if opts.dump_path is None:
            err = "extract phase requested but no dump_path provided"
            _emit(callback, "phase_skip", phase=PHASE_EXTRACT, t0=t0, reason=err)
            outcome.ok = False
            outcome.failed_phase = PHASE_EXTRACT
            _emit(callback, "pipeline_end", t0=t0,
                  ok=False, failed_phase=PHASE_EXTRACT)
            return outcome
        ex = run_extract(
            opts.dump_path,
            hactool_override=opts.hactool_override,
            prod_keys_override=opts.prod_keys_override,
            verify_hash=opts.verify_hash,
            callback=callback, t0=t0,
        )
        outcome.extract = ex
        outcome.phases_run.append(PHASE_EXTRACT)
        if not ex.ok:
            outcome.ok = False
            outcome.failed_phase = PHASE_EXTRACT
            _emit(callback, "pipeline_end", t0=t0,
                  ok=False, failed_phase=PHASE_EXTRACT)
            return outcome

    if PHASE_BUILD in opts.phases:
        bridge_host = opts.bridge_host or detect_lan_ip()
        bd = run_build(bridge_host, callback=callback, t0=t0)
        outcome.build = bd
        outcome.phases_run.append(PHASE_BUILD)
        if not bd.ok:
            outcome.ok = False
            outcome.failed_phase = PHASE_BUILD
            _emit(callback, "pipeline_end", t0=t0,
                  ok=False, failed_phase=PHASE_BUILD)
            return outcome

    if PHASE_DEPLOY in opts.phases:
        # Deploy needs build outputs; if build phase wasn't run, the
        # bundled switch_mod tree may still have artifacts from a
        # previous build. Try to pick them up; fail clean otherwise.
        if outcome.build is not None:
            outputs = outcome.build.outputs
        else:
            from .build import collect_build_outputs
            try:
                outputs = collect_build_outputs()
            except FileNotFoundError as e:
                err = f"deploy phase requested but no build outputs found: {e}"
                _emit(callback, "phase_skip", phase=PHASE_DEPLOY, t0=t0, reason=err)
                outcome.ok = False
                outcome.failed_phase = PHASE_DEPLOY
                _emit(callback, "pipeline_end", t0=t0,
                      ok=False, failed_phase=PHASE_DEPLOY)
                return outcome
        dp = run_deploy(
            opts.deploy_target, opts.deploy_path, outputs,
            callback=callback, t0=t0,
        )
        outcome.deploy = dp
        outcome.phases_run.append(PHASE_DEPLOY)
        if not dp.ok:
            outcome.ok = False
            outcome.failed_phase = PHASE_DEPLOY
            _emit(callback, "pipeline_end", t0=t0,
                  ok=False, failed_phase=PHASE_DEPLOY)
            return outcome

    _emit(callback, "pipeline_end", t0=t0, ok=outcome.ok,
          phases_run=outcome.phases_run)
    return outcome


# ---------------------------------------------------------------------------
# Callback adapters: JSON Lines on stdout / human-readable text
# ---------------------------------------------------------------------------

def make_json_events_callback(stream=None) -> EventCallback:
    """Return a callback that writes one JSON object per event to `stream`
    (default: sys.stdout). Flushes after each event so a CI harness
    tailing the stream sees events live."""
    s = stream if stream is not None else sys.stdout

    def emit(payload: dict[str, Any]) -> None:
        s.write(json.dumps(payload, default=str) + "\n")
        s.flush()
    return emit


def make_text_callback(stream=None) -> EventCallback:
    """Return a callback that renders events as human-readable log lines.

    The format is intentionally compact so it's readable in a terminal:
    `[t+0.123] phase=build event=build_step_end step=sync_capture ok=True`
    The full event dict is also dumped for events that carry a `line`
    field, so subprocess log output is surfaced verbatim.
    """
    s = stream if stream is not None else sys.stdout

    def emit(payload: dict[str, Any]) -> None:
        # Prefix with [t+N.NNN] for quick scanning. Render `line` events
        # without the dict wrapper so subprocess output reads as if it
        # were piped through directly.
        ts = payload.get("ts", 0.0)
        if payload.get("event") == "log":
            line = payload.get("line", "")
            s.write(f"[t+{ts:.3f}] {line}\n")
        else:
            fields = " ".join(
                f"{k}={v!r}" for k, v in payload.items()
                if k not in ("event", "ts")
            )
            evt = payload.get("event")
            s.write(f"[t+{ts:.3f}] {evt} {fields}\n")
        s.flush()
    return emit


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m apworld.smo_archipelago._setup.wizard_cli",
        description=(
            "Headless SMO Archipelago setup pipeline. Runs the same "
            "probe -> install -> extract -> build -> deploy sequence the "
            "Kivy wizard drives, but with no UI and a JSON-event stream "
            "suitable for CI."
        ),
    )
    p.add_argument(
        "--json-events", action="store_true",
        help="Emit one JSON object per event on stdout (line-delimited).",
    )
    p.add_argument(
        "--phases", default=",".join(ALL_PHASES),
        help=(
            f"Comma-separated phase subset to run. Choices: "
            f"{','.join(ALL_PHASES)}. Default: all."
        ),
    )
    p.add_argument(
        "--dump", type=Path, default=None,
        help="Path to SMO 1.0.0 NSP or XCI (required for extract phase).",
    )
    p.add_argument(
        "--bridge-host", default="",
        help="LAN IP for the Switch -> bridge connection. Default: auto-detect.",
    )
    p.add_argument(
        "--deploy-target", choices=DEPLOY_TARGETS, default="none",
        help="Deploy destination kind. Default: none (skip deploy).",
    )
    p.add_argument(
        "--deploy-path", type=Path, default=None,
        help="Deploy destination path. Required for sd/custom; "
             "optional for ryujinx (auto-detects %%APPDATA%%/Ryujinx).",
    )
    p.add_argument(
        "--hactool", type=Path, default=None,
        help="Override hactool path (else PATH / auto-installed).",
    )
    p.add_argument(
        "--keys", type=Path, default=None,
        help="Override prod.keys path (else ~/.switch/prod.keys).",
    )
    p.add_argument(
        "--auto-install", action="store_true",
        help="Auto-install missing prereqs before extract.",
    )
    p.add_argument(
        "--no-install-preflight", action="store_true",
        help="Skip the internet + winget preflight (testing only).",
    )
    p.add_argument(
        "--no-verify-hash", action="store_true",
        help=(
            "Skip the SHA-256 canonical-fingerprint gate after extract. "
            "Testing only -- production runs must verify."
        ),
    )
    return p


def _parse_phases(raw: str) -> tuple[str, ...]:
    """Validate the --phases argument. Raises ValueError on unknown names."""
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    unknown = [p for p in parts if p not in ALL_PHASES]
    if unknown:
        raise ValueError(
            f"unknown phase(s): {unknown}. Valid: {ALL_PHASES}"
        )
    return parts


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry. Returns the process exit code (0 on success)."""
    args = _build_parser().parse_args(argv)
    try:
        phases = _parse_phases(args.phases)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json_events:
        cb = make_json_events_callback()
    else:
        cb = make_text_callback()

    opts = PipelineOptions(
        phases=phases,
        dump_path=args.dump,
        bridge_host=args.bridge_host,
        deploy_target=args.deploy_target,
        deploy_path=args.deploy_path,
        hactool_override=args.hactool,
        prod_keys_override=args.keys,
        install_missing=args.auto_install,
        install_preflight=not args.no_install_preflight,
        verify_hash=not args.no_verify_hash,
    )
    outcome = run_pipeline(opts, callback=cb)
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
