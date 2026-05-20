"""Tests for `_setup.launcher_errors` — making swallowed launch-time
crashes visible to the user.

These exist because the original "click .meatballsap → nothing happened" report
turned out to be a `multiprocessing.Process.start()` that fired, crashed,
and exited with no console attached to print the traceback. The decorator
in launcher_errors.py is the only thing standing between a future crash
and the same silent failure mode.
"""

from __future__ import annotations

from _setup.launcher_errors import show_launch_error, visible_errors


def test_visible_errors_invokes_notifier_on_crash() -> None:
    notifications: list[tuple[str, str]] = []
    logs: list[str] = []

    @visible_errors("Test context")
    def crashes() -> None:
        raise RuntimeError("kaboom")

    # Patch the helpers used by the decorator's default `show_launch_error`
    # call — we don't want to actually pop a Tk window or write to %APPDATA%.
    import _setup.launcher_errors as le

    def fake_notifier(title: str, body: str) -> None:
        notifications.append((title, body))

    def fake_log_writer(text: str) -> str | None:
        logs.append(text)
        return "/tmp/fake-crash.log"

    orig_n = le._default_notifier
    orig_w = le._default_log_writer
    le._default_notifier = fake_notifier
    le._default_log_writer = fake_log_writer
    try:
        try:
            crashes()
        except RuntimeError:
            pass  # expected — decorator re-raises
        else:
            raise AssertionError("decorator must re-raise")
    finally:
        le._default_notifier = orig_n
        le._default_log_writer = orig_w

    assert len(notifications) == 1, "notifier must be called exactly once"
    title, body = notifications[0]
    assert "Test context" in title
    assert "kaboom" in body, "messagebox must include the actual error"
    assert "/tmp/fake-crash.log" in body, "messagebox must point to the log file"

    assert len(logs) == 1
    assert "Test context" in logs[0]
    assert "RuntimeError: kaboom" in logs[0]
    assert "Traceback" in logs[0], "log must include full traceback, not just message"


def test_visible_errors_passthrough_on_success() -> None:
    """Happy path: decorator is transparent when wrapped function succeeds."""
    @visible_errors("Test context")
    def ok(x: int, y: int) -> int:
        return x + y

    assert ok(2, 3) == 5


def test_show_launch_error_survives_failing_notifier() -> None:
    """If Tk itself is broken, the log file must still get written.
    The reverse also holds — but the log path is the more important channel
    because it persists past the crash."""
    logs: list[str] = []

    def broken_notifier(title: str, body: str) -> None:
        raise RuntimeError("no display")

    def fake_log_writer(text: str) -> str | None:
        logs.append(text)
        return "/tmp/x.log"

    try:
        raise ValueError("payload")
    except ValueError as e:
        show_launch_error(
            "Boot",
            e,
            notifier=broken_notifier,
            log_writer=fake_log_writer,
        )

    assert len(logs) == 1
    assert "ValueError: payload" in logs[0]


def test_show_launch_error_survives_failing_log_writer() -> None:
    """If the log dir is unwritable, the messagebox must still appear."""
    notifications: list[tuple[str, str]] = []

    def fake_notifier(title: str, body: str) -> None:
        notifications.append((title, body))

    def broken_log_writer(text: str) -> str | None:
        raise OSError("disk full")

    try:
        raise ValueError("payload")
    except ValueError as e:
        show_launch_error(
            "Boot",
            e,
            notifier=fake_notifier,
            log_writer=broken_log_writer,
        )

    assert len(notifications) == 1
    title, body = notifications[0]
    assert "Boot" in title
    assert "ValueError: payload" in body
    # No log_path_msg should appear since the writer raised before returning
    # a path. Don't assert on absence of "Full traceback written to" — the
    # important thing is the dialog appeared with the error in it.


def test_show_launch_error_trims_huge_tracebacks() -> None:
    """Tracebacks can be 50+ lines (Kivy import chains etc.). The dialog
    must keep its body under a reasonable size so the messagebox is
    actually readable on Windows (and so the OK button doesn't fall off
    the bottom of the screen)."""
    notifications: list[tuple[str, str]] = []

    def fake_notifier(title: str, body: str) -> None:
        notifications.append((title, body))

    # Synthesize a deep stack to get a long traceback
    def deep(n: int) -> None:
        if n == 0:
            raise RuntimeError("x" * 2000)
        deep(n - 1)

    try:
        deep(80)
    except RuntimeError as e:
        show_launch_error(
            "Boot",
            e,
            notifier=fake_notifier,
            log_writer=lambda _: None,
        )

    assert len(notifications) == 1
    _, body = notifications[0]
    # Body should be capped well under the 2000-char raw error message
    assert len(body) < 1500, f"messagebox body grew to {len(body)} chars; should be trimmed"
