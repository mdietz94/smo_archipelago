"""Tests for `_setup.wizard_cli` — the headless orchestrator that
drives the same probe -> install -> extract -> build -> deploy
sequence as the Kivy wizard, but with a JSON-event stream and no UI.

Heavy primitives (`run_extract_maps`, `run_sync_capture_table`,
`run_sync_shine_table`, `run_build_switchmod`, `INSTALLERS`,
`deploy_to_*`, `check_all`) are
monkeypatched per-test so the orchestration's sequencing + event
emission can be exercised in CI without a Switch dump, prod.keys,
LLVM toolchain, SD card, or Ryujinx install. Tests assert on:

- Phase ordering and short-circuit-on-failure.
- Event-stream shape (a CI harness consuming `--json-events` needs a
  stable schema, so we pin the event names + key fields here).
- Argparse boundary behavior (unknown phases rejected; --json-events
  vs human-readable text differ in shape; deploy-target validation).
- The wizard.py-style worker hooks (callback, retry, hash-gate) all
  reduce to the same `*Outcome` dataclasses regardless of which entry
  point invoked them.

We never let the orchestrator's `run_pipeline` reach into a real
subprocess, the real `%APPDATA%`, or a real network — every test runs
in milliseconds against monkeypatched primitives.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from _setup import wizard_cli
from _setup.wizard_cli import (
    ALL_PHASES,
    DEPLOY_TARGETS,
    BuildOutcome,
    DeployOutcome,
    ExtractOutcome,
    InstallOutcome,
    PipelineOptions,
    PipelineOutcome,
    ProbeOutcome,
    _parse_phases,
    make_json_events_callback,
    make_text_callback,
    run_build,
    run_deploy,
    run_extract,
    run_install,
    run_pipeline,
    run_probe,
)


# ---------------------------------------------------------------------------
# Fakes for the primitives wizard_cli delegates to
# ---------------------------------------------------------------------------

@dataclass
class _FakePrereq:
    """Mirror of `_setup.prereqs.PrereqResult` minus the fields wizard_cli
    doesn't read. Kept local so tests don't depend on the real dataclass
    accumulating new fields."""
    key: str
    name: str
    ok: bool
    detail: str = ""
    auto_installable: bool = True


@dataclass
class _FakeBuildResult:
    """Mirror of `_setup.build.BuildResult` shape."""
    ok: bool
    returncode: int = 0
    log: str = ""


@dataclass
class _FakeInstallResult:
    """Mirror of `_setup.installers.InstallResult` shape."""
    ok: bool
    returncode: int = 0
    log: str = ""
    detail: str = ""


@dataclass
class _FakeDeployResult:
    """Mirror of `_setup.deploy.DeployResult` shape."""
    ok: bool
    target: str
    files: list[tuple[Path, Path]]
    error: str = ""


@dataclass
class _FakeHashCheck:
    """Mirror of `_setup.build.MapHashCheck` shape."""
    filename: str
    expected: str
    actual: str
    present: bool
    match: bool


def _collect_events() -> tuple[list[dict[str, Any]], Any]:
    """Return (sink, callback) — callback appends to sink. Tests then
    assert against the sink list instead of redirecting stdout."""
    sink: list[dict[str, Any]] = []
    return sink, sink.append


# ---------------------------------------------------------------------------
# Event-stream schema basics
# ---------------------------------------------------------------------------

def test_all_phases_constant_is_canonical_order() -> None:
    """The phase order is load-bearing — install MUST come after probe
    (probe identifies the missing keys) and deploy MUST come after build
    (deploy reads build_outputs). A typo / reorder here would silently
    break the pipeline sequencing."""
    assert ALL_PHASES == ("probe", "install", "extract", "build", "deploy")


def test_deploy_targets_constant_matches_argparse_choices() -> None:
    """argparse uses DEPLOY_TARGETS as the choices= for --deploy-target.
    Keeping them in sync prevents a future "I added 'usb' to the
    dispatcher but argparse still rejects it" UX bug."""
    parser = wizard_cli._build_parser()
    deploy_action = next(
        a for a in parser._actions if a.dest == "deploy_target"
    )
    assert tuple(deploy_action.choices) == DEPLOY_TARGETS


def test_parse_phases_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown phase"):
        _parse_phases("probe,not_a_phase,build")


def test_parse_phases_accepts_subset() -> None:
    assert _parse_phases("extract,build") == ("extract", "build")


def test_parse_phases_strips_whitespace_and_empty_segments() -> None:
    assert _parse_phases(" probe , , build ") == ("probe", "build")


def test_json_events_callback_emits_one_line_per_event() -> None:
    """The JSON callback must produce ONE JSON object per line so a CI
    consumer can `for line in stream: json.loads(line)` without
    accumulating state. A multi-line JSON value would silently break
    that pattern."""
    buf = io.StringIO()
    cb = make_json_events_callback(buf)
    cb({"event": "phase_start", "ts": 0.0, "phase": "probe"})
    cb({"event": "phase_end", "ts": 0.01, "phase": "probe", "ok": True})
    lines = buf.getvalue().rstrip("\n").split("\n")
    assert len(lines) == 2
    a = json.loads(lines[0])
    b = json.loads(lines[1])
    assert a["event"] == "phase_start" and a["phase"] == "probe"
    assert b["event"] == "phase_end" and b["ok"] is True


