"""Tests for `_setup.build._stream_subprocess` timeout enforcement.

A wedged child (hactool stuck on a bad NCA, ninja deadlocked, cmake
waiting on stdin) used to hang the wizard forever because the read
loop was `for raw in proc.stdout:` — a blocking iterator with no way
to interrupt it. The timeout machinery these tests pin:

  - `wall_timeout_s` — total wall-clock cap from spawn to exit
  - `stall_timeout_s` — max time without any stdout output

On timeout the child is SIGTERM'd, then SIGKILL'd after 5s of
non-cooperation, and the function returns BuildResult(ok=False,
returncode=TIMEOUT_RETURNCODE=124, log=...). Tests use the host
Python as a stand-in subprocess so they run cross-platform without
needing the cross-compile toolchain or hactool installed.
"""

from __future__ import annotations

import sys
import time

import pytest

from _setup.build import TIMEOUT_RETURNCODE, _stream_subprocess


def _py(*args: str) -> list[str]:
    """Spawn the host Python with -c so timeouts can be tested
    deterministically — sleep, print, exit."""
    return [sys.executable, "-c", *args]


def test_normal_completion_under_timeout() -> None:
    """Sanity: a child that prints + exits well before either timeout
    should report ok=True with returncode=0."""
    result = _stream_subprocess(
        _py("print('hello'); print('world')"),
        wall_timeout_s=30.0,
        stall_timeout_s=10.0,
    )
    assert result.ok
    assert result.returncode == 0
    assert "hello" in result.log
    assert "world" in result.log


def test_no_timeout_means_no_cap() -> None:
    """`None` for both timeouts must not kill a fast child — pins that
    the timeout machinery is opt-in."""
    result = _stream_subprocess(
        _py("print('done')"),
        wall_timeout_s=None,
        stall_timeout_s=None,
    )
    assert result.ok
    assert result.returncode == 0


def test_wall_timeout_kills_long_running_child() -> None:
    """A child that loops printing slowly past the wall-clock cap must
    be killed and surfaced as a timeout-class failure, not a hang."""
    start = time.monotonic()
    result = _stream_subprocess(
        _py(
            "import time, sys\n"
            "for i in range(30):\n"
            "    print(f'line {i}', flush=True)\n"
            "    time.sleep(0.5)\n"
        ),
        wall_timeout_s=2.0,
        stall_timeout_s=None,
    )
    elapsed = time.monotonic() - start
    assert not result.ok
    assert result.returncode == TIMEOUT_RETURNCODE
    assert "wall-clock timeout" in result.log
    # Should NOT take the full 15s the child would have run for; the
    # SIGTERM-then-SIGKILL path is bounded at wall + 10s grace.
    assert elapsed < 14.0, (
        f"timeout enforcement took {elapsed:.1f}s — "
        f"the kill path is too slow or not happening"
    )


def test_stall_timeout_kills_silent_child() -> None:
    """A child that produces no output for longer than the stall cap
    must be killed even if it's well under the wall-clock limit. This
    is the more useful of the two timeouts for long builds — `ninja`
    can be silent for minutes while a heavy compilation unit runs."""
    start = time.monotonic()
    result = _stream_subprocess(
        _py(
            "import time, sys\n"
            "print('starting', flush=True)\n"
            "time.sleep(30)\n"     # silence past stall cap
            "print('done', flush=True)\n"
        ),
        wall_timeout_s=60.0,
        stall_timeout_s=1.5,
    )
    elapsed = time.monotonic() - start
    assert not result.ok
    assert result.returncode == TIMEOUT_RETURNCODE
    assert "stall timeout" in result.log
    assert elapsed < 14.0, (
        f"stall timeout took {elapsed:.1f}s — kill path is too slow"
    )


