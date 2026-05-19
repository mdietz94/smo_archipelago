"""Prepend a new version entry to poptracker/versions.json.

PopTracker's auto-update fetches the URL named in the pack manifest's
`versions_url`, treats the top entry as the latest, and compares its
`package_version` against the installed pack's. Different → update prompt.
Spec: https://github.com/black-sliver/PopTracker/tree/packlist

This script is invoked by the release workflow after a GitHub release
publishes — it edits versions.json on a fresh branch, which the workflow
then opens as a PR against main.

Idempotent: re-running with the same --version is a no-op (so a retried
workflow won't dup an entry).

Usage:
    python scripts/update_poptracker_versions.py \
        --version 0.1.18-alpha \
        --download-url https://github.com/mdietz94/smo_archipelago/releases/download/v0.1.18-alpha/smo-poptracker-v0.1.18-alpha.zip \
        --sha256-file release-artifacts/smo-poptracker.sha256 \
        --changelog-file release-artifacts/changelog.txt

    python scripts/update_poptracker_versions.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VERSIONS_FILE = REPO_ROOT / "poptracker" / "versions.json"


def read_versions(path: Path) -> dict:
    """Read versions.json. Missing file → empty bootstrap."""
    if not path.exists():
        return {"versions": []}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {"versions": []}
    data = json.loads(raw)
    if not isinstance(data, dict) or "versions" not in data:
        raise ValueError(f"{path}: expected object with 'versions' key")
    if not isinstance(data["versions"], list):
        raise ValueError(f"{path}: 'versions' must be an array")
    return data


def parse_sha256(sha256: str | None, sha256_file: Path | None, asset_name: str | None) -> str:
    """Resolve the sha256 hex. Either an explicit value or pull from a
    `sha256sum`-format file. When reading the file and multiple lines are
    present, asset_name (the zip filename) selects the right one."""
    if sha256:
        s = sha256.strip().lower()
    else:
        if sha256_file is None:
            raise ValueError("either --sha256 or --sha256-file is required")
        lines = [l.strip() for l in sha256_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            raise ValueError(f"{sha256_file}: empty")
        if len(lines) == 1:
            s = lines[0].split()[0].strip().lower()
        else:
            if asset_name is None:
                raise ValueError(f"{sha256_file}: multiple entries; pass --asset-name to disambiguate")
            match = None
            for line in lines:
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1].lstrip("*").strip() == asset_name:
                    match = parts[0].strip().lower()
                    break
            if match is None:
                raise ValueError(f"{sha256_file}: no entry for {asset_name!r}")
            s = match
    if not re.fullmatch(r"[0-9a-f]{64}", s):
        raise ValueError(f"bad sha256 {s!r}; need 64 hex chars")
    return s


def parse_changelog(text: str | None, path: Path | None) -> list[str]:
    """Each non-empty line becomes one changelog entry. Leading bullet
    characters (`-`, `*`, `•`) are stripped so the file format is flexible."""
    if text is None and path is None:
        return []
    if text is not None:
        raw = text
    else:
        raw = path.read_text(encoding="utf-8")
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        # Strip a leading bullet + space
        s = re.sub(r"^[-*•]\s+", "", s)
        if s:
            out.append(s)
    return out


def upsert_entry(
    data: dict,
    version: str,
    download_url: str,
    sha256: str,
    changelog: list[str],
) -> tuple[dict, bool]:
    """Prepend a new entry. Returns (new_data, changed).
    If `version` is already in the list, returns the data unchanged so
    a retried workflow is idempotent."""
    versions = list(data.get("versions", []))
    for entry in versions:
        if isinstance(entry, dict) and entry.get("package_version") == version:
            return data, False
    new_entry: dict = {
        "package_version": version,
        "download_url": download_url,
        "sha256": sha256,
    }
    if changelog:
        new_entry["changelog"] = changelog
    versions.insert(0, new_entry)
    return {"versions": versions}, True


def write_versions(path: Path, data: dict) -> None:
    """2-space indent + trailing newline so the file diffs cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------- self-test