def test_json_events_callback_serializes_paths_as_strings() -> None:
    """Path objects are non-JSON-serializable by default. The CLI passes
    them through callbacks (dump path, deploy_path, build outputs), so
    the JSON serializer needs `default=str` — losing that turns the
    --json-events stream into a TypeError storm."""
    buf = io.StringIO()
    cb = make_json_events_callback(buf)
    cb({"event": "phase_start", "ts": 0.0, "dump": Path("/tmp/x.nsp")})
    payload = json.loads(buf.getvalue())
    assert payload["dump"] == str(Path("/tmp/x.nsp"))


def test_text_callback_renders_log_lines_inline() -> None:
    """`log` events should render the line verbatim with a timestamp
    prefix — not as a dict-repr — so subprocess output reads cleanly
    in a terminal. A dict-repr would bury compiler errors inside
    `{'event': 'log', 'line': '...'}` quoting."""
    buf = io.StringIO()
    cb = make_text_callback(buf)
    cb({"event": "log", "ts": 1.5, "line": "ninja: build complete"})
    out = buf.getvalue()
    assert "ninja: build complete" in out
    assert "1.500" in out
    assert "'event'" not in out  # not a dict repr


# ---------------------------------------------------------------------------
# Phase: probe
# ---------------------------------------------------------------------------

def test_run_probe_emits_one_prereq_event_per_row(monkeypatch) -> None:
    """The CI consumer needs per-row events to render row-by-row status
    without re-running detection. Drop or merge a row here and the
    JSON consumer loses the granularity it depends on."""
    fake_results = [
        _FakePrereq(key="llvm19", name="LLVM 19.1.x", ok=True, detail="19.1.7"),
        _FakePrereq(key="cmake", name="CMake 3.24+", ok=False,
                    detail="not found", auto_installable=True),
        _FakePrereq(key="prodkeys", name="prod.keys", ok=False,
                    detail="not found", auto_installable=False),
    ]
    monkeypatch.setattr(
        "_setup.prereqs.check_all", lambda **kw: fake_results
    )
    monkeypatch.setattr(
        "_setup.prereqs.all_ok",
        lambda rs: all(r.ok for r in rs),
    )

    sink, cb = _collect_events()
    outcome = run_probe(callback=cb)

    prereq_events = [e for e in sink if e["event"] == "prereq"]
    assert {e["key"] for e in prereq_events} == {"llvm19", "cmake", "prodkeys"}
    assert outcome.ok is False
    # Only auto-installable failures should land in missing_keys —
    # prodkeys must NOT (user has to dump it themselves; no installer).
    assert outcome.missing_keys == ["cmake"]


def test_run_probe_returns_ok_when_all_rows_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        "_setup.prereqs.check_all",
        lambda **kw: [_FakePrereq(key="cmake", name="cmake", ok=True)],
    )
    monkeypatch.setattr(
        "_setup.prereqs.all_ok", lambda rs: all(r.ok for r in rs)
    )
    outcome = run_probe()
    assert outcome.ok is True
    assert outcome.missing_keys == []


# ---------------------------------------------------------------------------
# Phase: install
# ---------------------------------------------------------------------------

def test_run_install_no_keys_short_circuits_ok(monkeypatch) -> None:
    """An empty install request must succeed without invoking preflight
    or installers. The pipeline's "no missing prereqs" path depends on
    this — otherwise a clean machine sees a spurious internet/winget
    probe on every run."""
    called: list[str] = []
    monkeypatch.setattr(
        "_setup.installers.check_internet",
        lambda on_line=None: called.append("internet") or
                              _FakeInstallResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.installers.check_winget",
        lambda on_line=None: called.append("winget") or
                             _FakeInstallResult(ok=True),
    )
    outcome = run_install([])
    assert outcome.ok is True
    assert outcome.installed == [] and outcome.failed == []
    assert called == []


def test_run_install_runs_in_install_order_not_arg_order(monkeypatch) -> None:
    """INSTALL_ORDER is load-bearing because some installers depend on
    others (sail_python_deps needs python312 first). Passing keys in a
    different order must NOT reorder execution."""
    monkeypatch.setattr(
        "_setup.installers.check_internet",
        lambda on_line=None: _FakeInstallResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.installers.check_winget",
        lambda on_line=None: _FakeInstallResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.installers.INSTALL_ORDER",
        ("python312", "sail_python_deps", "cmake"),
    )
    order: list[str] = []

    def _fake(key: str):
        def runner(on_line=None):
            order.append(key)
            return _FakeInstallResult(ok=True)
        return runner

    monkeypatch.setattr(
        "_setup.installers.INSTALLERS",
        {
            "python312": _fake("python312"),
            "sail_python_deps": _fake("sail_python_deps"),
            "cmake": _fake("cmake"),
        },
    )
    # Caller passes them out of order; orchestrator must reorder.
    outcome = run_install(["sail_python_deps", "cmake", "python312"])
    assert outcome.ok is True
    assert order == ["python312", "sail_python_deps", "cmake"]


