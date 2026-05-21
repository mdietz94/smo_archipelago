#!/usr/bin/env python3
"""Convert OdysseyHeaders text-symlink-files-on-Windows into directory junctions.

Git for Windows clones repository symlinks as small text files containing the
relative target path. The example we used for spike-validation does this for
the `include/` -> `lib/OdysseyHeaders/*` mappings. Our equivalent layout under
`switch-mod/` may also need this if we ever symlink OdysseyHeaders
subdirectories into our include path (we don't today — we add OdysseyHeaders
directly to the include path via CMake — but if a future refactor moves to a
symlink layout, this script handles it).

For now this script is a no-op stub. It exists so that:
  - the scripts/ layout matches the migration plan
  - if a future user re-introduces include/ symlinks, the script is ready
  - the spike's history is preserved for reference
"""

import os
import sys

# Map link-name → target-relative-to-include-dir.
# Empty in the production build. If the build ever fails with
# "agl/common/aglDrawContext.h: No such file or directory" or similar,
# investigate whether a symlink was added and populate this dict.
MAPPINGS: dict[str, str] = {}


def main() -> int:
    if not MAPPINGS:
        print("[fix_hakkun_symlinks] no symlink mappings configured — nothing to do")
        return 0

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    include_dir = os.path.join(repo_root, "switch-mod", "include")
    if not os.path.isdir(include_dir):
        print(f"[fix_hakkun_symlinks] include/ dir not found: {include_dir}; nothing to do")
        return 0

    # Stub for future use.
    return 0


if __name__ == "__main__":
    sys.exit(main())
