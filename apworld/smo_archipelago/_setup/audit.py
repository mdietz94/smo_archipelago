"""Subprocess audit hook for the setup wizard.

Python's ``subprocess.Popen`` raises a ``sys.audit("subprocess.Popen", ...)``
event on every spawn. We register a hook at wizard entry that:

  * Appends a line-delimited JSON record (executable, argv, cwd, ts) to
    ``%APPDATA%/SMOArchipelago/exec-trace.log`` on every spawn. Always on,
    so a real user's "what did the installer actually run" question has
    an answer after the fact.
  * If ``SMOAP_AUDIT=strict`` (set in CI), resolves each executable to an
    absolute path and asserts it lives under one of the vendored
    prefixes — ``%LOCALAPPDATA%/SMOArchipelago``, the bundled CPython
    3.12 install, system ``curl.exe``, and the cmake/ninja dirs that the
    prereq detectors resolved earlier in the run. Anything else raises
    ``AuditViolation`` from inside the hook, which aborts ``Popen`` and
    surfaces as a CI failure with the offending argv in the log.

Re-entrancy and recursion are handled with a thread-local guard: a
violation's log write could in principle fire another audit event, so the
hook short-circuits if it's already on the stack.

``sys.addaudithook`` has no removal API; calling ``disable_audit_hook()``
flips a module-level flag that makes the still-registered hook a no-op,
which is the only path back to a clean state (used by the unit tests so
one test's strict mode doesn't poison the next).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import threading
import time
from pathlib import Path

__all__ = [
    "AuditViolation",
    "install_audit_hook",
    "disable_audit_hook",
    "add_allowed_prefix",
    "current_allowlist",
    "log_path",
]


class AuditViolation(RuntimeError):
    """Raised from inside the audit hook in strict mode when a
    ``subprocess.Popen`` spawn resolves to an executable outside the
    vendored allowlist. The exception propagates back out of ``Popen``
    and aborts the spawn before any process is created."""


_lock = threading.Lock()
_re_entrant = threading.local()
_installed = False
_enabled = False
_strict = False
_log_path: Path | None = None
_extra_prefixes: list[Path] = []


def _appdata_root() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "SMOArchipelago"
    return Path.home() / ".local" / "share" / "SMOArchipelago"


def _default_log_path() -> Path:
    return _appdata_root() / "exec-trace.log"


def log_path() -> Path | None:
    """Where the hook is currently appending records (or ``None`` if the
    hook has not been installed in this process)."""
    return _log_path


def install_audit_hook(
    *,
    log_path: Path | None = None,
    strict: bool | None = None,
) -> None:
    """Register the subprocess audit hook. Idempotent.

    ``log_path`` defaults to ``%APPDATA%/SMOArchipelago/exec-trace.log``;
    parent dirs are created best-effort.

    ``strict`` defaults to ``os.environ.get("SMOAP_AUDIT") == "strict"``;
    pass ``True`` / ``False`` explicitly to override (used by tests).
    """
    global _installed, _enabled, _strict, _log_path
    with _lock:
        if not _installed:
            sys.addaudithook(_hook)
            _installed = True
        if log_path is None:
            log_path = _default_log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        _log_path = log_path
        if strict is None:
            strict = os.environ.get("SMOAP_AUDIT", "").strip().lower() == "strict"
        _strict = bool(strict)
        _enabled = True


def disable_audit_hook() -> None:
    """Make the still-registered hook a no-op. Sticks for the rest of
    the process until ``install_audit_hook`` is called again."""
    global _enabled, _strict
    with _lock:
        _enabled = False
        _strict = False


def add_allowed_prefix(path: str | Path) -> None:
    """Whitelist an extra path (file or directory) for strict mode.

    Use when a tool resolves to a location the static allowlist
    doesn't know about (e.g. a custom Ryujinx install). Idempotent.
    """
    p = Path(path)
    with _lock:
        if p not in _extra_prefixes:
            _extra_prefixes.append(p)


def current_allowlist() -> list[Path]:
    """Snapshot the live allowlist (resolved tool dirs from prereqs +
    static vendored prefixes + runtime additions). Exposed so the
    wizard log / tests can show what the hook is checking against."""
    return _build_allowlist()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _build_allowlist() -> list[Path]:
    out: list[Path] = []

    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        out.append(Path(localapp) / "SMOArchipelago")
        out.append(Path(localapp) / "Programs" / "Python" / "Python312")
        # winget drops cmake / ninja / etc. under here when the "Install
        # them for me" prereq path fires. resolved_cmake / resolved_ninja
        # below will also point here, but the broader prefix catches the
        # tools cmake itself shells out to (e.g. clang-cl, link.exe).
        out.append(Path(localapp) / "Microsoft" / "WinGet" / "Packages")

    # System curl — used by installers.py's download fallback. The user
    # listed this explicitly; the literal file path is fine as an
    # "allowed prefix" because _is_under() accepts exact-match.
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    out.append(Path(sysroot) / "System32" / "curl.exe")

    # The current interpreter — the wizard spawns child Pythons for
    # build_switchmod.py, extract_shine_map.py, install_apworld.py, etc.
    # Without this, strict mode rejects the wizard's first subprocess.
    out.append(Path(sys.executable))
    out.append(Path(sys.executable).parent)

    # Tool paths the prereq detectors resolved earlier in the run. Lazy
    # import — prereqs imports subprocess, and we want audit.py to be
    # safely importable from anywhere without dragging that in.
    try:
        from . import prereqs as _p
    except Exception:
        _p = None

    if _p is not None:
        for getter in (
            getattr(_p, "resolved_cmake", None),
            getattr(_p, "resolved_python312_bin", None),
            getattr(_p, "resolved_llvm_bin", None),
            getattr(_p, "resolved_mingw_bin", None),
            getattr(_p, "resolved_ninja_bin", None),
        ):
            if getter is None:
                continue
            try:
                v = getter()
            except Exception:
                v = None
            if not v or v in ("cmake", "ninja"):
                # Bare-name fallback — the detector didn't actually
                # resolve a path. Skip (don't allowlist arbitrary PATH).
                continue
            p = Path(v)
            out.append(p)
            if p.parent != p:
                out.append(p.parent)

    out.extend(_extra_prefixes)
    return out


def _resolve_executable(executable, args) -> Path | None:
    """Best-effort: return the absolute path the OS will load.

    ``executable`` is whatever Popen passed to the audit event (the
    ``executable=`` kwarg, or ``None`` if the caller used argv[0]).
    Falls back to ``args[0]`` and resolves bare names via
    ``shutil.which``.

    Windows note: ``subprocess.Popen`` on Windows joins a list-arg into
    a single command line string before firing the audit event, so
    ``args`` may be a ``str`` rather than a list. We shlex-split it to
    pull out the leading token in that case.
    """
    target: str | None = None
    if isinstance(executable, (bytes, bytearray)):
        try:
            target = bytes(executable).decode("utf-8", "replace")
        except Exception:
            target = None
    elif isinstance(executable, str):
        target = executable

    if not target:
        if isinstance(args, (list, tuple)) and args:
            a0 = args[0]
            if isinstance(a0, (bytes, bytearray)):
                try:
                    target = bytes(a0).decode("utf-8", "replace")
                except Exception:
                    target = None
            elif isinstance(a0, str):
                target = a0
        elif isinstance(args, str) and args:
            try:
                parts = shlex.split(args, posix=(os.name != "nt"))
            except ValueError:
                parts = args.split()
            if parts:
                target = parts[0]
        elif isinstance(args, (bytes, bytearray)):
            try:
                decoded = bytes(args).decode("utf-8", "replace")
            except Exception:
                decoded = ""
            try:
                parts = shlex.split(decoded, posix=(os.name != "nt"))
            except ValueError:
                parts = decoded.split()
            if parts:
                target = parts[0]

    if not target:
        return None

    p = Path(target)
    if not p.is_absolute():
        which = shutil.which(target)
        if which:
            p = Path(which)
        else:
            return None
    try:
        return p.resolve()
    except OSError:
        return p


def _is_under(child: Path, parent: Path) -> bool:
    try:
        c = child.resolve()
    except OSError:
        c = child
    try:
        p = parent.resolve()
    except OSError:
        p = parent
    if c == p:
        return True
    try:
        c.relative_to(p)
        return True
    except ValueError:
        return False


def _argv_to_list(args) -> list[str]:
    """Normalize the audit event's ``args`` (list, tuple, str, or bytes)
    into a list of stringified argv elements. On Windows ``args`` is the
    already-joined command line string; we shlex-split it so the log
    record looks the same shape regardless of platform."""
    if isinstance(args, str):
        try:
            return shlex.split(args, posix=(os.name != "nt"))
        except ValueError:
            return args.split()
    if isinstance(args, (bytes, bytearray)):
        try:
            s = bytes(args).decode("utf-8", "replace")
        except Exception:
            return [repr(args)]
        try:
            return shlex.split(s, posix=(os.name != "nt"))
        except ValueError:
            return s.split()
    if not isinstance(args, (list, tuple)):
        return [repr(args)]
    out: list[str] = []
    for a in args:
        if isinstance(a, (bytes, bytearray)):
            try:
                out.append(bytes(a).decode("utf-8", "replace"))
            except Exception:
                out.append(repr(a))
        else:
            out.append(str(a))
    return out


def _write_log(record: dict) -> None:
    p = _log_path
    if p is None:
        return
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _hook(event: str, event_args) -> None:
    if event != "subprocess.Popen":
        return
    if not _enabled:
        return
    # A log write or shutil.which inside the hook could in theory fire
    # another audit event that lands back here. Short-circuit re-entry
    # so we never recurse or take the lock twice on one thread.
    if getattr(_re_entrant, "active", False):
        return
    _re_entrant.active = True
    try:
        # Audit signature (Py3.8+): (executable, args, cwd, env). Older /
        # alternate runtimes may pass fewer; pad defensively.
        padded = tuple(event_args) + (None, None, None, None)
        executable, args, cwd, _env = padded[:4]
        resolved = _resolve_executable(executable, args)
        argv = _argv_to_list(args)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "executable_raw": executable if isinstance(executable, str) else None,
            "executable_resolved": str(resolved) if resolved else None,
            "argv": argv,
            "cwd": str(cwd) if cwd else None,
            "strict": _strict,
        }
        if _strict:
            allowlist = _build_allowlist()
            ok = resolved is not None and any(
                _is_under(resolved, prefix) for prefix in allowlist
            )
            record["allowlist_ok"] = ok
            if not ok:
                record["allowlist_checked"] = [str(p) for p in allowlist]
            _write_log(record)
            if not ok:
                raise AuditViolation(
                    f"subprocess.Popen outside vendored allowlist: "
                    f"executable={resolved!s} argv={argv!r}"
                )
        else:
            _write_log(record)
    finally:
        _re_entrant.active = False