def test_run_install_aborts_on_preflight_failure(monkeypatch) -> None:
    """A failed internet preflight must NOT spawn the installer
    subprocesses — pre-flight is the whole point of the check. If we
    proceeded, we'd hit N download timeouts in a row."""
    ran_installer: list[str] = []
    monkeypatch.setattr(
        "_setup.installers.check_internet",
        lambda on_line=None: _FakeInstallResult(
            ok=False, detail="no network",
        ),
    )
    monkeypatch.setattr(
        "_setup.installers.INSTALL_ORDER", ("cmake",),
    )
    monkeypatch.setattr(
        "_setup.installers.INSTALLERS",
        {"cmake": lambda on_line=None: (
            ran_installer.append("cmake")
            or _FakeInstallResult(ok=True)
        )},
    )
    sink, cb = _collect_events()
    outcome = run_install(["cmake"], preflight=True, callback=cb)
    assert outcome.ok is False
    assert ran_installer == []
    pf = [e for e in sink if e["event"] == "preflight"]
    assert pf and pf[0]["ok"] is False


def test_run_install_stops_on_first_failure(monkeypatch) -> None:
    """sail_python_deps needs python312. If python312 fails, attempting
    sail_python_deps would just compound the failure with a less
    actionable error. Stop on first failure to keep the diagnostic
    surface narrow."""
    monkeypatch.setattr(
        "_setup.installers.check_internet",
        lambda on_line=None: _FakeInstallResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.installers.check_winget",
        lambda on_line=None: _FakeInstallResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.installers.INSTALL_ORDER",
        ("python312", "sail_python_deps"),
    )
    invocations: list[str] = []

    def _python_fails(on_line=None):
        invocations.append("python312")
        return _FakeInstallResult(ok=False, returncode=1, detail="boom")

    def _sail(on_line=None):
        invocations.append("sail_python_deps")
        return _FakeInstallResult(ok=True)

    monkeypatch.setattr(
        "_setup.installers.INSTALLERS",
        {"python312": _python_fails, "sail_python_deps": _sail},
    )
    outcome = run_install(["python312", "sail_python_deps"], preflight=False)
    assert outcome.ok is False
    assert outcome.failed == ["python312"]
    assert "sail_python_deps" not in invocations


# ---------------------------------------------------------------------------
# Phase: extract
# ---------------------------------------------------------------------------

def test_run_extract_rejects_missing_dump(tmp_path) -> None:
    """If the dump path doesn't exist, the orchestrator must fail BEFORE
    spawning a subprocess — otherwise the wizard's extract.log fills with
    a useless 'NSP not found' from deep inside the extractor instead of
    the clean wizard-layer message."""
    outcome = run_extract(tmp_path / "no_such.nsp", verify_hash=False)
    assert outcome.ok is False
    assert outcome.maps_present is False


def test_run_extract_succeeds_when_subprocess_ok_and_hash_matches(
    monkeypatch, tmp_path,
) -> None:
    fake_dump = tmp_path / "smo.nsp"
    fake_dump.write_bytes(b"")

    monkeypatch.setattr(
        "_setup.build.run_extract_maps",
        lambda dump, **kw: _FakeBuildResult(ok=True, returncode=0),
    )
    monkeypatch.setattr("_setup.build.maps_ready", lambda: True)
    monkeypatch.setattr(
        "_setup.build.verify_map_hashes",
        lambda: [
            _FakeHashCheck("shine_map.json", "x" * 64, "x" * 64,
                           present=True, match=True),
            _FakeHashCheck("capture_map.json", "y" * 64, "y" * 64,
                           present=True, match=True),
        ],
    )
    outcome = run_extract(fake_dump)
    assert outcome.ok is True
    assert outcome.hash_ok is True
    assert outcome.maps_present is True
    # Two checks, both match.
    assert len(outcome.hash_checks) == 2


def test_run_extract_fails_on_hash_mismatch_even_when_subprocess_ok(
    monkeypatch, tmp_path,
) -> None:
    """Hash gate is the canonical "wrong dump version" signal — a
    successful extract that produces a mismatched fingerprint is a
    1.1.0+ patch / different game / corrupted dump and must NOT
    advance to the build step."""
    fake_dump = tmp_path / "smo.nsp"
    fake_dump.write_bytes(b"")
    monkeypatch.setattr(
        "_setup.build.run_extract_maps",
        lambda dump, **kw: _FakeBuildResult(ok=True, returncode=0),
    )
    monkeypatch.setattr("_setup.build.maps_ready", lambda: True)
    monkeypatch.setattr(
        "_setup.build.verify_map_hashes",
        lambda: [
            _FakeHashCheck("shine_map.json", "x" * 64, "z" * 64,
                           present=True, match=False),
        ],
    )
    outcome = run_extract(fake_dump)
    assert outcome.ok is False
    assert outcome.hash_ok is False
    assert outcome.maps_present is True


def test_run_extract_fails_when_subprocess_returns_zero_but_maps_missing(
    monkeypatch, tmp_path,
) -> None:
    """Windows `os.execv` quirk: the extractor's bootstrap can return 0
    from the parent even when the re-launched child failed. Belt-and-
    braces here closes that hole — no maps means no extract, regardless
    of returncode."""
    fake_dump = tmp_path / "smo.nsp"
    fake_dump.write_bytes(b"")
    monkeypatch.setattr(
        "_setup.build.run_extract_maps",
        lambda dump, **kw: _FakeBuildResult(ok=True, returncode=0),
    )
    monkeypatch.setattr("_setup.build.maps_ready", lambda: False)
    outcome = run_extract(fake_dump, verify_hash=False)
    assert outcome.ok is False
    assert outcome.maps_present is False


