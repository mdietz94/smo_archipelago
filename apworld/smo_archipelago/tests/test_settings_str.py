"""Tests for `client.main._settings_str`.

Reading a `settings.UserFilePath` attribute (e.g. `shine_map_path`) triggers
the host's path validation. On some Archipelago forks — notably MultiworldGG —
an empty/relative path is resolved against the install root and the *attribute
access itself* raises `FileNotFoundError` when that root doesn't exist on disk
(observed in the wild: `C:\\Program Files\\MultiworldGG\\`). `getattr`'s
`default=` argument only swallows `AttributeError`, so that error used to
propagate all the way out of `main()` and crash the SMOClient subprocess
bootstrap with a launch-crash popup before the GUI ever opened.

`_settings_str` treats any read failure as "unset" so the bundled
`client/data/` map default wins instead of taking down the client.

`client.main` imports `Utils` / `CommonClient` at module scope, which aren't on
sys.path for the default suite (conftest keeps vendor/Archipelago off it). We
stub those modules so this pure-function regression is covered without an
Archipelago checkout.
"""

from __future__ import annotations

import sys
import types

import pytest


class _Permissive(type):
    """Metaclass so any attribute access on a stub class yields another
    stub (covers enum-style `ClientStatus.CLIENT_GOAL` access)."""

    def __getattr__(cls, name):  # noqa: N805
        return _make(name)


def _make(name: str = "Stub"):
    """A stub that is simultaneously a usable base class, a callable, and
    truthy — enough to satisfy `class X(CommonContext)`, `get_base_parser()`,
    and `if gui_enabled:` all at once."""
    return _Permissive(name, (), {})


class _AutoModule(types.ModuleType):
    """Module whose every missing attribute resolves to a permissive stub."""

    def __getattr__(self, name):
        return _make(name)


@pytest.fixture
def settings_str():
    """Import `_settings_str` with the heavy AP imports stubbed out."""
    created: list[str] = []
    for name in ("Utils", "CommonClient", "NetUtils"):
        if name not in sys.modules:
            sys.modules[name] = _AutoModule(name)
            created.append(name)
    # Snapshot + evict any client.* modules so the stubs take effect for the
    # whole import chain (context → NetUtils, etc.). Restore on teardown so a
    # stub-bound client module never leaks into a later test that needs the
    # real CommonClient.
    client_snapshot = {
        m: sys.modules[m]
        for m in list(sys.modules)
        if m == "client" or m.startswith("client.")
    }
    if created:
        for m in client_snapshot:
            sys.modules.pop(m, None)
    try:
        from client.main import _settings_str  # type: ignore[import-not-found]
        yield _settings_str
    finally:
        for name in created:
            sys.modules.pop(name, None)
        # Drop anything imported under the stubs, then restore the originals.
        for m in [m for m in sys.modules if m == "client" or m.startswith("client.")]:
            sys.modules.pop(m, None)
        sys.modules.update(client_snapshot)


class _Settings:
    """Minimal stand-in for an SMOSettings group."""

    switch_listen_host = "0.0.0.0"

    def __init__(self, **values):
        self._values = values

    def __getattr__(self, name):
        if name in self._values:
            val = self._values[name]
            if isinstance(val, Exception):
                raise val
            return val
        raise AttributeError(name)


def test_returns_plain_value(settings_str) -> None:
    s = _Settings(shine_map_path="/tmp/shine.json")
    assert settings_str(s, "shine_map_path") == "/tmp/shine.json"


def test_missing_attribute_returns_empty(settings_str) -> None:
    """AttributeError → "" (the historical getattr default behavior)."""
    assert settings_str(_Settings(), "shine_map_path") == ""


def test_filenotfound_on_access_returns_empty(settings_str) -> None:
    """The MultiworldGG crash: attribute access raises FileNotFoundError
    because the empty path resolved against a nonexistent install root.
    Must be swallowed so the bundled default wins instead of crashing."""
    s = _Settings(
        shine_map_path=FileNotFoundError(
            2, "No such file or directory", "C:\\Program Files\\MultiworldGG\\"
        )
    )
    assert settings_str(s, "shine_map_path") == ""


def test_arbitrary_exception_returns_empty(settings_str) -> None:
    """Forks differ in which exception type validation raises — any of
    them must degrade to the default, never propagate."""
    s = _Settings(capture_map_path=RuntimeError("validation blew up"))
    assert settings_str(s, "capture_map_path") == ""


def test_empty_string_value_returns_empty(settings_str) -> None:
    assert settings_str(_Settings(shine_map_path=""), "shine_map_path") == ""
