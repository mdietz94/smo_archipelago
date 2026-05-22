# One-time setup: point this clone's core.hooksPath at the in-repo
# .githooks/ directory so the tracked pre-push hook fires on tag pushes.
# Idempotent -- re-running just confirms the config is correct.
#
# Writes to the SHARED .git/config (not the per-worktree config.worktree)
# so the setting applies to every worktree of this repo. Several
# automation tools (notably the Claude Code worktree harness) initialize
# each new worktree's config.worktree with an absolute `hooksPath`
# pointing at the main checkout's .git/hooks; that override would mask
# our shared setting in the current worktree, so this script ALSO unsets
# the override in the current worktree's config when needed. Other
# worktrees with their own override stay unchanged -- the user should
# re-run this from any worktree they intend to push tags from.
#
# After running, `git push origin v0.X.Y-alpha` invokes
# scripts/local_release_audit.ps1 before the push lands. See
# docs/release-process.md for the full pre-tag flow.

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $repoRoot

# Find the shared .git/config (lives under git-common-dir; in a regular
# clone that's `.git/config`, in a worktree it's the main checkout's
# `.git/config`).
$gitCommonDir = (& git rev-parse --git-common-dir).Trim()
$gitDir = (& git rev-parse --git-dir).Trim()
$sharedConfig = Join-Path $gitCommonDir "config"
if (-not (Test-Path $sharedConfig)) {
  throw "shared git config not found at $sharedConfig -- is this a git repo?"
}

# Write to the shared config. From the main checkout `git config --local`
# would write here too, but from a worktree it would write to
# config.worktree instead -- explicit `--file` is unambiguous.
& git config --file $sharedConfig core.hooksPath .githooks
if ($LASTEXITCODE -ne 0) { throw "git config (shared) failed (rc=$LASTEXITCODE)" }
Write-Host "Set core.hooksPath = .githooks in $sharedConfig"

# If we're in a worktree, the per-worktree config.worktree may override
# the shared setting (the Claude Code harness does this -- each new
# worktree is created with an absolute hooksPath pointing back at the
# main .git/hooks). Unset that override so the shared `.githooks` wins.
#
# Important flag distinction:
#   --local    : SHARED .git/config (NOT the worktree config; counter-
#                intuitive but per git's docs that's what --local means
#                when extensions.worktreeConfig is enabled).
#   --worktree : per-worktree config.worktree.
# Using --local --unset here would silently clobber the shared setting
# we just wrote.
if ($gitDir -ne $gitCommonDir) {
  # `--unset` returns 5 if the key isn't set; treat that as success
  # (idempotent). Anything else is a real failure.
  & git config --worktree --unset core.hooksPath 2>$null
  if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 5) {
    throw "git config --worktree --unset failed (rc=$LASTEXITCODE)"
  }
  Write-Host "Cleared per-worktree core.hooksPath override (so shared `.githooks` wins)"
}

# Read back the effective value to confirm the setting actually took
# effect in this worktree (catches mismatches between what we set and
# what git resolves -- e.g. an environment-level GIT_CONFIG_KEY_* var
# would silently win over both shared and worktree).
$effective = (& git config --get core.hooksPath).Trim()
if ($effective -ne ".githooks") {
  throw @"
core.hooksPath did not take effect: effective value is '$effective', expected '.githooks'.
Check `git config --show-origin --get-all core.hooksPath` for who's setting it.
"@
}
Write-Host "Verified: `git config core.hooksPath` -> $effective"

# Sanity-check the hook itself.
$hook = Join-Path $repoRoot ".githooks\pre-push"
if (-not (Test-Path $hook)) {
  Write-Warning "expected hook not found at $hook -- check that your clone is complete."
} else {
  Write-Host "Pre-push hook present at $hook"
}

Write-Host ""
Write-Host "Tag pushes now trigger scripts\local_release_audit.ps1 before completing."
Write-Host "Bypass (rarely needed): git push --no-verify origin <tag>"