def test_run_extract_short_circuits_when_maps_already_hash_correctly(
    monkeypatch, tmp_path,
) -> None:
    """Fast path: when canonical maps already exist and every hash
    matches, run_extract skips the (slow) extractor subprocess entirely
    and returns success without touching dump_path. This is what lets
    the wizard's NSP page accept a missing/stale dump for users who
    already have valid extracted maps from a prior install."""
    invocations: list[Any] = []

    def fake_run(dump, **kw):
        invocations.append(dump)
        return _FakeBuildResult(ok=True, returncode=0)

    monkeypatch.setattr("_setup.build.run_extract_maps", fake_run)
    monkeypatch.setattr("_setup.build.maps_ready", lambda: True)
    monkeypatch.setattr(
        "_setup.build.verify_map_hashes",
        lambda: [
            _FakeHashCheck("shine_map.json", "x" * 64, "x" * 64,
                           present=True, match=True),
            _FakeHashCheck("capture_map.json", "y" * 64, "y" * 64,
                           present=True, match=True),
        ],
    )
    # dump_path doesn't exist — the short-circuit must fire BEFORE the
    # dump_path validation, otherwise a re-run with stale state would
    # spuriously fail.
    outcome = run_extract(tmp_path / "no_such.nsp")
    assert outcome.ok is True
    assert outcome.hash_ok is True
    assert outcome.maps_present is True
    assert len(outcome.hash_checks) == 2
    assert invocations == [], (
        "subprocess must not be spawned when canonical maps already match"
    )


def test_run_extract_short_circuit_disabled_when_verify_hash_false(
    monkeypatch, tmp_path,
) -> None:
    """`verify_hash=False` is the opt-out path used by tests against
    synthetic data — it must NOT inherit the short-circuit, otherwise
    we'd silently skip extraction on stale leftover maps under a test
    that explicitly wanted to exercise the subprocess path."""
    fake_dump = tmp_path / "smo.nsp"
    fake_dump.write_bytes(b"")
    invocations: list[Any] = []

    def fake_run(dump, **kw):
        invocations.append(dump)
        return _FakeBuildResult(ok=True, returncode=0)

    monkeypatch.setattr("_setup.build.run_extract_maps", fake_run)
    monkeypatch.setattr("_setup.build.maps_ready", lambda: True)
    monkeypatch.setattr(
        "_setup.build.verify_map_hashes",
        lambda: [
            _FakeHashCheck("shine_map.json", "x" * 64, "x" * 64,
                           present=True, match=True),
        ],
    )
    outcome = run_extract(fake_dump, verify_hash=False)
    assert outcome.ok is True
    assert invocations == [fake_dump], (
        "verify_hash=False must run the subprocess regardless of map state"
    )


# ---------------------------------------------------------------------------
# Phase: build
# ---------------------------------------------------------------------------

def test_run_build_runs_sync_then_compile_then_collect_in_order(
    monkeypatch, tmp_path,
) -> None:
    """sync_capture_table + sync_shine_table generate the headers
    build_switchmod compiles against. Run them out of order and the
    compile fails with a less actionable error (or worse, succeeds
    against a stale header). Both sync steps must precede the compile."""
    order: list[str] = []

    def fake_sync(on_line=None):
        order.append("sync_capture")
        return _FakeBuildResult(ok=True)

    def fake_sync_shine(on_line=None):
        order.append("sync_shine")
        return _FakeBuildResult(ok=True)

    def fake_build(host, on_line=None):
        order.append("build")
        return _FakeBuildResult(ok=True)

    def fake_collect():
        order.append("collect")
        return {"subsdk9": tmp_path / "subsdk9",
                "main.npdm": tmp_path / "main.npdm"}

    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table", fake_sync
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table", fake_sync_shine
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod", fake_build
    )
    monkeypatch.setattr(
        "_setup.build.collect_build_outputs", fake_collect
    )
    outcome = run_build("10.0.0.5")
    assert outcome.ok is True
    assert order == ["sync_capture", "sync_shine", "build", "collect"]
    assert set(outcome.outputs) == {"subsdk9", "main.npdm"}


def test_run_build_stops_when_sync_fails(monkeypatch) -> None:
    """A failed sync produces a missing/stale capture_table.h. The
    compile would fail with a header-not-found error two minutes later;
    surface the sync failure now."""
    ran: list[str] = []
    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table",
        lambda on_line=None: (
            ran.append("sync_capture")
            or _FakeBuildResult(ok=False, returncode=2)
        ),
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table",
        lambda on_line=None: (
            ran.append("sync_shine")
            or _FakeBuildResult(ok=True)
        ),
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod",
        lambda host, on_line=None: (
            ran.append("build")
            or _FakeBuildResult(ok=True)
        ),
    )
    monkeypatch.setattr(
        "_setup.build.collect_build_outputs", lambda: {}
    )
    outcome = run_build("10.0.0.5")
    assert outcome.ok is False
    assert "build" not in ran
    # sync_capture failing must short-circuit BEFORE sync_shine — wasted
    # work otherwise, and lets a later sync failure mask the real one.
    assert "sync_shine" not in ran


