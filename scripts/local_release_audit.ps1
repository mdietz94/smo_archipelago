# Pre-tag release-gate harness. Fires the live wizard install e2e test
# (apworld/smo_archipelago/tests/test_wizard_e2e_live.py) which:
#   1. Sandboxes APPDATA + LOCALAPPDATA + PYTHONUSERBASE into a tempdir
#   2. Runs install_apworld --bundle-mod --bundle-scripts (real)
#   3. Runs wizard_cli.run_install over the sandboxable installers
#      against REAL upstream URLs (LLVM 19 ~800 MB, WinLibs ~260 MB,
#      hactool ~1 MB, sail-deps via pip)
#   4. Asserts every detector flips green post-install (the regression
#      guard for the hactool-wipe bug that motivated this work)
#   5. Writes stub maps + runs the wizard's real switch-mod build
#   6. Walks the resulting tree against the apworld zip's manifest +
#      release_audit's allowlist; rejects any unexpected file
#
# Total wall time ~15-20 min cold, ~2 min warm-cache. User state never
# touched (snapshot the dirs before/after to verify -- the test was
# validated against a real run, see PR #190).
#
# Invoked automatically by .githooks/pre-push on `git push origin v*`.
# Run standalone with: powershell -File scripts\local_release_audit.ps1
#
# ASCII-only: PowerShell 5.1 reads .ps1 files as the active ANSI codepage
# when there is no BOM. Stick to plain ASCII characters in this file.

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path

function Write-Step($msg) { Write-Host "[release-audit] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[release-audit] $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "[release-audit] $msg" -ForegroundColor Red }

# The e2e test itself does all the prereq + skip handling. We just need
# Python on PATH to invoke pytest. The test's own _require_winget_prereqs
# fixture will surface a clear skip if cmake/ninja/python aren't present
# (and the test isn't really useful in that case anyway).
Write-Step "running test_wizard_e2e_live.py (~15-20 min cold, ~2 min warm) ..."

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
  throw "python not found on PATH -- install Python 3.12 (the wizard's prereq install handles this)."
}

# Locate the pre-merge bridge venv at the main checkout's bridge/.venv
# if running from a worktree (where the venv isn't symlinked in). Falls
# back to the system python if the venv isn't there.
$venvPython = Join-Path $repoRoot "bridge\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  # Probe the main checkout (worktrees live under .claude/worktrees/...).
  $maybeMain = Join-Path $repoRoot "..\..\..\bridge\.venv\Scripts\python.exe"
  if (Test-Path $maybeMain) {
    $venvPython = (Resolve-Path $maybeMain).Path
  } else {
    $venvPython = $pythonCmd.Source
  }
}
Write-Host "  Python: $venvPython"

$env:SMOAP_LIVE_INSTALL = "1"
try {
  & $venvPython -m pytest `
    (Join-Path $repoRoot "apworld\smo_archipelago\tests\test_wizard_e2e_live.py") `
    -v
  $rc = $LASTEXITCODE
} finally {
  Remove-Item Env:\SMOAP_LIVE_INSTALL -ErrorAction SilentlyContinue
}

if ($rc -eq 0) {
  Write-Ok "release audit PASSED."
  exit 0
} else {
  Write-Fail "release audit FAILED (pytest rc=$rc)."
  exit $rc
}
