"""Kivy multi-page setup wizard.

Entry point: `run_setup_wizard(smoap_path: str | None = None)`. Surfaced
via the `/setup` slash command in SMOClient (which spawns this in a new
window via `launch_subprocess` while SMOClient stays open). Covers both
first-time setup and re-runs — bridge IP changes, apworld updates,
switching deploy targets between Ryujinx / SD card / custom folder.

Pages (sequenced; each calls `next_page()` when its work completes):

  1. WelcomePage       — what the wizard does, prereqs overview
  2. PrereqPage        — runs `_setup.prereqs.check_all()`, surfaces ✓/✗
  3. DumpPickerPage    — file dialog for the user's SMO 1.0.0 NSP or XCI
  4. ExtractPage       — runs the extractor in a worker thread, streams log
  5. BuildPage         — runs sync_capture_table → cmake configure → cmake build.
                         The bridge IP is captured silently via detect_lan_ip()
                         and baked in as a fallback; the Switch mod's runtime
                         UDP discovery (DiscoveryResponder) handles the common
                         case where the LAN IP changes after the build.
  6. DeployPage        — radio: SD card vs Ryujinx, with auto-detect
  7. DonePage          — "Launch SMOClient" button (if a .meatballsap was passed)

The legacy `BridgeIpPage` (a TextInput where the user manually confirmed the
LAN IP) was dropped in the discovery rework. The page's build function
(`build_ip`) is retained so the screen can still be reached programmatically
from a future Advanced override surface, but it is no longer registered in
the navigation flow.

Kivy is imported lazily INSIDE this module — never at apworld-import time —
because AP generation hosts (Linux servers running `python ap_generate.py`)
shouldn't need Kivy installed. Anyone running the wizard already has Kivy
installed because SMOClient itself uses it.

The wizard is intentionally synchronous on the UI thread for navigation;
long-running shells (extractor, cmake) run in `threading.Thread` workers
that push line-events back via Kivy's `Clock.schedule_once`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from . import appdata_root, build_dir, data_dir, setup_state_path
from .build import (
    BuildResult,
    bundled_switch_mod,
    collect_build_outputs,
    maps_ready,
    run_cmake_build,
    run_cmake_configure,
    run_extract_maps,
    run_sync_capture_table,
    verify_map_hashes,
)
from .deploy import (
    DeployResult,
    deploy_to_custom_folder,
    deploy_to_ryujinx,
    deploy_to_sd,
    detect_ryujinx_path,
    detect_sd_candidates,
)
from .net import detect_lan_ip, is_plausible_ipv4
from .prereqs import PrereqResult, all_ok, check_all
from .smoap_file import SmoapFile, parse_smoap, smoap_to_launch_args

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State that persists between wizard runs (last deploy target, etc.)
# ---------------------------------------------------------------------------

def load_setup_state() -> dict[str, Any]:
    p = setup_state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("setup_state.json unreadable; starting fresh")
        return {}


def save_setup_state(state: dict[str, Any]) -> None:
    p = setup_state_path()
    try:
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("failed to write setup_state.json: %s", e)


def _wizard_log_path():
    return appdata_root() / "wizard.log"


def wizard_log(line: str) -> None:
    """Append a breadcrumb to %APPDATA%/SMOArchipelago/wizard.log.

    Independent of the per-step extract.log (which only covers the
    extract subprocess) — this captures page transitions, deploy
    handler entry/exit, populate() execution, exception tracebacks,
    etc. so a "black screen, no text" report has SOMETHING for us to
    read after the fact.
    """
    import time
    try:
        p = _wizard_log_path()
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
    except OSError:
        pass  # best-effort; log losing a line shouldn't break the wizard


# Cap how many times a single wizard page can re-run its worker before
# we stop offering the Retry button. Picked so a flaky network or AV
# scan can retry a handful of times without manual intervention, but a
# persistently broken step (wrong NSP version, missing devkitPro, etc.)
# eventually surfaces a clear "give up — fix the underlying cause"
# message instead of letting the user click Retry forever. Counts reset
# on a fresh page entry, so navigating Back→Forward gives a clean slate.
MAX_STEP_ATTEMPTS = 5


def _resolve_persisted_path(
    state: dict[str, Any],
    key: str,
    on_line: Any = None,
) -> Path | None:
    """Pull a user-chosen tool path out of `setup_state.json` and verify
    the file is still there.

    The prereq page persists overrides for `hactool_path` and
    `prodkeys_path` so the extract worker doesn't have to re-prompt on
    each Retry. But the user can move those files between sessions
    (re-mount their key drive, archive an old hactool build, etc.) —
    passing a now-stale path to a subprocess produces a far less
    actionable error than catching the staleness here and surfacing a
    clear "this file is gone, re-check prereqs" message.

    Returns the Path if it points at an existing file; None if the key
    is unset, malformed, or the file is missing. The caller's normal
    `None` codepath (PATH fallback for hactool, prod.keys default
    location for keys) handles the "no override" case.
    """
    raw = state.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        if on_line is not None:
            on_line(
                f"[wizard] ignored persisted {key}: expected a non-empty "
                f"string in setup_state.json, got {type(raw).__name__}. "
                f"Falling back to auto-detect."
            )
        return None
    p = Path(raw)
    if not p.is_file():
        if on_line is not None:
            on_line(
                f"[wizard] persisted {key} no longer exists at {p}. "
                f"Falling back to auto-detect — if extraction fails, "
                f"re-run the prereq check to point at the new location."
            )
        return None
    return p


# ---------------------------------------------------------------------------
# Wizard entry point
# ---------------------------------------------------------------------------

def run_setup_wizard(smoap_path: str | None = None) -> bool:
    """Open the Kivy wizard window. Blocks until the user closes it.

    `smoap_path` is the .meatballsap file the user opened (if any) — used to
    pre-fill SMOClient on the "Launch now" button at the end. Pass None
    when the wizard is invoked standalone (e.g. via `/setup`).

    Returns True if the user clicked "Launch SMOClient" on the Done page
    (so the caller should hand off to SMOClient now that Kivy has shut
    down) and False otherwise. The caller — not this function — performs
    the SMOClient launch, because spawning a Kivy app from inside a still-
    running Kivy `App().run()` recurses into a broken state in frozen
    PyInstaller builds (multiprocessing.Process child can't read its
    bundled `kivy/data/style.kv` out of library.zip).
    """
    # IMPORTANT: kvui MUST be imported before any kivy.* module. kvui asserts
    # `"kivy" not in sys.modules` at its top and, as a side effect, sets
    # KIVY_DATA_DIR to point at AP's frozen-installer-extracted data dir.
    # Without this side effect, `from kivy.app import App` cascades into
    # kivy.lang.builder which open()s style.kv at the package-relative
    # default — inside library.zip in frozen builds — and aborts with
    # FileNotFoundError ("kivy\data\style.kv"). v0.1.1 through v0.1.3-alpha
    # all hit this. Same import-order rule client/gui.py follows.
    import kvui  # noqa: F401

    # Lazy Kivy import — keeps the apworld importable on headless gen hosts.
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.checkbox import CheckBox
    from kivy.uix.gridlayout import GridLayout
    from kivy.uix.label import Label
    from kivy.uix.popup import Popup
    from kivy.uix.progressbar import ProgressBar
    from kivy.uix.screenmanager import Screen, ScreenManager
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.textinput import TextInput
    from kivy.uix.widget import Widget

    # --------------------------- shared widgets / helpers -----------------

    def _label(text: str, **kw) -> Label:
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", 32)
        kw.setdefault("halign", "left")
        kw.setdefault("valign", "middle")
        lab = Label(text=text, **kw)
        lab.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        return lab

    def _h1(text: str) -> Label:
        return _label(f"[size=24][b]{text}[/b][/size]", markup=True, height=48)

    def _kivy_filter_to_ap_filetypes(
        picker_filter: tuple[str, ...] | list[str],
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Translate the apworld's Kivy-style filter tuples (`("hactool*",
        "*.exe", "*")`) into the `((label, (ext, ...)), ...)` shape
        `Utils.open_filename` expects (extensions WITH a leading dot;
        bare wildcards become an "Any file" group). hactool's `hactool*`
        glob has no leading-dot meaning, so we drop it and surface
        `(.exe, *)` instead — that matches the in-the-wild reality of
        users dropping hactool.exe under MyTools/ or similar."""
        if not picker_filter:
            return (("Any file", ("",)),)
        exts: list[str] = []
        any_seen = False
        for pat in picker_filter:
            if pat in ("*", "*.*"):
                any_seen = True
            elif pat.startswith("*.") and len(pat) > 2:
                exts.append("." + pat[2:])
        out: list[tuple[str, tuple[str, ...]]] = []
        if exts:
            out.append(("Supported files", tuple(exts)))
        if any_seen or not out:
            out.append(("Any file", ("",)))
        return tuple(out)

    def _nav_row(on_back, on_next, *, next_text="Next", next_enabled=True):
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=48, spacing=8)
        back_btn = Button(text="Back", size_hint_x=0.3)
        if on_back is None:
            back_btn.disabled = True
        else:
            back_btn.bind(on_release=lambda _i: on_back())
        next_btn = Button(text=next_text, size_hint_x=0.7)
        next_btn.disabled = not next_enabled
        if on_next is not None:
            next_btn.bind(on_release=lambda _i: on_next())
        row.add_widget(back_btn)
        row.add_widget(next_btn)
        return row, back_btn, next_btn

    # ----------------------------- shared state ---------------------------

    saved_state = load_setup_state()
    # Pre-fill the NSP/XCI dump path from saved state if the file is still
    # there. The user typically points the wizard at the same dump on every
    # re-run; making them re-Browse is busywork. We verify the file exists
    # before pre-filling so a moved/deleted dump falls back to "click Browse"
    # instead of silently propagating a stale path into the extract step.
    saved_dump = saved_state.get("dump_path")
    initial_dump = Path(saved_dump) if saved_dump and Path(saved_dump).is_file() else None
    wizard_state: dict[str, Any] = {
        "smoap_path": smoap_path,
        "smoap": parse_smoap(Path(smoap_path)) if smoap_path else None,
        "dump_path": initial_dump,
        "bridge_ip": detect_lan_ip(),
        "build_done": False,     # set True when cmake completes
        "deploy_target": saved_state.get("deploy_target", "ryujinx"),
        "ryujinx_root": str(detect_ryujinx_path() or ""),
        "sd_root": "",
        "custom_root": saved_state.get("custom_root", ""),
        # Set by the Done page's "Launch SMOClient" button before stopping
        # Kivy. The caller (in __init__.py) reads this after `App().run()`
        # returns and performs the actual SMOClient launch.
        "launch_smoclient_after_close": False,
    }
    wizard_log(f"=== wizard start (smoap={smoap_path!r}) ===")

    sm = ScreenManager()

    def goto(page_name: str) -> None:
        sm.current = page_name

    # ----------------------------- pages ----------------------------------

    # --- 1. Welcome
    def build_welcome() -> Screen:
        s = Screen(name="welcome")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("SMO Archipelago — Setup"))
        msg = (
            "This wizard prepares everything SMOClient needs to talk to a "
            "modded Switch running Super Mario Odyssey 1.0.0. Run it the "
            "first time you set up the client, and again whenever you "
            "update to a newer apworld, your bridge PC's LAN IP changes, "
            "or you want to switch deploy targets.\n\n"
            "REQUIREMENTS — confirm these BEFORE continuing:\n"
            "  - SMO version 1.0.0. If you're on 1.1.0+, downgrade first "
            "with Istador/odyssey-downgrade:\n"
            "    https://github.com/Istador/odyssey-downgrade\n"
            "  - Switch firmware 21.x or 22, OR an emulator. "
            "Both FW 21.x (the historical target) and FW 22 boot the "
            "subsdk9 overlay cleanly under Atmosphere; an emulator loads "
            "the same overlay and is fully supported.\n"
            "  - Atmosphere CFW set up on the above (real Switch only — "
            "skip on an emulator). See "
            "https://nh-server.github.io/switch-guide/ if you're starting "
            "from scratch.\n\n"
            "This wizard will:\n"
            "  - Check that you have LLVM 19, msys2 mingw64 g++, CMake, "
            "Ninja, hactool, Python 3.12, and your Switch prod.keys. "
            "(Most install quickest via winget — see the troubleshooting "
            "section in docs/first-time-setup.md.)\n"
            "  - Extract moon + capture name tables from your own SMO 1.0.0 "
            "NSP or XCI dump (we cannot ship these — they are Nintendo "
            "content).\n"
            "  - Compile the Switch module with your bridge PC's LAN IP "
            "baked in (the IP cannot be changed without a recompile on "
            "retail Switch firmware).\n"
            "  - Copy the compiled module to your SD card OR Ryujinx mods "
            "directory.\n\n"
            "Changing AP server or slot does NOT require re-running this "
            "— those go through SMOClient's Connect bar."
        )
        m = Label(text=msg, halign="left", valign="top", text_size=(600, None))
        m.bind(size=lambda inst, val: setattr(inst, "text_size", (val[0], None)))
        root.add_widget(m)
        nav, _, next_btn = _nav_row(None, lambda: goto("prereqs"), next_text="Begin")
        root.add_widget(nav)
        s.add_widget(root)
        return s

    # --- 2. Prereqs
    def build_prereqs() -> Screen:
        s = Screen(name="prereqs")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Prerequisites"))

        # Mode toggle: "auto" silently installs missing prereqs via winget
        # / direct installer; "manual" surfaces today's install-page links
        # and Browse buttons. Both modes share the underlying detector +
        # winget-path probing, so a manual-mode user who runs `winget
        # install` in a separate terminal still has Re-check turn rows
        # green without restarting the wizard. Default to auto; persist
        # the choice across wizard restarts.
        persisted_mode = saved_state.get("prereq_mode", "auto")
        mode_state: dict[str, str] = {"mode": persisted_mode}
        mode_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height=40, spacing=8)
        auto_cb = CheckBox(group="prereq_mode",
                           active=(persisted_mode == "auto"),
                           size_hint_x=None, width=30)
        mode_row.add_widget(auto_cb)
        mode_row.add_widget(_label(
            "Install them for me (recommended)",
            size_hint_x=None, width=300,
        ))
        manual_cb = CheckBox(group="prereq_mode",
                             active=(persisted_mode == "manual"),
                             size_hint_x=None, width=30)
        mode_row.add_widget(manual_cb)
        mode_row.add_widget(_label("I'll install them myself"))
        root.add_widget(mode_row)

        rows_box = BoxLayout(orientation="vertical", spacing=4, size_hint_y=None)
        rows_box.bind(minimum_height=rows_box.setter("height"))
        scroller = ScrollView()
        scroller.add_widget(rows_box)
        root.add_widget(scroller)

        next_btn_holder: dict[str, Any] = {}
        # Hold the most-recent results so a mode change can re-render
        # without re-running the detectors (which can take ~1 s).
        render_state: dict[str, Any] = {"last_results": []}

        def open_picker_for(r: PrereqResult) -> None:
            """Open the native file dialog for the given prereq via
            `Utils.open_filename` — Archipelago's standard helper.
            Same call as `Launcher.open_patch` / "Install APWorld",
            so the dialog (Win32 common dialog on Windows, kdialog /
            zenity on Linux, NSOpenPanel-via-subprocess on macOS) and
            its title-bar phrasing match every other AP-issued file
            picker. Persist the picked path under the prereq's key and
            re-run the prereq check so the row turns green."""
            from Utils import open_filename
            picked = open_filename(
                r.picker_label,
                _kivy_filter_to_ap_filetypes(r.picker_filter),
            )
            if picked:
                state = load_setup_state()
                state[f"{r.key}_path"] = picked
                save_setup_state(state)
            do_check()

        def run_installer_popup(keys: list[str], *, preflight: bool) -> None:
            """Spawn a worker thread that runs `_setup.installers` for the
            given prereq keys, streaming output into a modal popup.

            `preflight=True` runs `check_internet` (always) and `check_winget`
            (if any winget-installable key is in the batch) before kicking
            off the first installer. Used by the "Install all missing"
            button. Per-row Auto-install bypasses preflight to keep the
            single-tool case snappy."""
            log_widget = TextInput(text="", readonly=True, size_hint=(1, 1))
            popup_box = BoxLayout(orientation="vertical", spacing=8)
            popup_box.add_widget(log_widget)
            close_btn = Button(text="Installing... (close button enabled when done)",
                               size_hint_y=None, height=40, disabled=True)
            popup_box.add_widget(close_btn)
            popup = Popup(
                title=f"Installing {', '.join(keys)}",
                content=popup_box,
                size_hint=(0.9, 0.85),
                auto_dismiss=False,
            )
            close_btn.bind(on_release=lambda _i: popup.dismiss())
            popup.open()

            log_lines: list[str] = []

            def append_log(line: str) -> None:
                log_lines.append(line)
                if len(log_lines) > 2000:
                    del log_lines[:1000]
                log_widget.text = "\n".join(log_lines[-400:])

            def on_line(line: str) -> None:
                from kivy.clock import Clock as _Clock
                _Clock.schedule_once(lambda dt: append_log(line))

            def worker() -> None:
                # Lazy-import installers — pulls in urllib + ctypes + the
                # GitHub-API JSON parser, none of which the apworld layer
                # should drag onto a headless gen host.
                from .installers import (
                    INSTALLERS, check_internet, check_winget,
                )
                if preflight:
                    on_line("[wizard] preflight: checking internet...")
                    r = check_internet(on_line)
                    if not r.ok:
                        on_line("[wizard] preflight failed; aborting.")
                        from kivy.clock import Clock as _Clock
                        _Clock.schedule_once(
                            lambda dt: (
                                setattr(close_btn, "disabled", False),
                                setattr(close_btn, "text", "Close (install failed)"),
                            ),
                        )
                        return
                    if any(k in {"cmake", "ninja", "python312"} for k in keys):
                        on_line("[wizard] preflight: checking winget...")
                        r = check_winget(on_line)
                        if not r.ok:
                            on_line("[wizard] preflight failed; aborting.")
                            from kivy.clock import Clock as _Clock
                            _Clock.schedule_once(
                                lambda dt: (
                                    setattr(close_btn, "disabled", False),
                                    setattr(close_btn, "text", "Close (install failed)"),
                                ),
                            )
                            return
                any_failed = False
                for key in keys:
                    fn = INSTALLERS.get(key)
                    if fn is None:
                        on_line(f"[wizard] no installer registered for {key!r}; skipping")
                        continue
                    on_line(f"[wizard] === starting installer for {key!r} ===")
                    try:
                        result = fn(on_line)
                    except Exception as e:  # pragma: no cover — surface to UI
                        import traceback
                        on_line(f"[wizard] installer for {key!r} crashed: "
                                f"{type(e).__name__}: {e}")
                        on_line(traceback.format_exc())
                        any_failed = True
                        break
                    on_line(
                        f"[wizard] installer {key!r}: ok={result.ok} "
                        f"detail={result.detail!r}"
                    )
                    if not result.ok:
                        any_failed = True
                        on_line(f"[wizard] stopping install run after {key!r} failure")
                        break
                on_line("[wizard] === install run complete; re-running prereq check ===")
                from kivy.clock import Clock as _Clock

                def finish(_dt):
                    close_btn.disabled = False
                    close_btn.text = (
                        "Close" if not any_failed
                        else "Close (some installs failed — see log above)"
                    )
                    # Always re-check after install regardless of failure —
                    # rows that DID succeed still need their green flip.
                    do_check()
                _Clock.schedule_once(finish)

            threading.Thread(target=worker, daemon=True).start()

        def render(results: list[PrereqResult]) -> None:
            render_state["last_results"] = results
            rows_box.clear_widgets()
            current_mode = mode_state["mode"]
            for r in results:
                row = BoxLayout(orientation="horizontal", size_hint_y=None, height=36, spacing=8)
                mark = "[color=00aa00][b]OK[/b][/color]" if r.ok else "[color=cc0000][b]X[/b][/color]"
                row.add_widget(Label(text=mark, markup=True, size_hint_x=0.1))
                row.add_widget(Label(text=r.name, size_hint_x=0.25, halign="left", text_size=(150, None)))
                row.add_widget(Label(text=r.detail[:80], size_hint_x=0.45, halign="left", text_size=(320, None)))
                if not r.ok and r.picker_label:
                    pick = Button(text="Browse...", size_hint_x=0.1)
                    pick.bind(on_release=lambda _i, res=r: open_picker_for(res))
                    row.add_widget(pick)
                else:
                    row.add_widget(Label(text="", size_hint_x=0.1))
                # Mode-dependent action button: Auto-install in auto mode
                # (when the detector is auto-installable), Install link in
                # manual mode or as a fallback when no auto-install path
                # exists. The Browse button above is mode-independent —
                # users can still drop a hand-installed binary into place.
                if not r.ok:
                    if current_mode == "auto" and r.auto_installable:
                        auto_btn = Button(text="Auto-install", size_hint_x=0.1)
                        auto_btn.bind(
                            on_release=lambda _i, key=r.key: run_installer_popup(
                                [key], preflight=False,
                            ),
                        )
                        row.add_widget(auto_btn)
                    elif r.install_url:
                        link = Button(text="Install...", size_hint_x=0.1)
                        link.bind(on_release=lambda _i, url=r.install_url: webbrowser.open(url))
                        row.add_widget(link)
                    else:
                        row.add_widget(Label(text="", size_hint_x=0.1))
                else:
                    row.add_widget(Label(text="", size_hint_x=0.1))
                rows_box.add_widget(row)
                # When a detector provides multi-line install guidance
                # (currently just Ninja's winget hint + restart reminder),
                # surface it as a sub-row below the main row. Auto-size
                # the height to the wrapped text so the message can't be
                # silently clipped — the restart reminder is the whole
                # point of the note.
                if not r.ok and r.note:
                    note_lbl = Label(
                        text=r.note,
                        size_hint_y=None,
                        halign="left",
                        valign="top",
                        color=(0.85, 0.7, 0.2, 1),
                    )
                    def _resize_note(inst, val, _lbl=note_lbl):
                        _lbl.text_size = (val[0], None)
                        _lbl.height = _lbl.texture_size[1] + 8
                    note_lbl.bind(size=_resize_note)
                    rows_box.add_widget(note_lbl)
            ok = all_ok(results)
            if "next_btn" in next_btn_holder:
                next_btn_holder["next_btn"].disabled = not ok

        recheck = Button(text="Re-check", size_hint_y=None, height=40)
        check_in_progress: dict[str, bool] = {"running": False}

        def do_check() -> None:
            # `check_all` shells out to ~6 detectors (cmake, ninja,
            # python, devkitpro, hactool, prod.keys) — ~1 second of
            # frozen UI on the main thread if run inline. Off-load to
            # a worker so the button visibly changes state and the
            # Kivy frame loop keeps running.
            if check_in_progress["running"]:
                return
            check_in_progress["running"] = True
            recheck.disabled = True
            recheck.text = "Checking..."

            def worker() -> None:
                state = load_setup_state()
                hactool_override = (
                    Path(state["hactool_path"]) if state.get("hactool_path") else None
                )
                prod_keys_override = (
                    Path(state["prodkeys_path"]) if state.get("prodkeys_path") else None
                )
                results = check_all(
                    hactool_override=hactool_override,
                    prod_keys_override=prod_keys_override,
                )
                def finish(_dt):
                    render(results)
                    recheck.text = "Re-check"
                    recheck.disabled = False
                    check_in_progress["running"] = False
                from kivy.clock import Clock as _Clock
                _Clock.schedule_once(finish)

            threading.Thread(target=worker, daemon=True).start()

        # "Install all missing" — auto mode only. Disabled (greyed out) in
        # manual mode so the UI element stays visible (users can see it
        # exists, understand what auto-mode would give them) without
        # accidentally firing it.
        def on_install_all_missing(_i) -> None:
            from .installers import INSTALL_ORDER
            results = render_state.get("last_results", [])
            failed_keys = {
                r.key for r in results if not r.ok and r.auto_installable
            }
            ordered = [k for k in INSTALL_ORDER if k in failed_keys]
            if not ordered:
                wizard_log("Install all missing: no failed auto-installable rows")
                return
            wizard_log(f"Install all missing: {ordered}")
            run_installer_popup(ordered, preflight=True)

        install_all_btn = Button(
            text="Install all missing (requires UAC consent for devkitPro; ~700 MB total)",
            size_hint_y=None, height=40,
            disabled=(persisted_mode != "auto"),
        )
        install_all_btn.bind(on_release=on_install_all_missing)
        root.add_widget(install_all_btn)

        recheck.bind(on_release=lambda _i: do_check())
        root.add_widget(recheck)

        def on_mode_change(_inst, _val) -> None:
            new_mode = "auto" if auto_cb.active else "manual"
            if new_mode == mode_state["mode"]:
                return
            mode_state["mode"] = new_mode
            state = load_setup_state()
            state["prereq_mode"] = new_mode
            save_setup_state(state)
            install_all_btn.disabled = (new_mode != "auto")
            # Re-render without re-running detectors so button swap is
            # instant. The cached results are still authoritative because
            # a mode change doesn't affect detection.
            render(render_state.get("last_results", []))
        auto_cb.bind(active=on_mode_change)
        manual_cb.bind(active=on_mode_change)

        nav, _, next_btn = _nav_row(lambda: goto("welcome"), lambda: goto("nsp"))
        next_btn.disabled = True
        next_btn_holder["next_btn"] = next_btn
        root.add_widget(nav)
        s.add_widget(root)
        # Run the initial check when the page is first shown.
        s.bind(on_pre_enter=lambda _i: do_check())
        return s

    # --- 3. NSP/XCI picker
    def build_nsp() -> Screen:
        s = Screen(name="nsp")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Pick your SMO 1.0.0 dump"))
        root.add_widget(_label(
            "Browse to an NSP or XCI dump of Super Mario Odyssey 1.0.0 (not "
            "a patched version — 1.0.0 only). Moon + capture names will be "
            "extracted to %APPDATA%/SMOArchipelago/data/ and never leave "
            "your machine.\n\n"
            "XCI dumps additionally need your title.keys (alongside "
            "prod.keys) to contain the SMO entry — NSPs ship their own "
            "ticket so this isn't required for that path.",
            height=96,
        ))

        # The path display + Browse button. Display is a read-only text
        # input so long paths can be scrolled (a Label would clip silently
        # at the right edge for any path past the viewport width).
        picker_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                               height=48, spacing=8)
        # Pre-fill from setup_state if the user previously picked a dump
        # that still exists; we initialized wizard_state["dump_path"] from
        # saved_state at the top of run_setup_wizard. Otherwise show the
        # "click Browse" placeholder.
        initial_dump_path = wizard_state.get("dump_path")
        path_input = TextInput(
            text=(str(initial_dump_path) if initial_dump_path
                  else "(no file picked — click Browse...)"),
            readonly=True,
            multiline=False,
        )
        picker_row.add_widget(path_input)
        browse_btn = Button(text="Browse...", size_hint_x=None, width=120)
        picker_row.add_widget(browse_btn)
        root.add_widget(picker_row)

        nav, _, next_btn = _nav_row(lambda: goto("prereqs"),
                                    lambda: goto("extract"))
        # If we restored a valid dump path from saved state, the user can
        # advance immediately without re-Browsing.
        next_btn.disabled = initial_dump_path is None

        def on_browse(_i) -> None:
            # `Utils.open_filename` is the same helper Launcher.open_patch
            # and the Install-APWorld flow use, so the dialog and its
            # "Select ..." title-bar phrasing match every other AP-issued
            # file picker. `suggest` pre-fills with the previous pick
            # (handy on a Re-pick after a path typo).
            from Utils import open_filename
            current = wizard_state.get("dump_path")
            picked = open_filename(
                "Select Super Mario Odyssey 1.0.0 NSP or XCI",
                (("Switch dump", (".nsp", ".xci")),),
                suggest=str(current) if current else "",
            )
            if picked:
                wizard_state["dump_path"] = Path(picked)
                path_input.text = picked
                next_btn.disabled = False
                # Persist so the next wizard run pre-fills this path.
                # Merge into existing state to preserve sibling keys
                # (hactool_path, prodkeys_path, deploy_target, ...).
                state = load_setup_state()
                state["dump_path"] = picked
                save_setup_state(state)

        browse_btn.bind(on_release=on_browse)
        root.add_widget(nav)
        s.add_widget(root)
        return s

    # --- 4. Extract maps
    def build_extract() -> Screen:
        s = Screen(name="extract")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Extract moon + capture maps"))
        status = _label(
            "Extracting moon + capture maps from your dump. This typically "
            "takes 2-5 minutes the first time (oead venv setup + RomFS "
            "extract); subsequent runs are faster.",
            height=64,
        )
        root.add_widget(status)
        log_lines: list[str] = []
        log_box = TextInput(text="", readonly=True, font_name="RobotoMono-Regular"
                            if False else "Roboto",
                            size_hint=(1, 1))
        root.add_widget(log_box)

        # Bridge IP is now captured silently via detect_lan_ip() at startup
        # and baked into the build as a fallback; runtime UDP discovery
        # handles the common case. Skip straight to the build step.
        nav, _, next_btn = _nav_row(lambda: goto("nsp"), lambda: goto("build"))
        next_btn.disabled = True
        retry_btn = Button(text="Retry", size_hint_y=None, height=40, disabled=True)
        root.add_widget(retry_btn)
        root.add_widget(nav)
        s.add_widget(root)

        # File-log handle shared across helpers. Opened/closed by start_worker.
        # Mirrors every on_line message + adds wizard-side breadcrumbs the
        # subprocess can't emit (heartbeats, the exact spawn command, etc).
        extract_log_path = appdata_root() / "extract.log"
        _state: dict[str, Any] = {
            "log_file": None,            # TextIOWrapper | None
            "last_output_ts": None,      # float
            "worker_thread": None,
            "heartbeat_thread": None,
            # Bounded-retry counter — incremented at start_worker, reset on
            # fresh page entry (on_pre_enter resets it before kicking off
            # the first attempt). Caps the user's Retry clicks so a
            # persistently broken extract eventually surfaces a "stop and
            # diagnose" message instead of looping forever.
            "attempt_count": 0,
        }

        def _log_to_file(line: str) -> None:
            f = _state["log_file"]
            if f is not None:
                try:
                    import time
                    f.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
                    f.flush()
                except Exception:
                    pass

        def append_line(line: str) -> None:
            log_lines.append(line)
            # Cap the visible log so 5000 lines of compiler output don't
            # OOM the Kivy text widget.
            if len(log_lines) > 1000:
                del log_lines[:500]
            log_box.text = "\n".join(log_lines[-300:])

        def on_line(line: str) -> None:
            """Worker-thread callback: invoked from `_stream_subprocess`'s
            stdout-reader loop once per child-process line. Two destinations:

              1. The file log, immediately + synchronously, so the line is
                 durable even if the wizard process dies before the next
                 Kivy frame.
              2. The Kivy text widget, via Clock.schedule_once because we're
                 on a worker thread and direct UI mutation is unsafe.

            Also updates the heartbeat timestamp so the watcher knows the
            subprocess is still producing output."""
            import time
            _state["last_output_ts"] = time.monotonic()
            _log_to_file(line)
            from kivy.clock import Clock as _Clock
            _Clock.schedule_once(lambda dt: append_line(line))

        def _heartbeat() -> None:
            """Emit "still running" lines to BOTH the file log and the
            Kivy widget every 10s of subprocess silence. Lets the user
            distinguish "wizard hung" from "subprocess working silently".
            Exits when the worker thread terminates."""
            import time
            while True:
                worker = _state.get("worker_thread")
                if worker is None or not worker.is_alive():
                    return
                time.sleep(5)
                last = _state.get("last_output_ts")
                if last is None:
                    continue
                elapsed = time.monotonic() - last
                if elapsed >= 10:
                    msg = (
                        f"[wizard] no subprocess output for {int(elapsed)}s "
                        f"(python.exe still alive; check Task Manager CPU/I/O)"
                    )
                    on_line(msg)
                    _state["last_output_ts"] = time.monotonic()  # debounce

        def run_in_worker() -> None:
            dump = wizard_state["dump_path"]
            import time
            _state["last_output_ts"] = time.monotonic()
            # Open file log fresh per run; "w" truncates so each Retry
            # gets a clean log instead of compounding across attempts.
            # If the log can't be opened (APPDATA read-only, disk full),
            # surface to status.text — the previous behaviour of logging
            # only via on_line meant the warning landed in the log box
            # but the user kept staring at a "Extracting..." status with
            # no indication anything was wrong.
            try:
                _state["log_file"] = open(extract_log_path, "w", encoding="utf-8")
            except OSError as e:
                _state["log_file"] = None
                msg = (
                    f"Could not open extract log at {extract_log_path}: {e}. "
                    f"Extraction will still run, but output won't be "
                    f"persisted to disk for later inspection. Check that "
                    f"%APPDATA% is writable."
                )
                on_line(f"[wizard] {msg}")
                from kivy.clock import Clock as _Clock
                _Clock.schedule_once(
                    lambda dt: status.setter("text")(status, msg)
                )
            # Validate the dump file still exists — the picker checked at
            # the time of selection, but the user may have moved or
            # deleted the file between then and clicking Start Extract,
            # and otherwise the subprocess would fail far downstream with
            # an opaque "couldn't read NSP" message.
            if not dump.is_file():
                msg = (
                    f"Dump file no longer exists at {dump}. Go back to "
                    f"the previous step and re-pick your NSP/XCI."
                )
                on_line(f"[wizard] {msg}")
                from kivy.clock import Clock as _Clock
                _Clock.schedule_once(
                    lambda dt: status.setter("text")(status, msg)
                )
                _Clock.schedule_once(
                    lambda dt: setattr(retry_btn, "disabled", False)
                )
                _close_log()
                return
            status.text = f"Extracting from {dump.name}... (2-5 minutes typical)"
            on_line(f"[wizard] === extract run start: {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
            on_line(f"[wizard] dump: {dump} (kind={dump.suffix.lstrip('.').lower() or 'nsp'})")
            try:
                # Use the user-picked hactool path if the wizard's prereq
                # page persisted one; extractor falls back to PATH otherwise.
                state = load_setup_state()
                hactool_override = _resolve_persisted_path(
                    state, "hactool_path", on_line
                )
                prod_keys_override = _resolve_persisted_path(
                    state, "prodkeys_path", on_line
                )
                on_line(f"[wizard] hactool override: {hactool_override}")
                on_line(f"[wizard] prod.keys override: {prod_keys_override}")
                on_line(f"[wizard] DEVKITPRO env: {os.environ.get('DEVKITPRO', '<unset>')}")
                on_line(f"[wizard] PATH (first 200 chars): {os.environ.get('PATH', '')[:200]}")
                # Start the heartbeat *after* we've laid down the header so
                # the first few lines aren't drowned out by "no output" pings.
                import threading as _threading
                hb = _threading.Thread(target=_heartbeat, daemon=True)
                _state["heartbeat_thread"] = hb
                hb.start()
                result = run_extract_maps(
                    dump,
                    keys_path=prod_keys_override,
                    hactool_path=hactool_override,
                    on_line=on_line,
                )
                on_line(f"[wizard] subprocess exit code: {result.returncode}")
            except Exception as e:  # pragma: no cover
                on_line(f"[wizard] EXCEPTION: {type(e).__name__}: {e}")
                from kivy.clock import Clock as _Clock
                _Clock.schedule_once(lambda dt: status.setter("text")(
                    status, f"Failed: {e}"))
                _close_log()
                return
            # Belt-and-braces: even when the subprocess returns 0, confirm
            # the two output files actually landed. Earlier wizard releases
            # hit a Windows `os.execv` bug in the extractor's bootstrap
            # where the re-launched child failed with a non-zero exit code,
            # but the parent (the process subprocess.Popen was watching)
            # had already returned 0 because Windows `_wspawnv` returns
            # control to the caller. Checking for the output files closes
            # that gap regardless of how a future regression sneaks in.
            outputs_present = maps_ready()
            if result.ok and not outputs_present:
                on_line(
                    "[wizard] subprocess returned 0 but shine_map.json / "
                    "capture_map.json are missing — treating as failure"
                )
            # Hash gate: the USen-locale extract is deterministic across
            # every legitimate SMO 1.0.0 source (eShop NSP, cartridge,
            # XCI, any valid ticket). A mismatch is a real "wrong dump"
            # signal — typically a v1.1.0+ patched build, a different
            # game, or a corrupted dump — so the wizard refuses to
            # continue. If the user's source is genuinely 1.0.0 but
            # produces different bytes, the fix is to dump cleanly with
            # NXDumpTool from a clean retail source.
            hash_ok = False
            hash_hint = ""
            if outputs_present:
                try:
                    checks = verify_map_hashes()
                except Exception as e:  # pragma: no cover
                    on_line(
                        f"[wizard] hash check crashed: "
                        f"{type(e).__name__}: {e} — treating as failure"
                    )
                    checks = []
                    hash_hint = (
                        "Hash check itself errored — re-run setup."
                    )
                if checks and all(c.match for c in checks):
                    hash_ok = True
                    on_line(
                        "[wizard] hash check: maps match canonical "
                        "SMO 1.0.0 USen fingerprint"
                    )
                else:
                    mismatched = [c for c in checks if not c.match]
                    for c in mismatched:
                        on_line(
                            f"[wizard] hash check: {c.filename} "
                            f"differs from canonical "
                            f"(expected {c.expected[:12]}…, "
                            f"got {(c.actual or '<missing>')[:12]}…)"
                        )
                    if mismatched and not hash_hint:
                        hash_hint = (
                            "Maps don't match the canonical SMO 1.0.0 "
                            "USen fingerprint. Confirm your dump is "
                            "SMO 1.0.0 (not 1.1.0+ or a different game), "
                            "then re-dump with NXDumpTool from a clean "
                            "retail source and re-run Extract."
                        )
            from kivy.clock import Clock as _Clock
            def finish(_dt):
                if result.ok and outputs_present and hash_ok:
                    status.text = "Extraction complete."
                    next_btn.disabled = False
                    retry_btn.disabled = True
                elif result.ok and outputs_present and not hash_ok:
                    # Files exist but don't match the canonical
                    # fingerprint. Surface the actionable hint in the
                    # status text so the user sees the fix without
                    # scrolling the log.
                    status.text = (
                        hash_hint
                        or "Extraction produced unexpected maps — "
                           "see hash check lines above."
                    )
                    retry_btn.disabled = False
                elif result.ok:
                    status.text = (
                        "Extraction reported success but output files are "
                        "missing — see extract.log."
                    )
                    retry_btn.disabled = False
                else:
                    status.text = f"Extraction failed (exit {result.returncode})."
                    retry_btn.disabled = False
            _Clock.schedule_once(finish)
            _close_log()

        def _close_log() -> None:
            f = _state.pop("log_file", None)
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass

        def start_worker() -> None:
            # Refuse to spawn yet another subprocess after MAX_STEP_ATTEMPTS
            # consecutive failures on this page. Surfaces an actionable
            # "look at the log, diagnose, and re-run setup" message
            # instead of letting the user click Retry indefinitely on a
            # configuration problem that won't fix itself by retrying.
            if _state["attempt_count"] >= MAX_STEP_ATTEMPTS:
                msg = (
                    f"Extract failed {_state['attempt_count']} times in a "
                    f"row. Full log: {extract_log_path}. Common causes: "
                    f"wrong SMO version (need 1.0.0), corrupt NSP/XCI "
                    f"dump, missing hactool or prod.keys. Fix the "
                    f"underlying issue and re-open the wizard."
                )
                status.text = msg
                on_line(f"[wizard] {msg}")
                retry_btn.disabled = True
                return
            _state["attempt_count"] += 1
            on_line(
                f"[wizard] === attempt "
                f"{_state['attempt_count']}/{MAX_STEP_ATTEMPTS} ==="
            )
            log_lines.clear()
            log_box.text = ""
            next_btn.disabled = True
            retry_btn.disabled = True
            import threading as _threading
            t = _threading.Thread(target=run_in_worker, daemon=True)
            _state["worker_thread"] = t
            t.start()

        def _reset_and_start(*_):
            # Fresh page entry resets the attempt counter so navigating
            # Back→Forward doesn't immediately hit the cap.
            _state["attempt_count"] = 0
            start_worker()

        retry_btn.bind(on_release=lambda _i: start_worker())
        s.bind(on_pre_enter=_reset_and_start)
        return s

    # --- 5. Bridge IP
    def build_ip() -> Screen:
        s = Screen(name="ip")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Bridge PC IP"))
        root.add_widget(_label(
            "Enter the LAN IP your Switch will use to reach this PC. We've "
            "guessed your primary adapter's IP. This IP gets baked into the "
            "Switch module — changing it later (or updating to a newer "
            "apworld) means re-running setup."
        ))
        ip_input = TextInput(text=wizard_state["bridge_ip"], multiline=False,
                              size_hint_y=None, height=48)
        root.add_widget(ip_input)
        err_label = _label("", color=(0.8, 0.1, 0.1, 1))
        root.add_widget(err_label)

        nav, _, next_btn = _nav_row(lambda: goto("extract"),
                                    lambda: (commit(), goto("build")))

        def commit() -> None:
            wizard_state["bridge_ip"] = ip_input.text.strip()

        def validate(*_):
            ok = is_plausible_ipv4(ip_input.text.strip())
            next_btn.disabled = not ok
            err_label.text = "" if ok else "Not a valid IPv4 address (a.b.c.d)"

        ip_input.bind(text=validate)
        validate()
        root.add_widget(nav)
        s.add_widget(root)
        return s

    # --- 6. Build
    def build_build() -> Screen:
        s = Screen(name="build")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Build Switch module"))
        status = _label("Preparing...")
        root.add_widget(status)
        log_lines: list[str] = []
        log_box = TextInput(text="", readonly=True, size_hint=(1, 1))
        root.add_widget(log_box)

        nav, _, next_btn = _nav_row(lambda: goto("extract"), lambda: goto("deploy"))
        next_btn.disabled = True
        retry_btn = Button(text="Retry", size_hint_y=None, height=40, disabled=True)
        root.add_widget(retry_btn)
        root.add_widget(nav)
        s.add_widget(root)

        def append_line(line: str) -> None:
            log_lines.append(line)
            if len(log_lines) > 2000:
                del log_lines[:1000]
            log_box.text = "\n".join(log_lines[-400:])

        def on_line(line: str) -> None:
            from kivy.clock import Clock as _Clock
            _Clock.schedule_once(lambda dt: append_line(line))

        def update_status(text: str) -> None:
            from kivy.clock import Clock as _Clock
            _Clock.schedule_once(lambda dt: status.setter("text")(status, text))

        def run_in_worker() -> None:
            steps: list[tuple[str, callable]] = [
                ("Syncing capture table...",
                 lambda: run_sync_capture_table(on_line=on_line)),
                # `check_devkitpro` mutates os.environ["DEVKITPRO"] on a
                # default-path fallback, so cmake inherits it naturally —
                # but pass explicitly too as belt-and-braces in case the
                # detector hasn't run in this wizard session (Re-check
                # always runs it; first-render also runs it; this is just
                # defensive).
                (f"Configuring CMake (bridge={wizard_state['bridge_ip']})...",
                 lambda: run_cmake_configure(
                     wizard_state["bridge_ip"],
                     devkitpro=os.environ.get("DEVKITPRO"),
                     on_line=on_line,
                 )),
                ("Compiling Switch module (this can take ~1 minute)...",
                 lambda: run_cmake_build(on_line=on_line)),
            ]
            for label, fn in steps:
                update_status(label)
                try:
                    result = fn()
                except FileNotFoundError as e:
                    update_status(f"Failed: {e}")
                    from kivy.clock import Clock as _Clock
                    _Clock.schedule_once(lambda dt: setattr(retry_btn, "disabled", False))
                    return
                if not result.ok:
                    update_status(f"Failed at step '{label}' (exit {result.returncode}).")
                    from kivy.clock import Clock as _Clock
                    _Clock.schedule_once(lambda dt: setattr(retry_btn, "disabled", False))
                    return
            # Verify outputs.
            try:
                collect_build_outputs()
            except FileNotFoundError as e:
                update_status(f"Build returned 0 but outputs missing: {e}")
                from kivy.clock import Clock as _Clock
                _Clock.schedule_once(lambda dt: setattr(retry_btn, "disabled", False))
                return
            wizard_state["build_done"] = True
            update_status("Build complete.")
            from kivy.clock import Clock as _Clock
            _Clock.schedule_once(lambda dt: setattr(next_btn, "disabled", False))

        # Bounded-retry counter for the build page. Same rationale as the
        # extract page: a wedged cmake/ninja config issue (wrong devkitPro
        # version, missing toolchain, etc.) won't fix itself by retrying,
        # so eventually we surface a "diagnose and re-open setup" message
        # instead of letting the user click Retry forever.
        build_state: dict[str, Any] = {"attempt_count": 0}

        def start_worker() -> None:
            if build_state["attempt_count"] >= MAX_STEP_ATTEMPTS:
                msg = (
                    f"Build failed {build_state['attempt_count']} times "
                    f"in a row. Common causes: wrong devkitPro version, "
                    f"missing Ninja in PATH, corrupt switch_mod sources. "
                    f"Re-run the Re-check prereqs step, then re-open the "
                    f"wizard."
                )
                update_status(msg)
                on_line(f"[wizard] {msg}")
                retry_btn.disabled = True
                return
            build_state["attempt_count"] += 1
            on_line(
                f"[wizard] === build attempt "
                f"{build_state['attempt_count']}/{MAX_STEP_ATTEMPTS} ==="
            )
            log_lines.clear()
            log_box.text = ""
            next_btn.disabled = True
            retry_btn.disabled = True
            threading.Thread(target=run_in_worker, daemon=True).start()

        def _reset_and_start(*_):
            build_state["attempt_count"] = 0
            start_worker()

        retry_btn.bind(on_release=lambda _i: start_worker())
        s.bind(on_pre_enter=_reset_and_start)
        return s

    # --- 7. Deploy target
    def build_deploy() -> Screen:
        s = Screen(name="deploy")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Deploy target"))
        root.add_widget(_label("Where should we copy the compiled mod?"))

        # Three radio rows in a 4-column GridLayout so checkbox / label /
        # input / browse-button align vertically across rows. Separate
        # per-row BoxLayouts can't align because each row sizes its
        # columns independently (and the custom row's extra Browse button
        # would shrink only that row's input).
        sd_candidates = detect_sd_candidates()
        sd_default = str(sd_candidates[0]) if sd_candidates else ""
        wizard_state["sd_root"] = sd_default
        wizard_state.setdefault("custom_root", "")

        grid = GridLayout(
            cols=4,
            size_hint_y=None,
            spacing=8,
            row_default_height=48,
            row_force_default=True,
        )
        grid.bind(minimum_height=grid.setter("height"))

        # Fixed-width checkbox column keeps the indicator next to its
        # label instead of floating in the middle of a stretched cell.
        _CB_W = 40
        _LBL_W = 200
        _BROWSE_W = 100

        # Ryujinx row
        ryu_cb = CheckBox(group="target",
                          active=(wizard_state["deploy_target"] == "ryujinx"),
                          size_hint_x=None, width=_CB_W)
        ryu_input = TextInput(text=wizard_state["ryujinx_root"] or "(not detected)",
                              multiline=False)
        grid.add_widget(ryu_cb)
        grid.add_widget(_label("Ryujinx (emulator):",
                               size_hint_x=None, width=_LBL_W))
        grid.add_widget(ryu_input)
        grid.add_widget(Widget(size_hint_x=None, width=_BROWSE_W))

        # SD row
        sd_cb = CheckBox(group="target",
                         active=(wizard_state["deploy_target"] == "sd"),
                         size_hint_x=None, width=_CB_W)
        sd_input = TextInput(
            text=sd_default or "(plug SD card in, then click Re-detect)",
            multiline=False,
        )
        grid.add_widget(sd_cb)
        grid.add_widget(_label("Real Switch (SD card):",
                               size_hint_x=None, width=_LBL_W))
        grid.add_widget(sd_input)
        grid.add_widget(Widget(size_hint_x=None, width=_BROWSE_W))

        # Custom-folder row — for users who want to manage the SD-card
        # sync themselves (UMS later, DBI / Goldleaf transfer, staging on
        # a network share, etc.). Writes the same atmosphere/contents/...
        # subtree the SD-card deploy produces, just under the chosen
        # folder root.
        custom_cb = CheckBox(group="target",
                             active=(wizard_state["deploy_target"] == "custom"),
                             size_hint_x=None, width=_CB_W)
        custom_input = TextInput(
            text=wizard_state["custom_root"] or "(click Browse to pick a folder)",
            multiline=False,
        )
        browse_btn = Button(text="Browse...", size_hint_x=None, width=_BROWSE_W)
        grid.add_widget(custom_cb)
        grid.add_widget(_label("Custom folder:",
                               size_hint_x=None, width=_LBL_W))
        grid.add_widget(custom_input)
        grid.add_widget(browse_btn)

        root.add_widget(grid)

        def open_custom_picker(_i):
            # Tk's askdirectory is the cleanest cross-platform folder
            # picker — Kivy's FileChooserListView is file-oriented and
            # awkward for picking dirs. Tk is stdlib so no extra dep.
            try:
                import tkinter
                import tkinter.filedialog
                tkroot = tkinter.Tk()
                tkroot.withdraw()
                # Always-on-top so it isn't hidden behind the Kivy window.
                tkroot.attributes("-topmost", True)
                chosen = tkinter.filedialog.askdirectory(
                    title="Select custom deploy folder",
                    parent=tkroot,
                )
                tkroot.destroy()
                if chosen:
                    custom_input.text = chosen
                    custom_cb.active = True
            except Exception as e:
                wizard_log(f"custom-folder picker failed: {e!r}")
                status.text = f"Folder picker failed: {e}"
        browse_btn.bind(on_release=open_custom_picker)

        redetect = Button(text="Re-detect", size_hint_y=None, height=40)
        def do_redetect(_i):
            cands = detect_sd_candidates()
            if cands:
                sd_input.text = str(cands[0])
            ryu_input.text = str(detect_ryujinx_path() or "")
        redetect.bind(on_release=do_redetect)
        root.add_widget(redetect)

        status = _label("")
        root.add_widget(status)

        nav, _, next_btn = _nav_row(lambda: goto("build"), lambda: do_deploy_and_continue())
        root.add_widget(nav)
        s.add_widget(root)

        def do_deploy_and_continue() -> None:
            wizard_log("do_deploy_and_continue: entered")
            try:
                outputs = collect_build_outputs()
            except FileNotFoundError as e:
                wizard_log(f"do_deploy_and_continue: build outputs missing: {e}")
                status.text = f"Build outputs missing: {e}"
                return
            if ryu_cb.active:
                target = Path(ryu_input.text.strip())
                if not target.is_dir():
                    status.text = f"Ryujinx folder does not exist: {target}"
                    return
                wizard_log(f"deploy_to_ryujinx target={target}")
                result = deploy_to_ryujinx(target, outputs)
                wizard_state["deploy_target"] = "ryujinx"
                wizard_state["ryujinx_root"] = str(target)
            elif sd_cb.active:
                target = Path(sd_input.text.strip())
                if not target.exists():
                    status.text = f"SD card path does not exist: {target}"
                    return
                wizard_log(f"deploy_to_sd target={target}")
                result = deploy_to_sd(target, outputs)
                wizard_state["deploy_target"] = "sd"
                wizard_state["sd_root"] = str(target)
            elif custom_cb.active:
                target = Path(custom_input.text.strip())
                # Custom folder needn't already exist — we'll create it.
                # But the parent must exist and be a directory; otherwise
                # we'd silently create folder trees in surprising places.
                if not target.parent.exists():
                    status.text = (
                        f"Custom folder parent does not exist: {target.parent}"
                    )
                    return
                target.mkdir(parents=True, exist_ok=True)
                wizard_log(f"deploy_to_custom_folder target={target}")
                result = deploy_to_custom_folder(target, outputs)
                wizard_state["deploy_target"] = "custom"
                wizard_state["custom_root"] = str(target)
            else:
                status.text = "Pick a deploy target (Ryujinx, SD card, or Custom folder)."
                return
            wizard_log(
                f"deploy result ok={result.ok} target={result.target!r} "
                f"files={len(result.files)} error={result.error!r}"
            )
            if not result.ok:
                status.text = f"Deploy failed: {result.error}"
                return
            # Merge into existing state instead of replacing — sibling
            # keys like hactool_path, prodkeys_path, and dump_path are
            # persisted by other wizard pages, and writing a fresh dict
            # here would wipe them so every subsequent setup run would
            # re-prompt the user for files they already located.
            state = load_setup_state()
            state.update({
                "deploy_target": wizard_state["deploy_target"],
                "ryujinx_root": wizard_state["ryujinx_root"],
                "sd_root": wizard_state["sd_root"],
                "custom_root": wizard_state["custom_root"],
            })
            save_setup_state(state)
            wizard_state["deploy_result"] = result
            wizard_log("deploy succeeded; transitioning to 'done' page")
            goto("done")

        return s

    # --- 8. Done
    def build_done() -> Screen:
        s = Screen(name="done")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Setup complete"))

        def _success_banner() -> Label:
            # Bright green, larger-than-h1 confirmation so the user has
            # an unmistakable "you did it" cue even if the deploy
            # summary below fails to render. Independent of _h1's
            # styling so a markup parser failure here doesn't black-out
            # both lines.
            return _label(
                "[size=22][b]Installation successful.[/b][/size]",
                markup=True,
                color=(0.2, 0.8, 0.2, 1),
                height=56,
            )

        def _populate_inner() -> None:
            root.clear_widgets()
            root.add_widget(_success_banner())
            root.add_widget(_h1("Setup complete"))
            result: DeployResult | None = wizard_state.get("deploy_result")
            if result:
                summary = (
                    f"Copied {len(result.files)} files to {result.target}.\n\n"
                    "What to do next:\n"
                    "  - For a real Switch (SD card deploy): eject your SD "
                    "card, plug it into the Switch, boot SMO. The mod loads "
                    "automatically.\n"
                    "  - For Ryujinx: boot SMO in Ryujinx; the mod is "
                    "already in the mods directory.\n"
                    "  - For Custom folder: the files are laid out under "
                    "`atmosphere/contents/0100000000010000/{exefs,romfs}/` "
                    "inside the folder you picked. Copy that whole subtree "
                    "to your SD card's root (or onto the Switch however you "
                    "prefer).\n\n"
                    "Re-run this wizard if you update to a newer apworld "
                    "(SMOClient and the Switch mod ship in lockstep; "
                    "SMOClient will refuse to connect on a version "
                    "mismatch), if your bridge PC's LAN IP changes, or if "
                    "you want to switch deploy targets. AP server / slot "
                    "changes don't need a rebuild — type /connect or use "
                    "the Connect bar in SMOClient."
                )
                # Multi-line summary needs explicit height proportional to
                # content; the standard _label() helper hardcodes 32px which
                # would clip everything past the first line. 240px fits the
                # 12-line summary at default font size and gives us breathing
                # room if a sentence reflows.
                summary_lbl = _label(summary, height=240)
                root.add_widget(summary_lbl)
            launch_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=48)
            if wizard_state.get("smoap") is not None:
                launch_btn = Button(text=f"Launch SMOClient as {wizard_state['smoap'].slot_name}")
                launch_btn.bind(on_release=lambda _i: launch_smoclient_now())
                launch_row.add_widget(launch_btn)
            close_btn = Button(text="Close")
            close_btn.bind(on_release=lambda _i: App.get_running_app().stop())
            launch_row.add_widget(close_btn)
            root.add_widget(launch_row)

        def populate(*_):
            """Done-page builder. Wrapped in try/except so a render error
            becomes a visible "setup is done but the wizard couldn't
            render the summary" page rather than a black screen with no
            text (v0.1.8-alpha bug report). Either way, the user is
            free to close the wizard — setup IS complete; this is just
            the post-deploy UI."""
            wizard_log(f"populating Done page; deploy_result={wizard_state.get('deploy_result')!r}")
            try:
                _populate_inner()
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                wizard_log(f"populate() crashed: {e!r}\n{tb}")
                # Fallback minimal UI so the user sees text + can close.
                root.clear_widgets()
                root.add_widget(_success_banner())
                root.add_widget(_h1("Setup complete (rendering issue)"))
                root.add_widget(_label(
                    f"Setup finished but the Done page failed to render: {e}\n\n"
                    f"This does NOT mean setup failed — your build is deployed.\n"
                    f"Full traceback at {_wizard_log_path()}.",
                    height=120,
                ))
                close_btn = Button(text="Close", size_hint_y=None, height=48)
                close_btn.bind(on_release=lambda _i: App.get_running_app().stop())
                root.add_widget(close_btn)

        s.bind(on_pre_enter=populate)
        s.add_widget(root)
        return s

    def launch_smoclient_now() -> None:
        """Mark for post-wizard launch, then stop Kivy.

        We can't spawn SMOClient from here because (a) `launch_subprocess`
        is broken in frozen builds (Kivy bootstrap fails in the child) and
        (b) running SMOClient inline would try to start a second Kivy App
        while ours is still alive. The caller in __init__.py performs the
        handoff after `App().run()` returns and Kivy is fully torn down."""
        wizard_state["launch_smoclient_after_close"] = True
        App.get_running_app().stop()

    # ----------------------- assemble ---------------------------------

    sm.add_widget(build_welcome())
    sm.add_widget(build_prereqs())
    sm.add_widget(build_nsp())
    sm.add_widget(build_extract())
    # BridgeIpPage intentionally NOT registered — bridge IP is now
    # captured silently via detect_lan_ip() (see wizard_state init above)
    # and baked in as a fallback for the new runtime UDP discovery.
    sm.add_widget(build_build())
    sm.add_widget(build_deploy())
    sm.add_widget(build_done())

    class SetupApp(App):
        title = "SMO Archipelago — Setup"
        def build(self_app):  # type: ignore[override]
            return sm

    SetupApp().run()
    return bool(wizard_state["launch_smoclient_after_close"])