def test_run_build_stops_when_sync_shine_fails(monkeypatch) -> None:
    """Same guarantee for sync_shine_table as sync_capture_table —
    a stale/missing shine_table.h fails the compile too (SaveLoadHook,
    MoonGetHook, shine_lookup all #include it)."""
    ran: list[str] = []
    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table",
        lambda on_line=None: (
            ran.append("sync_capture")
            or _FakeBuildResult(ok=True)
        ),
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table",
        lambda on_line=None: (
            ran.append("sync_shine")
            or _FakeBuildResult(ok=False, returncode=3)
        ),
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod",
        lambda host, on_line=None: (
            ran.append("build")
            or _FakeBuildResult(ok=True)
        ),
    )
    monkeypatch.setattr(
        "_setup.build.collect_build_outputs", lambda: {}
    )
    outcome = run_build("10.0.0.5")
    assert outcome.ok is False
    assert ran == ["sync_capture", "sync_shine"]


def _stub_all_resolver_caches(monkeypatch, *, populated: bool) -> None:
    """Set every resolved-bin cache `run_build` checks to either a
    populated dummy value or the unset sentinel. Centralized so the
    next person adding a SMOAP_*_BIN slot only updates one place."""
    monkeypatch.setattr(
        "_setup.prereqs._resolved_python312_bin",
        r"C:/Python312" if populated else None,
        raising=False,
    )
    monkeypatch.setattr(
        "_setup.prereqs._resolved_ninja_bin",
        r"C:/Ninja" if populated else None,
        raising=False,
    )
    monkeypatch.setattr(
        "_setup.prereqs._resolved_llvm_bin",
        r"C:/LLVM/bin" if populated else None,
        raising=False,
    )
    monkeypatch.setattr(
        "_setup.prereqs._resolved_mingw_bin",
        r"C:/WinLibs/bin" if populated else None,
        raising=False,
    )
    # _resolved_cmake's bare-name sentinel "cmake" counts as unset for
    # the env-var-set check; a real path counts as populated.
    monkeypatch.setattr(
        "_setup.prereqs._resolved_cmake",
        r"C:/CMake/bin/cmake.exe" if populated else None,
        raising=False,
    )


def test_run_build_prewarms_check_all_when_any_resolver_unset(
    monkeypatch, tmp_path,
) -> None:
    """Mirror of upstream commits c9a3a54 + 1bec0e0: every resolved-bin
    cache the build subprocess consumes has to be aligned with the
    wizard detector that populated it. The Kivy wizard's page order
    pre-populates all five via the prereq page; wizard_cli's
    `--phases build` (alone) doesn't have that. The prewarm here calls
    check_all so EVERY SMOAP_*_BIN env var the build needs gets pinned
    -- not just python312 (PR #169) but also ninja, cmake, llvm, mingw
    (PR #171). Without the broader prewarm, build_switchmod.py's
    hardcoded defaults fire and PR #171's literal-username Ninja path
    breaks for any non-dev user."""
    _stub_all_resolver_caches(monkeypatch, populated=False)
    called: list[str] = []

    def fake_check_all(**kwargs):
        called.append("check_all")
        # Pretend every detector flipped its cache green so the prewarm
        # is idempotent on a second hypothetical call.
        _stub_all_resolver_caches(monkeypatch, populated=True)
        return []
    monkeypatch.setattr("_setup.prereqs.check_all", fake_check_all)
    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod",
        lambda host, on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.collect_build_outputs",
        lambda: {"subsdk9": tmp_path / "subsdk9",
                 "main.npdm": tmp_path / "main.npdm"},
    )
    outcome = run_build("10.0.0.5")
    assert outcome.ok is True
    assert called == ["check_all"], (
        "run_build must call check_all when any SMOAP_*_BIN cache is "
        "empty -- otherwise the build subprocess hits broken hardcoded "
        "defaults (literal-username Ninja path, wrong Python, etc.) "
        "from PR #171's audit"
    )


def test_run_build_skips_prewarm_when_all_resolver_caches_populated(
    monkeypatch, tmp_path,
) -> None:
    """The prewarm must be idempotent: when `run_probe` ran first (the
    common all-phases path), every resolved-bin cache is already set
    and `run_build` should NOT re-detect (would waste ~1s of subprocess
    work per build iteration)."""
    _stub_all_resolver_caches(monkeypatch, populated=True)
    called: list[str] = []
    monkeypatch.setattr(
        "_setup.prereqs.check_all",
        lambda **kwargs: called.append("check_all") or [],
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod",
        lambda host, on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.collect_build_outputs",
        lambda: {"subsdk9": tmp_path / "subsdk9",
                 "main.npdm": tmp_path / "main.npdm"},
    )
    run_build("10.0.0.5")
    assert called == [], "prewarm should be a no-op when caches are populated"


@pytest.mark.parametrize("missing_cache", [
    "_resolved_python312_bin",
    "_resolved_ninja_bin",
    "_resolved_llvm_bin",
    "_resolved_mingw_bin",
])
def test_run_build_prewarms_when_any_single_cache_is_empty(
    monkeypatch, tmp_path, missing_cache,
) -> None:
    """Any one unset cache should trigger the prewarm — not just
    python312. Parameterized so the next person adding a SMOAP_*_BIN
    slot must wire the cache name into the prewarm guard or this test
    fails for that slot."""
    _stub_all_resolver_caches(monkeypatch, populated=True)
    monkeypatch.setattr(
        f"_setup.prereqs.{missing_cache}", None, raising=False,
    )
    called: list[str] = []
    monkeypatch.setattr(
        "_setup.prereqs.check_all",
        lambda **kwargs: called.append("check_all") or [],
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod",
        lambda host, on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.collect_build_outputs",
        lambda: {"subsdk9": tmp_path / "subsdk9",
                 "main.npdm": tmp_path / "main.npdm"},
    )
    run_build("10.0.0.5")
    assert called == ["check_all"], (
        f"unsetting {missing_cache} must trigger the prewarm so its "
        f"matching SMOAP_*_BIN env var gets pinned"
    )