def self_test() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name}: {detail}")

    sample_sha = "a" * 64

    # upsert into empty bootstrap
    data, changed = upsert_entry({"versions": []}, "1.0.0", "https://x/y.zip", sample_sha, ["First release"])
    check("upsert empty changed", changed, str(changed))
    check("upsert empty length", len(data["versions"]) == 1, str(data))
    check("upsert empty top version", data["versions"][0]["package_version"] == "1.0.0", str(data))
    check("upsert empty changelog", data["versions"][0]["changelog"] == ["First release"], str(data))

    # prepend a newer version
    data, changed = upsert_entry(data, "1.0.1", "https://x/y2.zip", "b" * 64, ["Fix bug"])
    check("prepend changed", changed)
    check("prepend length", len(data["versions"]) == 2, str(data))
    check("prepend newest first", data["versions"][0]["package_version"] == "1.0.1", str(data))
    check("prepend older second", data["versions"][1]["package_version"] == "1.0.0", str(data))

    # idempotency: same version is a no-op
    data2, changed = upsert_entry(data, "1.0.1", "https://x/different.zip", "c" * 64, ["different changelog"])
    check("dup no-op changed", not changed)
    check("dup no-op identical", data2 == data, "data mutated on duplicate")

    # entry without changelog
    data3, changed = upsert_entry({"versions": []}, "0.0.1", "https://x/z.zip", sample_sha, [])
    check("no changelog field omitted", "changelog" not in data3["versions"][0], str(data3))

    # parse_changelog: bullets stripped, blanks dropped
    cl = parse_changelog("- one\n* two\n\n  three  \n• four\n", None)
    check("parse_changelog bullets", cl == ["one", "two", "three", "four"], str(cl))

    # parse_changelog: file path branch
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as tf:
        tf.write("alpha\nbeta\n")
        tf_path = Path(tf.name)
    try:
        cl = parse_changelog(None, tf_path)
        check("parse_changelog file", cl == ["alpha", "beta"], str(cl))
    finally:
        tf_path.unlink()

    # parse_sha256: explicit
    s = parse_sha256("DEADBEEF" * 8, None, None)
    check("sha256 explicit lowercased", s == "deadbeef" * 8, s)

    # parse_sha256: single-line file
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".sha256", encoding="utf-8") as tf:
        tf.write("abc123" + "0" * 58 + "  smo-poptracker-v1.zip\n")
        tf_path = Path(tf.name)
    try:
        s = parse_sha256(None, tf_path, None)
        check("sha256 single-line file", s == "abc123" + "0" * 58, s)
    finally:
        tf_path.unlink()

    # parse_sha256: multi-line file, asset_name picks the right one
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".sha256", encoding="utf-8") as tf:
        tf.write("11" * 32 + "  other.zip\n")
        tf.write("22" * 32 + "  smo-poptracker-v1.zip\n")
        tf_path = Path(tf.name)
    try:
        s = parse_sha256(None, tf_path, "smo-poptracker-v1.zip")
        check("sha256 multi-line picks asset", s == "22" * 32, s)
    finally:
        tf_path.unlink()

    # parse_sha256: bad input raises
    try:
        parse_sha256("not-hex", None, None)
        check("sha256 bad input raises", False, "no exception")
    except ValueError:
        check("sha256 bad input raises", True)

    # round-trip via tempfile preserves structure
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as tf:
        tf.write('{"versions": [{"package_version":"0.1.0","download_url":"u","sha256":"' + sample_sha + '"}]}')
        tf_path = Path(tf.name)
    try:
        d = read_versions(tf_path)
        d2, changed = upsert_entry(d, "0.2.0", "u2", "f" * 64, ["bump"])
        write_versions(tf_path, d2)
        roundtrip = json.loads(tf_path.read_text(encoding="utf-8"))
        check("roundtrip top", roundtrip["versions"][0]["package_version"] == "0.2.0", str(roundtrip))
        check("roundtrip older preserved", roundtrip["versions"][1]["package_version"] == "0.1.0", str(roundtrip))
    finally:
        tf_path.unlink()

    if failures:
        print("FAIL:", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)
        return 1
    print("OK")
    return 0


# ---------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--version", help="package_version string (no leading v)")
    ap.add_argument("--download-url", help="direct URL to the released pack zip")
    ap.add_argument("--sha256", help="hex sha256 of the zip (64 chars)")
    ap.add_argument("--sha256-file", type=Path,
                    help="path to a sha256sum-format file (one or more lines)")
    ap.add_argument("--asset-name",
                    help="zip filename — used to disambiguate multi-line --sha256-file")
    ap.add_argument("--changelog", help="single changelog entry; lines split on \\n")
    ap.add_argument("--changelog-file", type=Path,
                    help="file with one changelog entry per line")
    ap.add_argument("--versions-file", type=Path, default=DEFAULT_VERSIONS_FILE,
                    help=f"path to versions.json (default: {DEFAULT_VERSIONS_FILE})")
    ap.add_argument("--self-test", action="store_true",
                    help="run internal tests and exit")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.version or not args.download_url:
        ap.error("--version and --download-url are required (or use --self-test)")
    sha = parse_sha256(args.sha256, args.sha256_file, args.asset_name)
    changelog = parse_changelog(args.changelog, args.changelog_file)
    data = read_versions(args.versions_file)
    new_data, changed = upsert_entry(data, args.version, args.download_url, sha, changelog)
    if not changed:
        print(f"no-op: {args.version} already in {args.versions_file}")
        return 0
    write_versions(args.versions_file, new_data)
    print(f"prepended {args.version} -> {args.versions_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
