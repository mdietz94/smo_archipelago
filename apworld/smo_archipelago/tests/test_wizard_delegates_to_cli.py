"""Drift guards: wizard.py's page workers MUST delegate to wizard_cli.

The Kivy wizard and the headless CLI share `_setup.{prereqs,installers,
build,deploy}` primitives at the leaf level, but the *sequencing* of
those primitives (probe -> install -> extract -> build -> deploy, plus
the hash gate, maps_ready belt-and-braces, INSTALL_ORDER reordering,
SMOAP_*_BIN prewarm, custom-folder parent check, etc.) is the actual
contract that has to stay in lockstep. The refactor moved every sequencer
into `wizard_cli.run_*`; the wizard.py page workers became thin shells
that call those and translate the typed `*Outcome` to Kivy state.

These tests pin that delegation. If a future PR brings back inline
sequencing in wizard.py (`run_extract_maps` then `maps_ready` then
`verify_map_hashes`, etc.) instead of `wizard_cli.run_extract`, these
tests fail with a clear pointer at the drift.

Note: we don't import or instantiate the Kivy widgets here — we grep
the source. Importing `_setup.wizard.run_setup_wizard` requires Kivy
to be importable AND triggers KIVY_DATA_DIR setup, neither of which is
in scope for a unit test of the delegation contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_WIZARD_PY = (
    Path(__file__).resolve().parent.parent / "_setup" / "wizard.py"
)
_WIZARD_CLI_PY = (
    Path(__file__).resolve().parent.parent / "_setup" / "wizard_cli.py"
)


@pytest.fixture(scope="module")
def wizard_source() -> str:
    return _WIZARD_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def wizard_cli_source() -> str:
    return _WIZARD_CLI_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# wizard.py must call wizard_cli.run_*, not the underlying primitives.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cli_fn", [
    "wizard_cli.run_extract",
    "wizard_cli.run_build",
    "wizard_cli.run_install",
    "wizard_cli.run_deploy",
])
def test_wizard_calls_each_run_phase(wizard_source: str, cli_fn: str) -> None:
    """Every orchestration entry point in wizard_cli must have a
    matching call site in wizard.py. Drops here mean the GUI is going
    around the CLI and reimplementing sequencing — exactly the drift
    we refactored to prevent."""
    assert cli_fn in wizard_source, (
        f"wizard.py no longer calls {cli_fn}. The corresponding page "
        f"worker has gone back to inline orchestration, which lets the "
        f"GUI and the CLI drift. Either delegate via {cli_fn} or move "
        f"the new sequencing into wizard_cli so both entry points "
        f"share it."
    )


@pytest.mark.parametrize("primitive", [
    "run_extract_maps",
    "run_sync_capture_table",
    "run_sync_shine_table",
    "run_build_switchmod",
    "verify_map_hashes",
    "maps_ready",
    "deploy_to_ryujinx",
    "deploy_to_sd",
    "deploy_to_custom_folder",
])
def test_wizard_does_not_call_orchestration_primitives_directly(
    wizard_source: str, primitive: str,
) -> None:
    """The page worker functions used to call these primitives in line
    (e.g. extract worker did `run_extract_maps()` then `maps_ready()`
    then `verify_map_hashes()`). Post-refactor, those calls live inside
    `wizard_cli.run_extract` so only ONE sequence exists. If a worker
    re-introduces a direct call, the sequencing can drift from the CLI
    -- this test catches that.

    The check is on the call-syntax pattern `<name>(`, not bare name --
    documentation references in docstrings and comments are fine
    (and useful) as long as the GUI doesn't actually invoke the
    primitive. `collect_build_outputs` is intentionally NOT in the
    list above: the deploy page reads it before calling run_deploy
    for a Kivy-friendly "Build outputs missing" status message.
    That's a UI concern, not orchestration."""
    # Strip line comments before searching so a `# build.run_extract_maps`
    # reference doesn't trip the guard. Docstrings are checked too
    # narrowly via the `(` suffix.
    import re
    no_comments = re.sub(r"#[^\n]*", "", wizard_source)
    call_pattern = primitive + "("
    assert call_pattern not in no_comments, (
        f"wizard.py calls `{primitive}(...)` directly. That used to "
        f"be part of an inline page-worker orchestration that's now "
        f"owned by `wizard_cli`. Direct calls here let the GUI "
        f"sequence diverge from the CLI sequence -- delete the call "
        f"and route through the matching `wizard_cli.run_*` instead."
    )


def test_wizard_does_not_reimplement_install_loop(wizard_source: str) -> None:
    """The pre-refactor install popup iterated `for key in keys:
    INSTALLERS[key](...)` directly, which could diverge from
    `wizard_cli.run_install`'s INSTALL_ORDER reordering. After the
    refactor that loop should live in wizard_cli only."""
    # The legacy pattern was `for key in keys` immediately followed
    # by `INSTALLERS.get(key)`. Catching both anchors makes the test
    # robust to whitespace / variable-naming changes.
    assert "INSTALLERS.get(key)" not in wizard_source, (
        "wizard.py looks like it re-implements the INSTALLERS loop "
        "inline. The single source of truth lives in "
        "`wizard_cli.run_install` -- delegate there."
    )