def test_run_build_prewarms_when_cmake_is_bare_name_sentinel(
    monkeypatch, tmp_path,
) -> None:
    """`_resolved_cmake` defaults to the bare-name `"cmake"` sentinel
    (per `prereqs.resolved_cmake`'s fallback) when detection never ran.
    That sentinel is not a real path, so the prewarm guard must treat
    it the same as None for the cmake slot. Otherwise SMOAP_CMAKE_BIN
    stays unset and PR #171's cmake fix loses its protection."""
    _stub_all_resolver_caches(monkeypatch, populated=True)
    # Flip cmake to the bare-name sentinel.
    monkeypatch.setattr(
        "_setup.prereqs._resolved_cmake", None, raising=False,
    )
    called: list[str] = []
    monkeypatch.setattr(
        "_setup.prereqs.check_all",
        lambda **kwargs: called.append("check_all") or [],
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod",
        lambda host, on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.collect_build_outputs",
        lambda: {"subsdk9": tmp_path / "subsdk9",
                 "main.npdm": tmp_path / "main.npdm"},
    )
    run_build("10.0.0.5")
    assert called == ["check_all"]


def test_run_build_fails_when_outputs_missing_after_zero_exit(
    monkeypatch, tmp_path,
) -> None:
    """A build that returns 0 but produces no subsdk9 / main.npdm is
    treated as a failure regardless of returncode — same belt-and-
    braces pattern as extract."""
    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table",
        lambda on_line=None: _FakeBuildResult(ok=True),
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod",
        lambda host, on_line=None: _FakeBuildResult(ok=True),
    )

    def _no_outputs():
        raise FileNotFoundError("build did not produce expected outputs")

    monkeypatch.setattr(
        "_setup.build.collect_build_outputs", _no_outputs
    )
    outcome = run_build("10.0.0.5")
    assert outcome.ok is False


# ---------------------------------------------------------------------------
# Phase: deploy
# ---------------------------------------------------------------------------

def test_run_deploy_none_target_short_circuits_ok(tmp_path) -> None:
    """The 'none' target is the explicit CI bypass — exists so a CI
    job can exercise probe -> extract -> build without needing a
    Ryujinx folder or SD card mounted."""
    outcome = run_deploy("none", None, {})
    assert outcome.ok is True
    assert "skipped" in outcome.target.lower()


def test_run_deploy_sd_requires_explicit_path(tmp_path) -> None:
    outcome = run_deploy("sd", None, {})
    assert outcome.ok is False
    assert "--deploy-path is required" in outcome.error


def test_run_deploy_custom_requires_explicit_path() -> None:
    outcome = run_deploy("custom", None, {})
    assert outcome.ok is False
    assert "--deploy-path is required" in outcome.error


def test_run_deploy_custom_rejects_missing_parent(tmp_path) -> None:
    """A typo'd custom path like `C:/totally/made/up/folder` must not
    silently materialize four nested directories. The parent has to
    exist; only the leaf is created. Mirrors the Kivy wizard's
    pre-refactor check so the GUI and CLI accept the same paths."""
    bogus = tmp_path / "does_not_exist" / "child"
    outcome = run_deploy("custom", bogus, {"subsdk9": tmp_path / "x"})
    assert outcome.ok is False
    assert "parent does not exist" in outcome.error
    # Critical: nothing got created.
    assert not bogus.exists()
    assert not bogus.parent.exists()


def test_run_deploy_ryujinx_uses_autodetect_when_path_omitted(
    monkeypatch, tmp_path,
) -> None:
    """Ryujinx has a canonical AppData location; CLI omission must
    fall back to it, not error. SD/custom can't (drive letters and
    folders are user-chosen)."""
    auto = tmp_path / "Ryujinx"
    auto.mkdir()
    monkeypatch.setattr(
        "_setup.deploy.detect_ryujinx_path", lambda: auto,
    )
    captured: dict[str, Any] = {}

    def fake_deploy(root, outputs):
        captured["root"] = root
        return _FakeDeployResult(
            ok=True, target=f"Ryujinx at {root}",
            files=[(Path("a"), Path("b"))],
        )

    monkeypatch.setattr(
        "_setup.deploy.deploy_to_ryujinx", fake_deploy
    )
    outcome = run_deploy("ryujinx", None, {"subsdk9": Path("x")})
    assert outcome.ok is True
    assert captured["root"] == auto


def test_run_deploy_unknown_target_returns_clear_error() -> None:
    """A typo / future-target slip should produce a wizard-layer error,
    not crash deep inside deploy.py with a KeyError."""
    outcome = run_deploy("usb", Path("/tmp"), {})
    assert outcome.ok is False
    assert "unknown deploy target" in outcome.error


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