def test_stall_resets_on_each_line() -> None:
    """A child that prints every 0.4s under a 1.5s stall cap should
    run to completion — the stall timer must reset on each line, not
    accumulate."""
    result = _stream_subprocess(
        _py(
            "import time, sys\n"
            "for i in range(5):\n"
            "    print(f'tick {i}', flush=True)\n"
            "    time.sleep(0.4)\n"
        ),
        wall_timeout_s=30.0,
        stall_timeout_s=1.5,
    )
    assert result.ok, f"stall reset broken — result: {result.log}"
    assert result.returncode == 0


def test_timeout_message_is_actionable() -> None:
    """The timeout log entry must say WHICH timeout fired and the
    configured threshold, so the wizard can render an explanation
    instead of an opaque exit code."""
    result = _stream_subprocess(
        _py("import time; time.sleep(10)"),
        wall_timeout_s=1.0,
    )
    assert not result.ok
    assert "wall-clock timeout" in result.log
    # Threshold value is included so the user can tell the timeout was
    # 1s vs 30 minutes when reading the log later.
    assert "1" in result.log


def test_kill_marker_appears_in_log() -> None:
    """The log must record that the wizard initiated the kill — useful
    when triaging "did the child crash or did the wizard time it out"
    from extract.log alone."""
    result = _stream_subprocess(
        _py("import time; time.sleep(10)"),
        wall_timeout_s=1.0,
    )
    assert "killing pid=" in result.log


# ---------------------------------------------------------------------------
# Per-step timeout configuration — pin that every run_* caller has both
# timeouts set, so a future caller can't accidentally regress to an
# unbounded subprocess by omitting the parameters.
# ---------------------------------------------------------------------------


def test_every_run_step_has_both_timeouts_configured(monkeypatch) -> None:
    """Each `run_*` function must pass BOTH wall_timeout_s and
    stall_timeout_s to _stream_subprocess. Catches the case where a new
    runner is added and someone forgets the timeout kwargs — the
    wizard would silently get a child that can hang forever.

    Post-Hakkun the build is driven by a single `run_build_switchmod`
    wrapper (replaces the old cmake_configure + cmake_build pair), so
    the runner count is 3 not 4. If a future change re-splits the build
    step or adds a new runner, this list needs to grow."""
    from _setup import build

    captured: list[dict] = []

    def fake_stream(cmd, *, cwd=None, env=None, on_line=None,
                    wall_timeout_s=None, stall_timeout_s=None):
        captured.append({
            "cmd": cmd,
            "wall": wall_timeout_s,
            "stall": stall_timeout_s,
        })
        from _setup.build import BuildResult
        return BuildResult(ok=True, returncode=0, log="")

    monkeypatch.setattr(build, "_stream_subprocess", fake_stream)
    from pathlib import Path
    monkeypatch.setattr(build, "bundled_script", lambda name: Path(name))
    monkeypatch.setattr(build, "bundled_data_file", lambda name: Path(name))
    monkeypatch.setattr(
        build, "bundled_switch_mod",
        lambda: Path("/fake/switch_mod"),
    )
    monkeypatch.setattr(build, "data_dir", lambda: Path("/fake/data"))
    monkeypatch.setattr(build, "build_dir", lambda: Path("/fake/build"))

    # Drive each runner. Each must emit one entry with both timeouts.
    build.run_sync_capture_table()
    build.run_build_switchmod("10.0.0.1")
    build.run_extract_maps(Path("fake.nsp"))

    assert len(captured) == 3
    for entry in captured:
        cmd_str = " ".join(str(c) for c in entry["cmd"])
        assert entry["wall"] is not None, (
            f"runner '{cmd_str}' has no wall timeout — could hang forever"
        )
        assert entry["stall"] is not None, (
            f"runner '{cmd_str}' has no stall timeout — could hang forever"
        )
        # Sanity bounds — anything over 2h is almost certainly a typo.
        assert 0 < entry["wall"] <= 7200, (
            f"runner '{cmd_str}' wall={entry['wall']} is out of range"
        )
        assert 0 < entry["stall"] <= entry["wall"], (
            f"runner '{cmd_str}' stall={entry['stall']} > wall={entry['wall']}"
        )