def test_wizard_does_not_reimplement_preflight(wizard_source: str) -> None:
    """The pre-refactor install popup called `check_internet` and
    `check_winget` directly before iterating installers. Post-refactor
    those calls live inside `wizard_cli.run_install` (when
    `preflight=True`)."""
    assert "check_internet(on_line)" not in wizard_source, (
        "wizard.py looks like it re-implements the install preflight "
        "(check_internet / check_winget) inline. That logic lives in "
        "wizard_cli.run_install with preflight=True -- delegate there."
    )


# ---------------------------------------------------------------------------
# Behavior parity: hash-gate, maps_ready, install order, parent check.
# ---------------------------------------------------------------------------

def test_hash_gate_lives_only_in_wizard_cli(
    wizard_source: str, wizard_cli_source: str,
) -> None:
    """Hash verification was duplicated pre-refactor; now it lives only
    in wizard_cli.run_extract. If verify_map_hashes shows up in
    wizard.py again, somebody re-introduced the duplicate -- and one
    copy will eventually get out of sync with the other."""
    assert "verify_map_hashes" not in wizard_source
    assert "verify_map_hashes" in wizard_cli_source


def test_maps_ready_belt_and_braces_lives_only_in_wizard_cli(
    wizard_source: str, wizard_cli_source: str,
) -> None:
    """Same shape as the hash gate: the belt-and-braces maps_ready
    check that catches Windows `os.execv` returncode-zero-but-output-
    missing must live in one place."""
    assert "maps_ready()" not in wizard_source
    assert "maps_ready()" in wizard_cli_source


def test_install_order_reorder_lives_only_in_wizard_cli(
    wizard_source: str, wizard_cli_source: str,
) -> None:
    """`INSTALL_ORDER` reordering is what guarantees python312 runs
    before sail_python_deps regardless of how the caller batched the
    keys. The wizard.py popup-rendering button passed an already-
    ordered list, but the per-row Auto-install passed `[key]` -- both
    paths now route through wizard_cli.run_install which does the
    reordering, so the wizard layer should NOT mention INSTALL_ORDER."""
    # The "Install all missing" button's ordering call still lives in
    # wizard.py (it computes the *display* order on the prereq page,
    # which is a UI concern). What we forbid is iterating INSTALL_ORDER
    # to drive installer execution -- that's wizard_cli's job now.
    # Detect the execution-loop pattern specifically.
    assert "INSTALLERS[k" not in wizard_source, (
        "wizard.py looks like it iterates INSTALLERS by key to drive "
        "execution. Delegate to wizard_cli.run_install instead."
    )
    assert "for key in INSTALL_ORDER" not in wizard_source
    assert "INSTALL_ORDER" in wizard_cli_source


def test_custom_folder_parent_check_lives_in_wizard_cli(
    wizard_cli_source: str,
) -> None:
    """The wizard.py worker used to reject `Path(\".../custom\").parent`
    missing before calling deploy_to_custom_folder. wizard_cli.run_deploy
    now does the same check so the CLI doesn't silently mkdir a typo'd
    four-deep path."""
    assert "Custom folder parent does not exist" in wizard_cli_source


# ---------------------------------------------------------------------------
# wizard_cli is the single source of truth for the per-phase outcome
# dataclasses the GUI reads.
# ---------------------------------------------------------------------------

def test_wizard_reads_outcomes_not_legacy_result_types(
    wizard_source: str,
) -> None:
    """Pre-refactor, page workers built local `result` variables out
    of `BuildResult` / `DeployResult` / `MapHashCheck` returns from
    individual primitives. Post-refactor, the worker reads the typed
    `*Outcome` dataclass returned by `wizard_cli.run_*`. The Done page
    in particular consumes `wizard_state['deploy_result']` -- that
    object is now a `wizard_cli.DeployOutcome`, sharing `.files` +
    `.target` with the legacy `DeployResult` for back-compat."""
    # The wizard should not import the legacy BuildResult / DeployResult
    # types at the orchestration layer (it can still mention them in
    # comments / docstrings — that's fine).
    assert "from .build import" in wizard_source
    # Drift signal: if BuildResult or DeployResult are re-added to the
    # build/deploy imports, the worker probably went back to inline
    # orchestration. The current import line is intentionally minimal.
    import re
    build_imports = re.search(
        r"from \.build import\s*\(?([^)]*?)\)?$",
        wizard_source, re.MULTILINE,
    )
    if build_imports:
        block = build_imports.group(1)
        assert "BuildResult" not in block, (
            "wizard.py re-imported BuildResult. That used to be the "
            "return type of inline orchestration calls. Today, the "
            "page workers read `wizard_cli.BuildOutcome` and don't "
            "need the legacy type."
        )
    deploy_imports = re.search(
        r"from \.deploy import\s*\(?([^)]*?)\)?$",
        wizard_source, re.MULTILINE,
    )
    if deploy_imports:
        block = deploy_imports.group(1)
        assert "DeployResult" not in block
