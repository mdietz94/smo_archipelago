"""Tests for the SMOAP_APPDATA_ROOT override in `_setup.appdata_root()`.

The audit harness (scripts/local_release_audit.ps1) sets this env var
so the audit runs against a tempdir instead of the user's real
%APPDATA%/SMOArchipelago/. A regression here would re-introduce the
risk of an audit destroying user state — lock the contract.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from _setup import appdata_root
from _setup.prereqs import localappdata_tools_root


def test_appdata_root_honors_override_env_var(tmp_path: Path, monkeypatch) -> None:
    """SMOAP_APPDATA_ROOT overrides both APPDATA and the POSIX fallback.
    Used by the harness to sandbox the audit into a tempdir."""
    override = tmp_path / "sandbox"
    monkeypatch.setenv("SMOAP_APPDATA_ROOT", str(override))
    # Also set APPDATA so we'd notice if precedence is wrong.
    monkeypatch.setenv("APPDATA", str(tmp_path / "should_not_be_used"))

    result = appdata_root()

    assert result == override
    assert override.exists() and override.is_dir()
    assert not (tmp_path / "should_not_be_used").exists()


def test_appdata_root_falls_back_to_appdata_without_override(
    tmp_path: Path, monkeypatch
) -> None:
    """Without SMOAP_APPDATA_ROOT, the wizard's normal %APPDATA% path is
    used — that's the production contract."""
    monkeypatch.delenv("SMOAP_APPDATA_ROOT", raising=False)
    appdata_base = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata_base))

    result = appdata_root()

    assert result == appdata_base / "SMOArchipelago"
    assert result.exists()


def test_appdata_root_empty_override_falls_through(
    tmp_path: Path, monkeypatch
) -> None:
    """An empty SMOAP_APPDATA_ROOT should be treated like unset — otherwise
    a clearing-without-deleting harness bug would silently route into
    CWD/SMOArchipelago and surprise the user."""
    monkeypatch.setenv("SMOAP_APPDATA_ROOT", "")
    appdata_base = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata_base))

    result = appdata_root()

    assert result == appdata_base / "SMOArchipelago"


def test_localappdata_root_honors_override_env_var(
    tmp_path: Path, monkeypatch
) -> None:
    """SMOAP_LOCALAPPDATA_ROOT overrides LOCALAPPDATA. Used by the live
    e2e wizard test to sandbox LLVM + WinLibs installs."""
    override = tmp_path / "localappdata_sandbox"
    monkeypatch.setenv("SMOAP_LOCALAPPDATA_ROOT", str(override))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "should_not_be_used"))

    result = localappdata_tools_root()

    assert result == override
    assert not (tmp_path / "should_not_be_used").exists()


def test_localappdata_root_falls_back_to_localappdata(
    tmp_path: Path, monkeypatch
) -> None:
    """Without the override, the production %LOCALAPPDATA%/SMOArchipelago
    path is used."""
    monkeypatch.delenv("SMOAP_LOCALAPPDATA_ROOT", raising=False)
    localappdata_base = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_base))

    result = localappdata_tools_root()

    assert result == localappdata_base / "SMOArchipelago"