def _stub_all_primitives(monkeypatch, tmp_path: Path, *, prereqs_ok=True,
                         extract_ok=True, build_ok=True, deploy_ok=True) -> None:
    """Wire every primitive wizard_cli touches to a controllable fake.
    Each test that exercises run_pipeline calls this then tweaks the
    one piece it cares about, keeping the asserts focused."""
    monkeypatch.setattr(
        "_setup.prereqs.check_all",
        lambda **kw: [_FakePrereq(key="cmake", name="cmake", ok=prereqs_ok)],
    )
    monkeypatch.setattr(
        "_setup.prereqs.all_ok", lambda rs: all(r.ok for r in rs)
    )
    monkeypatch.setattr(
        "_setup.build.run_extract_maps",
        lambda dump, **kw: _FakeBuildResult(ok=extract_ok),
    )
    monkeypatch.setattr("_setup.build.maps_ready", lambda: extract_ok)
    monkeypatch.setattr(
        "_setup.build.verify_map_hashes",
        lambda: [
            _FakeHashCheck("shine_map.json", "x" * 64,
                           ("x" if extract_ok else "z") * 64,
                           present=True, match=extract_ok),
        ],
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_capture_table",
        lambda on_line=None: _FakeBuildResult(ok=build_ok),
    )
    monkeypatch.setattr(
        "_setup.build.run_sync_shine_table",
        lambda on_line=None: _FakeBuildResult(ok=build_ok),
    )
    monkeypatch.setattr(
        "_setup.build.run_build_switchmod",
        lambda host, on_line=None: _FakeBuildResult(ok=build_ok),
    )

    sub = tmp_path / "subsdk9"
    npdm = tmp_path / "main.npdm"
    sub.write_bytes(b"")
    npdm.write_bytes(b"")

    def fake_collect():
        if not build_ok:
            raise FileNotFoundError("build outputs missing")
        return {"subsdk9": sub, "main.npdm": npdm}

    monkeypatch.setattr(
        "_setup.build.collect_build_outputs", fake_collect
    )
    monkeypatch.setattr(
        "_setup.deploy.detect_ryujinx_path",
        lambda: tmp_path / "Ryujinx",
    )

    def fake_ryu(root, outputs):
        return _FakeDeployResult(
            ok=deploy_ok,
            target=f"Ryujinx at {root}",
            files=[(Path("a"), Path("b"))] if deploy_ok else [],
            error="" if deploy_ok else "fake deploy failure",
        )

    monkeypatch.setattr(
        "_setup.deploy.deploy_to_ryujinx", fake_ryu
    )
    monkeypatch.setattr(
        "_setup.net.detect_lan_ip", lambda: "192.0.2.5"
    )


def test_run_pipeline_happy_path_runs_all_phases(monkeypatch, tmp_path) -> None:
    """End-to-end smoke. Every phase succeeds; orchestrator reports all
    five phases ran and final ok=True."""
    _stub_all_primitives(monkeypatch, tmp_path)
    (tmp_path / "Ryujinx").mkdir()
    dump = tmp_path / "smo.nsp"
    dump.write_bytes(b"")

    opts = PipelineOptions(
        phases=ALL_PHASES,
        dump_path=dump,
        bridge_host="10.0.0.1",
        deploy_target="ryujinx",
        deploy_path=tmp_path / "Ryujinx",
    )
    sink, cb = _collect_events()
    outcome = run_pipeline(opts, callback=cb)
    # The install phase is naturally skipped because prereqs are OK.
    assert outcome.ok is True
    assert outcome.phases_run == ["probe", "extract", "build", "deploy"]
    # Pipeline start + end events present and consistent.
    starts = [e for e in sink if e["event"] == "pipeline_start"]
    ends = [e for e in sink if e["event"] == "pipeline_end"]
    assert len(starts) == 1 and len(ends) == 1
    assert ends[0]["ok"] is True


def test_run_pipeline_fail_fast_on_probe_when_install_not_authorized(
    monkeypatch, tmp_path,
) -> None:
    """If prereqs are missing AND --auto-install is off, the pipeline
    must stop before extract — running the extractor against a broken
    toolchain just wastes minutes producing a less actionable error."""
    _stub_all_primitives(monkeypatch, tmp_path, prereqs_ok=False)
    opts = PipelineOptions(
        phases=ALL_PHASES,
        dump_path=tmp_path / "smo.nsp",
        deploy_target="none",
        install_missing=False,
    )
    outcome = run_pipeline(opts)
    assert outcome.ok is False
    assert outcome.failed_phase == "probe"
    # Extract/build/deploy must not have run.
    assert outcome.extract is None
    assert outcome.build is None
    assert outcome.deploy is None


def test_run_pipeline_skips_install_when_no_missing_keys(
    monkeypatch, tmp_path,
) -> None:
    """A clean machine — prereqs all green — should see install phase
    emit a `phase_skip` event with a reason, not silently run zero
    installers. Distinguishable from `--auto-install not set` is what
    a CI dashboard depends on."""
    _stub_all_primitives(monkeypatch, tmp_path, prereqs_ok=True)
    (tmp_path / "Ryujinx").mkdir()
    dump = tmp_path / "smo.nsp"
    dump.write_bytes(b"")
    opts = PipelineOptions(
        phases=ALL_PHASES,
        dump_path=dump,
        deploy_target="ryujinx",
        install_missing=True,  # opted in, but nothing to do
    )
    sink, cb = _collect_events()
    outcome = run_pipeline(opts, callback=cb)
    assert outcome.ok is True
    skips = [e for e in sink
             if e["event"] == "phase_skip" and e.get("phase") == "install"]
    assert skips, "expected a phase_skip event for install"
    assert "no missing" in skips[0]["reason"]


