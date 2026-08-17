"""Guards patch 10 in scripts/patch_hakkun.py — the LibHakkun stdlib URL.

LibHakkun moved its releases from GitHub to Codeberg and deleted the
GitHub ones (reported 2026-08-11), so the download URL baked into the
pinned submodule's `sys/tools/setup_libcxx_prepackaged.py` 404s. That
script is the only way `switch-mod/lib/std/*.a` (musl libc + LLVM libc++
+ libunwind + compiler-rt for aarch64) lands on disk, so an unpatched
tree cannot build at all — dev checkout, setup wizard, and release CI
alike.

These tests are network-free: they run patch_hakkun against a synthetic
submodule tree holding an upstream-verbatim copy of the script, then
exercise the patched download function with `subprocess.run` stubbed.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "patch_hakkun.py"

# Upstream sys/tools/setup_libcxx_prepackaged.py verbatim at pin 9892726b.
# Kept inline rather than read from the submodule because patch_hakkun
# rewrites the submodule copy in place — after one build there is no
# pristine copy left on disk to diff against.
_UPSTREAM = '''#!/usr/bin/env python3

import os
import subprocess
import tarfile
import sys

is_aarch32 = len(sys.argv) > 1 and sys.argv[1] == 'aarch32'

prepackaged_source_tar_name = "stdlib-aarch32-19.1.0_clang_19.1.7.tar.xz" if is_aarch32 else "stdlib-19.1.0_clang_19.1.7.tar.xz"
prepackaged_source = "https://github.com/fruityloops1/LibHakkun/releases/download/stdlib-19.1.0-3/" + prepackaged_source_tar_name

root_dir = os.getcwd()

def downloadAndExtractPrepackaged():
    print(f"Downloading pre-packaged stdlib")

    subprocess.run(['curl', '-O', '-L', prepackaged_source])

    print(f"Extracting")

    src_tar = tarfile.open(prepackaged_source_tar_name)
    src_tar.extractall('.')
    src_tar.close()

    os.remove(prepackaged_source_tar_name)

downloadAndExtractPrepackaged()
'''

_CODEBERG = "https://codeberg.org/fruityloops1/LibHakkun/releases/download/stdlib-19.1.0/"


def _fake_hakkun(tmp_path: Path, source: str = _UPSTREAM) -> Path:
    """Build a minimal `switch-mod/` tree holding just the one file
    patch 10 targets. The other patch targets are absent — patch_file
    reports them 'missing' and main() still returns 0."""
    script = tmp_path / "switch-mod" / "sys" / "tools" / "setup_libcxx_prepackaged.py"
    script.parent.mkdir(parents=True)
    script.write_text(source, encoding="utf-8")
    return script


def _run_patcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Import patch_hakkun fresh against the synthetic tree and run main().

    The module resolves HAKKUN at import time from SMOAP_SWITCH_MOD_DIR,
    so the env var must be set before exec_module — and the module must
    be re-imported per call rather than cached."""
    monkeypatch.setenv("SMOAP_SWITCH_MOD_DIR", str(tmp_path / "switch-mod"))
    spec = importlib.util.spec_from_file_location("patch_hakkun_under_test", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


def test_url_repointed_at_codeberg(tmp_path, monkeypatch):
    script = _fake_hakkun(tmp_path)
    assert _run_patcher(tmp_path, monkeypatch) == 0

    patched = script.read_text(encoding="utf-8")
    assert _CODEBERG in patched
    # The dead GitHub host must be gone from the live URL. It may only
    # survive inside our explanatory comment, never in an assignment.
    assert "github.com/fruityloops1/LibHakkun/releases" not in patched
    assert f'prepackaged_source = "{_CODEBERG}" + prepackaged_source_tar_name' in patched
    # Both arch tarball names are served under the same Codeberg tag, so
    # the aarch32 branch must keep flowing into the same URL prefix.
    assert "stdlib-aarch32-19.1.0_clang_19.1.7.tar.xz" in patched
    compile(patched, str(script), "exec")


def test_curl_call_hardened(tmp_path, monkeypatch):
    script = _fake_hakkun(tmp_path)
    _run_patcher(tmp_path, monkeypatch)

    patched = script.read_text(encoding="utf-8")
    # -f is what turns an HTTP error into a non-zero curl exit instead of
    # an error page saved under the tarball's name.
    assert "'-f'" in patched
    assert "returncode" in patched
    assert "raise SystemExit" in patched


def test_patch_is_idempotent(tmp_path, monkeypatch):
    script = _fake_hakkun(tmp_path)
    _run_patcher(tmp_path, monkeypatch)
    once = script.read_text(encoding="utf-8")
    _run_patcher(tmp_path, monkeypatch)
    assert script.read_text(encoding="utf-8") == once
    # Distinct sentinels: "SMO_HAKKUN_PATCH_10" alone would substring-match
    # 10B and make 10A self-report already-applied when it hadn't run.
    assert once.count("SMO_HAKKUN_PATCH_10A") == 1
    assert once.count("SMO_HAKKUN_PATCH_10B") == 1


def test_already_codeberg_tree_is_left_alone(tmp_path, monkeypatch):
    """If upstream fixes the URL themselves, patch 10a reports
    upstream-shifted (a warning) and main() still succeeds — the build
    proceeds rather than hard-failing on a patch we no longer need."""
    upstream_fixed = _UPSTREAM.replace(
        "https://github.com/fruityloops1/LibHakkun/releases/download/stdlib-19.1.0-3/",
        _CODEBERG,
    )
    script = _fake_hakkun(tmp_path, upstream_fixed)
    assert _run_patcher(tmp_path, monkeypatch) == 0
    assert _CODEBERG in script.read_text(encoding="utf-8")


@pytest.mark.parametrize("returncode", [22, 0])
def test_patched_download_fails_loudly(tmp_path, monkeypatch, returncode):
    """A failed download must raise with the URL in the message.

    `returncode=22` is curl's 4xx/5xx exit under -f. `returncode=0` covers
    the subtler case where curl claims success but no tarball landed —
    both used to fall through to an opaque tarfile.ReadError.
    """
    script = _fake_hakkun(tmp_path)
    _run_patcher(tmp_path, monkeypatch)

    calls: list[list[str]] = []

    class _Result:
        def __init__(self, rc: int) -> None:
            self.returncode = rc

    def _fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return _Result(returncode)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.chdir(tmp_path)  # no tarball here, so the exists() check fails

    source = script.read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        exec(compile(source, str(script), "exec"), {"__name__": "not_main"})

    message = str(excinfo.value)
    assert _CODEBERG in message
    assert "patch_hakkun.py" in message
    assert calls and "-f" in calls[0]
    assert calls[0][-1].startswith(_CODEBERG)


def test_repo_submodule_copy_is_patched_when_present():
    """The checked-out submodule (if initialized) must not still carry the
    dead GitHub URL after a build. Skips on a shallow checkout where
    switch-mod/sys was never initialized."""
    live = _REPO_ROOT / "switch-mod" / "sys" / "tools" / "setup_libcxx_prepackaged.py"
    if not live.exists():
        pytest.skip("switch-mod/sys submodule not initialized")
    text = live.read_text(encoding="utf-8")
    if "SMO_HAKKUN_PATCH_10A" not in text:
        pytest.skip("patch_hakkun.py has not been run against this checkout")
    assert 'prepackaged_source = "https://github.com/' not in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
