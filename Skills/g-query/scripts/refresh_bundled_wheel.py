"""Maintenance script: rebuild g_query from source and refresh the wheel
bundled in this skill's dist/ folder.

Only useful when run from within the g-query project itself (it needs the
project's pyproject.toml/src layout) -- not part of the portable, self-
contained skill surface. Run this after changing anything under src/g_query
so the skill's bundled wheel stays in sync with the real source.

Usage (from anywhere inside the g-query project):
    python .claude/skills/g-query/scripts/refresh_bundled_wheel.py [project_root]

If project_root is omitted, it's found by walking up from this script's
location looking for a pyproject.toml.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIST = Path(__file__).resolve().parent.parent / "dist"


def find_project_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "g_query").exists():
            return candidate
    raise SystemExit(
        "Could not find the g-query project root (looked for pyproject.toml "
        "+ src/g_query walking up from this script). Pass it explicitly: "
        "python refresh_bundled_wheel.py /path/to/G-Query"
    )


def main() -> int:
    project_root = Path(sys.argv[1]) if len(sys.argv) > 1 else find_project_root(Path(__file__).resolve())
    print(f"Project root: {project_root}")

    project_dist = project_root / "dist"
    for old in project_dist.glob("g_query-*.whl"):
        old.unlink()

    print("Building wheel...")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel"],
        cwd=str(project_root),
        check=True,
    )

    built = sorted(project_dist.glob("g_query-*.whl"))
    if not built:
        raise SystemExit("Build succeeded but no wheel found in dist/ -- something's wrong.")
    new_wheel = built[-1]

    SKILL_DIST.mkdir(parents=True, exist_ok=True)
    for old in SKILL_DIST.glob("g_query-*.whl"):
        old.unlink()

    dest = SKILL_DIST / new_wheel.name
    dest.write_bytes(new_wheel.read_bytes())
    print(f"Refreshed bundled wheel: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