def test_run_pipeline_subset_only_extract(monkeypatch, tmp_path) -> None:
    """`--phases extract` is the canonical "fast iteration after a fresh
    dump" path. It must run extract WITHOUT requiring deploy_target or
    build outputs to be valid."""
    _stub_all_primitives(monkeypatch, tmp_path)
    dump = tmp_path / "smo.nsp"
    dump.write_bytes(b"")
    opts = PipelineOptions(
        phases=("extract",),
        dump_path=dump,
        verify_hash=True,
    )
    outcome = run_pipeline(opts)
    assert outcome.ok is True
    assert outcome.phases_run == ["extract"]
    assert outcome.build is None and outcome.deploy is None


def test_run_pipeline_extract_without_dump_fails_cleanly(
    monkeypatch, tmp_path,
) -> None:
    """Extract phase needs a dump. Missing the arg should fail at the
    pipeline layer with a clear error, not crash inside run_extract
    with an AttributeError on None.is_file()."""
    _stub_all_primitives(monkeypatch, tmp_path)
    opts = PipelineOptions(phases=("extract",), dump_path=None)
    outcome = run_pipeline(opts)
    assert outcome.ok is False
    assert outcome.failed_phase == "extract"


def test_run_pipeline_deploy_phase_alone_uses_existing_build_outputs(
    monkeypatch, tmp_path,
) -> None:
    """If a previous run already produced subsdk9 / main.npdm, a
    deploy-only pipeline run should pick them up — useful for
    "redeploy to a different SD card" without re-cross-compiling."""
    _stub_all_primitives(monkeypatch, tmp_path)
    (tmp_path / "Ryujinx").mkdir()
    opts = PipelineOptions(
        phases=("deploy",),
        deploy_target="ryujinx",
        deploy_path=tmp_path / "Ryujinx",
    )
    outcome = run_pipeline(opts)
    assert outcome.ok is True
    assert outcome.phases_run == ["deploy"]


def test_run_pipeline_deploy_phase_fails_when_no_build_outputs(
    monkeypatch, tmp_path,
) -> None:
    """Deploy with no prior build artifacts should fail cleanly at the
    pipeline layer, not crash inside _copy_files with a 'source
    unreadable' error."""
    _stub_all_primitives(monkeypatch, tmp_path, build_ok=False)
    opts = PipelineOptions(
        phases=("deploy",),
        deploy_target="ryujinx",
        deploy_path=tmp_path / "Ryujinx",
    )
    outcome = run_pipeline(opts)
    assert outcome.ok is False
    assert outcome.failed_phase == "deploy"


def test_run_pipeline_emits_pipeline_start_and_end_events(
    monkeypatch, tmp_path,
) -> None:
    """`pipeline_start` + `pipeline_end` bracket every run so a CI tail
    can correlate runs across concurrent invocations."""
    _stub_all_primitives(monkeypatch, tmp_path)
    sink, cb = _collect_events()
    opts = PipelineOptions(phases=("probe",))
    run_pipeline(opts, callback=cb)
    starts = [e for e in sink if e["event"] == "pipeline_start"]
    ends = [e for e in sink if e["event"] == "pipeline_end"]
    assert len(starts) == 1
    assert len(ends) == 1
    # ts is non-negative and monotone.
    assert starts[0]["ts"] >= 0
    assert ends[0]["ts"] >= starts[0]["ts"]


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def test_main_exits_2_on_unknown_phase(capsys) -> None:
    rc = wizard_cli.main(["--phases", "probe,bogus"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown phase" in err


def test_main_json_events_outputs_parseable_jsonl(monkeypatch, capsys) -> None:
    """End-to-end smoke for `--json-events`: every line of stdout MUST
    parse as a standalone JSON object. A CI pipeline tailing this
    stream cannot tolerate a non-JSON line."""
    monkeypatch.setattr(
        "_setup.prereqs.check_all",
        lambda **kw: [_FakePrereq(key="cmake", name="cmake", ok=True)],
    )
    monkeypatch.setattr(
        "_setup.prereqs.all_ok", lambda rs: all(r.ok for r in rs),
    )
    rc = wizard_cli.main(["--json-events", "--phases", "probe"])
    out = capsys.readouterr().out
    assert rc == 0
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert lines, "expected at least one JSON event"
    for ln in lines:
        json.loads(ln)  # raises ValueError on a non-JSON line


def test_main_text_mode_renders_without_json_dict_repr(
    monkeypatch, capsys,
) -> None:
    """Default mode (no --json-events) must emit human-readable lines,
    not JSON. Confirms the CLI doesn't silently flip a default."""
    monkeypatch.setattr(
        "_setup.prereqs.check_all",
        lambda **kw: [_FakePrereq(key="cmake", name="cmake", ok=True)],
    )
    monkeypatch.setattr(
        "_setup.prereqs.all_ok", lambda rs: True,
    )
    rc = wizard_cli.main(["--phases", "probe"])
    out = capsys.readouterr().out
    assert rc == 0
    # No line should be a valid JSON object — text mode renders prefixes.
    for ln in out.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("{") and ln.endswith("}"):
            pytest.fail(f"text mode emitted JSON: {ln!r}")
