"""Environment status check (and optional auto-fix) for the g-query skill.

Run this before attempting any GQueryClient.query() call, in a fresh project
or after a long gap -- it tells you exactly what's missing (package install,
Chromium binary, signed-in profile) and either the exact command to fix it,
or (with --install) does the fixable parts for you automatically. This skill
bundles its own copy of the g_query wheel in dist/ alongside this script, so
it's self-contained -- it does not depend on any other project's dist/
folder existing on disk.

Usage:
    python check_setup.py                # diagnose only
    python check_setup.py --install      # also install the package + Chromium
    python check_setup.py --profile-dir PATH --wheel-glob PATTERN

Exit code 0 means "ready to query"; non-zero means at least one step is
still needed (see the printed report for which one). The sign-in step can
never be auto-fixed -- it requires a human to actually log into Google in a
visible browser window -- --install will still tell you the exact command
for that step.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_WHEEL_GLOB = str(SKILL_ROOT / "dist" / "g_query-*.whl")

# Checked after the bundled wheel, in case this skill was copied somewhere
# without its dist/ folder, or the user is developing g_query itself.
FALLBACK_WHEEL_GLOBS = [
    "dist/g_query-*.whl",
    "../dist/g_query-*.whl",
    "../../dist/g_query-*.whl",
]

PLAYWRIGHT_CACHE_CANDIDATES = [
    Path.home() / "AppData" / "Local" / "ms-playwright",  # Windows
    Path.home() / "Library" / "Caches" / "ms-playwright",  # macOS
    Path.home() / ".cache" / "ms-playwright",  # Linux
]


def check_g_query_importable() -> tuple[bool, str]:
    spec = importlib.util.find_spec("g_query")
    if spec is None:
        return False, "not installed"
    try:
        import g_query  # noqa: PLC0415

        return True, getattr(g_query, "__version__", "unknown version")
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"installed but failed to import: {exc}"


def find_wheel(extra_glob: str | None) -> str | None:
    globs = ([extra_glob] if extra_glob else []) + [BUNDLED_WHEEL_GLOB] + FALLBACK_WHEEL_GLOBS
    for pattern in globs:
        matches = sorted(glob.glob(pattern, recursive=True))
        if matches:
            return matches[-1]
    return None


def check_chromium_installed() -> bool:
    for candidate in PLAYWRIGHT_CACHE_CANDIDATES:
        if candidate.exists() and any(candidate.glob("chromium-*")):
            return True
    return False


def check_profile_signed_in(profile_dir: str) -> bool:
    path = Path(profile_dir).expanduser()
    if not path.exists():
        return False
    # A fresh, never-used profile dir is essentially empty. A profile that's
    # been through at least one real browser session has substantially more
    # in it (cookies, local storage, etc.) -- this is a heuristic, not proof
    # of a valid login, but catches the common "never set up" case cheaply.
    try:
        total_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return False
    return total_size > 1_000_000  # ~1MB: rough "has real browsing data" threshold


def run(cmd: list[str]) -> bool:
    print(f"       $ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-dir",
        default=str(Path.home() / ".g-query" / "profile"),
        help="Persistent Chromium profile directory to check (default: ~/.g-query/profile)",
    )
    parser.add_argument(
        "--wheel-glob",
        default=None,
        help="Extra glob pattern to search for the g_query wheel, checked before the bundled one",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Actually install the package and Chromium binary if missing "
        "(the sign-in step still always requires a human)",
    )
    args = parser.parse_args()

    print("g-query environment check")
    print("=" * 40)

    ok = True

    installed, detail = check_g_query_importable()
    if installed:
        print(f"[OK]   g_query importable ({detail})")
    else:
        wheel = find_wheel(args.wheel_glob)
        if args.install and wheel:
            print(f"[FIX]  Installing bundled wheel: {wheel}")
            if run([sys.executable, "-m", "pip", "install", "--only-binary=:all:", wheel]):
                installed, detail = check_g_query_importable()
        if installed:
            print(f"[OK]   g_query importable ({detail})")
        else:
            ok = False
            print(f"[FAIL] g_query not importable ({detail})")
            if wheel:
                print(f"       Found a wheel: {wheel}")
                print(f'       Fix: pip install --only-binary=:all: "{wheel}"')
            else:
                print("       No wheel found (bundled dist/ is missing or empty).")
                print("       g_query isn't published to PyPI -- rebuild it from source in")
                print("       the original project and copy the wheel into this skill's dist/,")
                print("       or point --wheel-glob at one.")

    chromium_ok = check_chromium_installed()
    if not chromium_ok and args.install:
        print("[FIX]  Installing Chromium via Playwright...")
        if run([sys.executable, "-m", "playwright", "install", "chromium"]):
            chromium_ok = check_chromium_installed()
    if chromium_ok:
        print("[OK]   Playwright Chromium binary is installed")
    else:
        ok = False
        print("[FAIL] Playwright Chromium binary not found")
        print("       Fix: playwright install chromium")

    profile_ok = check_profile_signed_in(args.profile_dir)
    if profile_ok:
        print(f"[OK]   Profile at {args.profile_dir} looks like it has real browsing data")
    else:
        ok = False
        print(f"[FAIL] Profile at {args.profile_dir} looks empty/fresh (heuristic, not certain)")
        print(f'       Fix (needs a human -- cannot be automated): g-query "hello world" --headed --profile-dir "{args.profile_dir}"')
        print("       Log into Google by hand in the window that opens, then confirm it")
        print("       prints a JSON result and exits on its own.")

    print("=" * 40)
    print("READY to query." if ok else "NOT ready yet -- see [FAIL] items above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
