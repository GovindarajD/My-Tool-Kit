"""Launch Webots and capture what CAN actually be captured from the command
line -- and be honest about what can't.

Empirically confirmed on Webots R2025a (Windows), after two wrong
assumptions got tested and rejected in turn -- don't take either claim
below on faith, they're recorded so the next person doesn't repeat the same
detours:

1. WRONG: "pass `--stdout=<file>` / `--stderr=<file>`". These flags take NO
   argument at all -- `webots --help` shows them as bare flags that relay a
   CONTROLLER's own print()/stderr into Webots' own process stdout/stderr.

2. WRONG (a g-query answer, sourced from Webots R2021a docs -- always check
   the doc's own version before trusting it applies to the version you're
   actually running): "redirecting the whole `webots.exe` process's OS-level
   stdout/stderr via `subprocess.Popen(stdout=file, stderr=file)` captures
   Webots' internal console". Tested directly: produced a completely empty
   file even with `--stdout --stderr --extern-urls` all passed together.

What DOES actually work, confirmed by direct comparison against this
project's own `webots_bridge/process_manager.py` (which reliably reads
`--extern-urls` announcements every single run): open Webots as a real
**async subprocess with a PIPE**, not a plain file handle -- Windows'
process-chain (the `webots` wrapper spawning a separate `webots-bin` child)
does not appear to inherit/flush a redirected file handle the same way it
serves a piped stream. This script mirrors that proven-working shape.

What this still does NOT capture, confirmed by direct empirical test: any
of Webots' own INTERNAL engine warnings/errors (PROTO load problems, mesh/
shadow-casting warnings, contact-point notices). Those live in a GUI-bound
Console panel with no confirmed CLI capture route in this version, piped
stdout or otherwise. For that class of message, the only confirmed reliable
method is a direct look at the running Console panel -- a screenshot via
Webots' own native "Take Screenshot" toolbar action reads the real
framebuffer and is more reliable than an OS-level desktop screenshot, which
can come back completely blank if the OpenGL surface isn't composited the
way the capture API expects (also empirically hit and confirmed in this
same project).

Usage:
    python launch_with_logs.py path/to/world.wbt [--webots-home DIR] [--mode fast|realtime|pause] [--log FILE]

Prints JSON: {"pid": <int>, "log": <path>} and keeps running, draining the
piped stream into --log for as long as this process stays alive (Ctrl+C to
stop draining; the launched Webots process is independent and survives
this script exiting).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def default_webots_home() -> Path | None:
    env = os.environ.get("WEBOTS_HOME")
    if env:
        return Path(env)
    candidates = [
        Path(r"C:\Program Files\Webots"),
        Path.home() / "Applications" / "Webots.app",
        Path("/usr/local/webots"),
        Path("/Applications/Webots.app"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def find_executable(webots_home: Path) -> Path:
    candidates = [
        webots_home / "msys64" / "mingw64" / "bin" / "webots.exe",
        webots_home / "webots.exe",
        webots_home / "webots",
        webots_home / "Contents" / "MacOS" / "webots",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(f"could not find a webots executable under {webots_home}")


async def _drain(process: asyncio.subprocess.Process, log_path: Path) -> None:
    with open(log_path, "ab", buffering=0) as f:
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            f.write(line)


async def run(world_path: str, webots_home: Path, mode: str, minimize: bool, log_path: Path) -> None:
    executable = find_executable(webots_home)
    # --clear-cache is not optional for iterative PROTO/world editing: Webots
    # caches parsed PROTOs across launches and does NOT reliably detect that
    # a local file changed. Confirmed the hard way -- a real fix (renaming
    # invalid boundingObject Pose nodes to Transform) kept reporting the
    # exact pre-fix warning text on every relaunch until this flag was added,
    # even with the file itself confirmed correct on disk and every stale
    # process confirmed killed first.
    cmd = [str(executable), "--extern-urls", f"--mode={mode}", "--stdout", "--stderr", "--batch", "--clear-cache"]
    if minimize:
        cmd.append("--minimize")
    cmd.append(str(Path(world_path).resolve()))

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    print(json.dumps({"pid": process.pid, "log": str(log_path)}), flush=True)
    await _drain(process, log_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("world_path")
    parser.add_argument("--webots-home", default=None)
    parser.add_argument("--mode", default="fast", choices=["fast", "realtime", "pause"])
    parser.add_argument("--minimize", action="store_true")
    parser.add_argument("--log", default="webots.log")
    args = parser.parse_args()

    webots_home = Path(args.webots_home) if args.webots_home else default_webots_home()
    if webots_home is None:
        print("error: could not locate Webots install; pass --webots-home or set WEBOTS_HOME", file=sys.stderr)
        return 2

    log_path = Path(args.log).resolve()
    asyncio.run(run(args.world_path, webots_home, args.mode, args.minimize, log_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
