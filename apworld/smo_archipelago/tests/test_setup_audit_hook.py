"""Tests for the subprocess.Popen audit hook installed by the wizard.

Exercises the hook by both synthesising audit events via ``sys.audit``
(fast + no real spawn) AND running a real ``subprocess.Popen`` (proves
Python actually fires the event for us in production).

The hook can't be uninstalled — ``sys.addaudithook`` is one-way. We
work around that with ``disable_audit_hook()`` between tests so one
case's strict mode doesn't leak into the next, and we keep the global
``sys.audit("subprocess.Popen", ...)`` firing out of every test so
ordering between this file and the rest of the suite doesn't matter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _setup import audit
from _setup.audit import (
    AuditViolation,
    add_allowed_prefix,
    current_allowlist,
    disable_audit_hook,
    install_audit_hook,
)


@pytest.fixture(autouse=True)
def _reset_hook(tmp_path: Path, monkeypatch):
    """Each test gets a fresh log file and a disabled hook on entry +
    exit, so cross-test ordering can't pollute. The hook itself stays
    registered with the interpreter; only the module's enabled flag
    toggles."""
    log_file = tmp_path / "exec-trace.log"
    # Clear any leaked state from prior tests.
    disable_audit_hook()
    # Wipe runtime-added prefixes so each test starts clean.
    audit._extra_prefixes.clear()  # type: ignore[attr-defined]
    yield log_file
    disable_audit_hook()
    audit._extra_prefixes.clear()  # type: ignore[attr-defined]


def _read_log(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_install_is_idempotent(tmp_path: Path):
    """Calling install twice doesn't register the hook twice, so we
    don't double-log every spawn."""
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=False)
    install_audit_hook(log_path=log, strict=False)
    sys.audit("subprocess.Popen", "/bin/ls", ["/bin/ls"], None, None)
    records = _read_log(log)
    assert len(records) == 1, f"expected 1 record, got {records}"


def test_log_records_executable_and_argv(tmp_path: Path):
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=False)
    sys.audit(
        "subprocess.Popen",
        r"C:\Tools\thing.exe",
        [r"C:\Tools\thing.exe", "--flag", "value"],
        r"C:\cwd",
        None,
    )
    records = _read_log(log)
    assert len(records) == 1
    rec = records[0]
    assert rec["argv"] == [r"C:\Tools\thing.exe", "--flag", "value"]
    assert rec["executable_raw"] == r"C:\Tools\thing.exe"
    assert rec["cwd"] == r"C:\cwd"
    assert rec["strict"] is False
    assert "ts" in rec


def test_disable_makes_hook_a_noop(tmp_path: Path):
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=False)
    disable_audit_hook()
    sys.audit("subprocess.Popen", "/bin/ls", ["/bin/ls"], None, None)
    assert not log.exists() or _read_log(log) == []


def test_real_subprocess_emits_event(tmp_path: Path):
    """Belt-and-braces: prove that an actual Popen — not just a
    synthesized audit event — flows through the hook. Spawns the
    current Python with a one-line no-op."""
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=False)
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc.wait(timeout=10)
    records = _read_log(log)
    assert any(
        rec["argv"][0] == sys.executable and rec["argv"][-1] == "pass"
        for rec in records
    ), f"no matching record in {records}"


def test_strict_blocks_unknown_executable(tmp_path: Path, monkeypatch):
    """An executable outside every allowed prefix must raise
    AuditViolation, which aborts the spawn before any process is
    created. Cleared %LOCALAPPDATA% + custom path so the default
    allowlist doesn't accidentally cover ``/tmp``."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=True)
    bogus = str(tmp_path / "elsewhere" / "evil.exe")
    with pytest.raises(AuditViolation):
        sys.audit("subprocess.Popen", bogus, [bogus, "--rm-rf"], None, None)
    records = _read_log(log)
    assert records, "violation should still be logged before raising"
    last = records[-1]
    assert last["allowlist_ok"] is False
    assert last["strict"] is True
    assert "allowlist_checked" in last


def test_strict_allows_current_python(tmp_path: Path):
    """``sys.executable`` is always implicitly allowed — without it,
    the wizard couldn't spawn any of its own helper Pythons."""
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=True)
    # No raise.
    sys.audit("subprocess.Popen", sys.executable, [sys.executable, "-V"], None, None)
    records = _read_log(log)
    assert records[-1]["allowlist_ok"] is True


def test_strict_allows_paths_under_vendored_prefix(tmp_path: Path, monkeypatch):
    """%LOCALAPPDATA%/SMOArchipelago/<anything> resolves under the
    static allowlist — covers the LLVM / WinLibs portable installs."""
    fake_local = tmp_path / "LocalAppData"
    vendored = fake_local / "SMOArchipelago" / "llvm-19" / "bin" / "clang.exe"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("")  # placeholder; resolve() needs the file to exist
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=True)
    sys.audit(
        "subprocess.Popen",
        str(vendored),
        [str(vendored), "--version"],
        None,
        None,
    )
    records = _read_log(log)
    assert records[-1]["allowlist_ok"] is True


def test_add_allowed_prefix_extends_allowlist(tmp_path: Path, monkeypatch):
    """Runtime additions (e.g. a custom Ryujinx folder the user picked
    in the wizard) are honored without re-installing the hook."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=True)
    custom = tmp_path / "custom-tool"
    custom.mkdir()
    exe = custom / "thing.exe"
    exe.write_text("")
    # Before adding: rejected.
    with pytest.raises(AuditViolation):
        sys.audit("subprocess.Popen", str(exe), [str(exe)], None, None)
    add_allowed_prefix(custom)
    # After: accepted.
    sys.audit("subprocess.Popen", str(exe), [str(exe)], None, None)
    records = _read_log(log)
    assert records[-1]["allowlist_ok"] is True


def test_env_var_drives_strict(tmp_path: Path, monkeypatch):
    """``SMOAP_AUDIT=strict`` flips strict mode on without an explicit
    kwarg — this is how CI activates the assertions."""
    monkeypatch.setenv("SMOAP_AUDIT", "strict")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log)
    with pytest.raises(AuditViolation):
        sys.audit(
            "subprocess.Popen",
            r"C:\Random\thing.exe",
            [r"C:\Random\thing.exe"],
            None,
            None,
        )


def test_env_var_default_is_log_only(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SMOAP_AUDIT", raising=False)
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log)
    sys.audit("subprocess.Popen", r"C:\Random\thing.exe",
              [r"C:\Random\thing.exe"], None, None)
    # No raise; record landed.
    records = _read_log(log)
    assert records and records[-1]["strict"] is False


def test_non_subprocess_events_are_ignored(tmp_path: Path):
    """Audit hooks see every event in the process; we only care about
    subprocess.Popen. Anything else is dropped on the floor."""
    log = tmp_path / "exec-trace.log"
    install_audit_hook(log_path=log, strict=True)
    # `open` is one of the noisiest audit events; this would flood the
    # log if the hook didn't filter.
    sys.audit("open", "/etc/hosts", "r", 0)
    sys.audit("import", "json", "", [], None)
    assert _read_log(log) == []


def test_current_allowlist_includes_static_prefixes(monkeypatch, tmp_path: Path):
    """Sanity-check the static prefixes the user spelled out in the
    requirement."""
    fake_local = tmp_path / "LocalAppData"
    fake_local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    items = [str(p) for p in current_allowlist()]
    assert any("SMOArchipelago" in s for s in items)
    assert any("Python312" in s for s in items)
    assert any(s.endswith("curl.exe") for s in items)
