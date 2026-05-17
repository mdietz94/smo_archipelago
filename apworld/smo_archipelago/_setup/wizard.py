"""Kivy multi-page setup wizard.

Entry point: `run_setup_wizard(smoap_path: str | None = None)`. Called via
`launch_subprocess` from the apworld root `__init__.py::launch_smo_client`
when the user double-clicks a `.smoap` file and `is_setup_complete()`
returns False. Also surfaced via the `/setup` slash command in SMOClient.

Pages (sequenced; each calls `next_page()` when its work completes):

  1. WelcomePage       — what the wizard does, prereqs overview
  2. PrereqPage        — runs `_setup.prereqs.check_all()`, surfaces ✓/✗
  3. NspPickerPage     — file dialog for the user's SMO 1.0.0 NSP
  4. ExtractPage       — runs the extractor in a worker thread, streams log
  5. BridgeIpPage      — text field prefilled with `detect_lan_ip()`
  6. BuildPage         — runs sync_capture_table → cmake configure → cmake build
  7. DeployPage        — radio: SD card vs Ryujinx, with auto-detect
  8. DonePage          — "Launch SMOClient" button (if a .smoap was passed)

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
    run_cmake_build,
    run_cmake_configure,
    run_extract_maps,
    run_sync_capture_table,
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


# ---------------------------------------------------------------------------
# Wizard entry point
# ---------------------------------------------------------------------------

def run_setup_wizard(smoap_path: str | None = None) -> bool:
    """Open the Kivy wizard window. Blocks until the user closes it.

    `smoap_path` is the .smoap file the user opened (if any) — used to
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
    from kivy.uix.filechooser import FileChooserListView
    from kivy.uix.label import Label
    from kivy.uix.popup import Popup
    from kivy.uix.progressbar import ProgressBar
    from kivy.uix.screenmanager import Screen, ScreenManager
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.textinput import TextInput

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
    wizard_state: dict[str, Any] = {
        "smoap_path": smoap_path,
        "smoap": parse_smoap(Path(smoap_path)) if smoap_path else None,
        "nsp_path": None,        # Path | None
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
        root.add_widget(_h1("SMO Archipelago — First-time setup"))
        msg = (
            "This wizard prepares everything SMOClient needs to talk to a "
            "modded Switch running Super Mario Odyssey 1.0.0.\n\n"
            "REQUIREMENTS — confirm these BEFORE continuing:\n"
            "  - SMO version 1.0.0. If you're on 1.1.0+, downgrade first "
            "with Istador/odyssey-downgrade:\n"
            "    https://github.com/Istador/odyssey-downgrade\n"
            "  - Switch firmware 21.x or earlier. FW22+ is NOT supported "
            "(Nintendo's lifecycle changes broke subsdk9-style mods).\n"
            "  - Atmosphere CFW set up on the above. See "
            "https://nh-server.github.io/switch-guide/ if you're starting "
            "from scratch.\n"
            "  - Or Ryujinx as an alternative (same SMO 1.0.0 requirement).\n\n"
            "This wizard will:\n"
            "  - Check that you have devkitPro, CMake, Ninja, hactool, "
            "Python 3.12, and your Switch prod.keys.\n"
            "  - Extract moon + capture name tables from your own SMO 1.0.0 "
            "NSP (we cannot ship these — they are Nintendo content).\n"
            "  - Compile the Switch module with your bridge PC's LAN IP "
            "baked in (the IP cannot be changed without a recompile on "
            "retail Switch firmware).\n"
            "  - Copy the compiled module to your SD card OR Ryujinx mods "
            "directory.\n\n"
            "You only need to run this once per machine. Changing AP server "
            "or slot does NOT require re-running this — those go through "
            "SMOClient's Connect bar."
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

        rows_box = BoxLayout(orientation="vertical", spacing=4, size_hint_y=None)
        rows_box.bind(minimum_height=rows_box.setter("height"))
        scroller = ScrollView()
        scroller.add_widget(rows_box)
        root.add_widget(scroller)

        next_btn_holder: dict[str, Any] = {}

        def open_picker_for(r: PrereqResult) -> None:
            """Open a Kivy file dialog filtered for the given prereq, then
            persist the picked path under the prereq's key in setup_state
            and re-run the prereq check so the row turns green."""
            popup_root = BoxLayout(orientation="vertical", spacing=8, padding=8)
            chooser = FileChooserListView(filters=list(r.picker_filter) or ["*"])
            popup_root.add_widget(chooser)
            btn_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=40, spacing=8)
            ok_btn = Button(text="OK")
            cancel_btn = Button(text="Cancel")
            btn_row.add_widget(cancel_btn)
            btn_row.add_widget(ok_btn)
            popup_root.add_widget(btn_row)
            popup = Popup(title=r.picker_label, content=popup_root, size_hint=(0.9, 0.9))

            def commit(_i):
                sel = chooser.selection
                if sel:
                    state = load_setup_state()
                    state[f"{r.key}_path"] = sel[0]
                    save_setup_state(state)
                popup.dismiss()
                do_check()

            ok_btn.bind(on_release=commit)
            cancel_btn.bind(on_release=lambda _i: popup.dismiss())
            popup.open()

        def render(results: list[PrereqResult]) -> None:
            rows_box.clear_widgets()
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
                if not r.ok and r.install_url:
                    link = Button(text="Install...", size_hint_x=0.1)
                    link.bind(on_release=lambda _i, url=r.install_url: webbrowser.open(url))
                    row.add_widget(link)
                else:
                    row.add_widget(Label(text="", size_hint_x=0.1))
                rows_box.add_widget(row)
            ok = all_ok(results)
            if "next_btn" in next_btn_holder:
                next_btn_holder["next_btn"].disabled = not ok

        def do_check() -> None:
            state = load_setup_state()
            hactool_override = Path(state["hactool_path"]) if state.get("hactool_path") else None
            results = check_all(hactool_override=hactool_override)
            render(results)

        recheck = Button(text="Re-check", size_hint_y=None, height=40)
        recheck.bind(on_release=lambda _i: do_check())
        root.add_widget(recheck)

        nav, _, next_btn = _nav_row(lambda: goto("welcome"), lambda: goto("nsp"))
        next_btn.disabled = True
        next_btn_holder["next_btn"] = next_btn
        root.add_widget(nav)
        s.add_widget(root)
        # Run the initial check when the page is first shown.
        s.bind(on_pre_enter=lambda _i: do_check())
        return s

    # --- 3. NSP picker
    def build_nsp() -> Screen:
        s = Screen(name="nsp")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Pick your SMO 1.0.0 NSP"))
        root.add_widget(_label(
            "Browse to a NSP dump of Super Mario Odyssey 1.0.0 (not a "
            "patched version — 1.0.0 only). Moon + capture names will be "
            "extracted to %APPDATA%/SMOArchipelago/data/ and never leave "
            "your machine."
        ))
        chooser = FileChooserListView(filters=["*.nsp"])
        root.add_widget(chooser)
        nav, _, next_btn = _nav_row(lambda: goto("prereqs"),
                                    lambda: (set_nsp(), goto("extract")))

        def set_nsp() -> None:
            sel = chooser.selection
            if sel:
                wizard_state["nsp_path"] = Path(sel[0])

        def update_enabled(*_):
            next_btn.disabled = not chooser.selection
        chooser.bind(selection=update_enabled)
        next_btn.disabled = True
        root.add_widget(nav)
        s.add_widget(root)
        return s

    # --- 4. Extract maps
    def build_extract() -> Screen:
        s = Screen(name="extract")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Extract moon + capture maps"))
        status = _label("Starting extraction...")
        root.add_widget(status)
        # Always-visible hint pointing at the parallel file log. The Kivy
        # text widget can hide output in subtle ways (worker-thread races,
        # clock-callback drops, multiprocess pipe weirdness); the file log
        # is the ground-truth backstop.
        log_hint = _label(
            "If this looks frozen, tail %APPDATA%\\SMOArchipelago\\extract.log "
            "to see what the subprocess is actually doing.",
            height=24,
        )
        root.add_widget(log_hint)
        log_lines: list[str] = []
        log_box = TextInput(text="", readonly=True, font_name="RobotoMono-Regular"
                            if False else "Roboto",
                            size_hint=(1, 1))
        root.add_widget(log_box)

        nav, _, next_btn = _nav_row(lambda: goto("nsp"), lambda: goto("ip"))
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
            nsp = wizard_state["nsp_path"]
            import time
            _state["last_output_ts"] = time.monotonic()
            # Open file log fresh per run; "w" truncates so each Retry
            # gets a clean log instead of compounding across attempts.
            try:
                _state["log_file"] = open(extract_log_path, "w", encoding="utf-8")
            except OSError as e:
                _state["log_file"] = None
                on_line(f"[wizard] could not open {extract_log_path}: {e}")
            status.text = f"Extracting from {nsp.name}..."
            on_line(f"[wizard] === extract run start: {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
            on_line(f"[wizard] NSP: {nsp}")
            try:
                # Use the user-picked hactool path if the wizard's prereq
                # page persisted one; extractor falls back to PATH otherwise.
                state = load_setup_state()
                hactool_override = (
                    Path(state["hactool_path"]) if state.get("hactool_path") else None
                )
                on_line(f"[wizard] hactool override: {hactool_override}")
                on_line(f"[wizard] DEVKITPRO env: {os.environ.get('DEVKITPRO', '<unset>')}")
                on_line(f"[wizard] PATH (first 200 chars): {os.environ.get('PATH', '')[:200]}")
                # Start the heartbeat *after* we've laid down the header so
                # the first few lines aren't drowned out by "no output" pings.
                import threading as _threading
                hb = _threading.Thread(target=_heartbeat, daemon=True)
                _state["heartbeat_thread"] = hb
                hb.start()
                result = run_extract_maps(
                    nsp,
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
            from kivy.clock import Clock as _Clock
            def finish(_dt):
                if result.ok:
                    status.text = "Extraction complete."
                    next_btn.disabled = False
                    retry_btn.disabled = True
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
            log_lines.clear()
            log_box.text = ""
            next_btn.disabled = True
            retry_btn.disabled = True
            import threading as _threading
            t = _threading.Thread(target=run_in_worker, daemon=True)
            _state["worker_thread"] = t
            t.start()

        retry_btn.bind(on_release=lambda _i: start_worker())
        s.bind(on_pre_enter=lambda _i: start_worker())
        return s

    # --- 5. Bridge IP
    def build_ip() -> Screen:
        s = Screen(name="ip")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Bridge PC IP"))
        root.add_widget(_label(
            "Enter the LAN IP your Switch will use to reach this PC. We've "
            "guessed your primary adapter's IP. This IP gets baked into the "
            "Switch module — changing it later means re-running setup."
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

        nav, _, next_btn = _nav_row(lambda: goto("ip"), lambda: goto("deploy"))
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

        def start_worker() -> None:
            log_lines.clear()
            log_box.text = ""
            next_btn.disabled = True
            retry_btn.disabled = True
            threading.Thread(target=run_in_worker, daemon=True).start()

        retry_btn.bind(on_release=lambda _i: start_worker())
        s.bind(on_pre_enter=lambda _i: start_worker())
        return s

    # --- 7. Deploy target
    def build_deploy() -> Screen:
        s = Screen(name="deploy")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Deploy target"))
        root.add_widget(_label("Where should we copy the compiled mod?"))

        # Three radio rows.
        sd_candidates = detect_sd_candidates()
        sd_default = str(sd_candidates[0]) if sd_candidates else ""
        wizard_state["sd_root"] = sd_default
        wizard_state.setdefault("custom_root", "")

        # Ryujinx row
        ryu_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=48, spacing=8)
        ryu_cb = CheckBox(group="target", active=(wizard_state["deploy_target"] == "ryujinx"))
        ryu_row.add_widget(ryu_cb)
        ryu_row.add_widget(_label("Ryujinx (emulator):", size_hint_x=0.3))
        ryu_input = TextInput(text=wizard_state["ryujinx_root"] or "(not detected)",
                              multiline=False)
        ryu_row.add_widget(ryu_input)
        root.add_widget(ryu_row)

        # SD row
        sd_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                           height=48, spacing=8)
        sd_cb = CheckBox(group="target", active=(wizard_state["deploy_target"] == "sd"))
        sd_row.add_widget(sd_cb)
        sd_row.add_widget(_label("Real Switch (SD card):", size_hint_x=0.3))
        sd_input = TextInput(
            text=sd_default or "(plug SD card in, then click Re-detect)",
            multiline=False,
        )
        sd_row.add_widget(sd_input)
        root.add_widget(sd_row)

        # Custom-folder row — for users who want to manage the SD-card
        # sync themselves (UMS later, DBI / Goldleaf transfer, staging on
        # a network share, etc.). Writes the same atmosphere/contents/...
        # subtree the SD-card deploy produces, just under the chosen
        # folder root.
        custom_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                               height=48, spacing=8)
        custom_cb = CheckBox(group="target",
                              active=(wizard_state["deploy_target"] == "custom"))
        custom_row.add_widget(custom_cb)
        custom_row.add_widget(_label("Custom folder:", size_hint_x=0.3))
        custom_input = TextInput(
            text=wizard_state["custom_root"] or "(click Browse to pick a folder)",
            multiline=False,
        )
        custom_row.add_widget(custom_input)
        browse_btn = Button(text="Browse...", size_hint_x=None, width=100)
        custom_row.add_widget(browse_btn)
        root.add_widget(custom_row)

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
                    title="Pick a custom deploy folder",
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
            save_setup_state({
                "deploy_target": wizard_state["deploy_target"],
                "ryujinx_root": wizard_state["ryujinx_root"],
                "sd_root": wizard_state["sd_root"],
                "custom_root": wizard_state["custom_root"],
            })
            wizard_state["deploy_result"] = result
            wizard_log("deploy succeeded; transitioning to 'done' page")
            goto("done")

        return s

    # --- 8. Done
    def build_done() -> Screen:
        s = Screen(name="done")
        root = BoxLayout(orientation="vertical", padding=20, spacing=12)
        root.add_widget(_h1("Setup complete"))

        def _populate_inner() -> None:
            root.clear_widgets()
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
                    "Re-run this wizard only if your bridge PC's LAN IP "
                    "changes (or you want to switch deploy targets). AP "
                    "server / slot changes don't need a rebuild — type "
                    "/connect or use the Connect bar in SMOClient."
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
    sm.add_widget(build_ip())
    sm.add_widget(build_build())
    sm.add_widget(build_deploy())
    sm.add_widget(build_done())

    class SetupApp(App):
        title = "SMO Archipelago — Setup"
        def build(self_app):  # type: ignore[override]
            return sm

    SetupApp().run()
    return bool(wizard_state["launch_smoclient_after_close"])
