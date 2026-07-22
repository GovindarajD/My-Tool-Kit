---
name: webots-sim-launch-debug
description: Launch and debug a running Webots simulation with tools that actually work, not the intuitive-but-wrong approach -- real console capture (Webots' own internal engine warnings/errors are a GUI-bound channel that plain shell redirection, and even --stdout/--stderr with a plain file handle, do NOT reliably capture; a piped async subprocess plus --clear-cache does), and automated Supervisor-level pose-drift detection (joint/device telemetry alone can look perfectly fine while a robot has silently fallen or drifted, since joint sensors only measure relative angles between links, not the whole body's position in the world). Use this whenever scripting/automating a Webots session from a CLI or Python, when "the console shows a warning I can't reproduce in my log," or when debugging "telemetry says X but the sim clearly isn't doing X."
---

# Webots Simulation Launch & Debug

Two tools, built and empirically tested against a real project (a URDF-
converted dual-arm workstation), after two wrong assumptions about how to
capture Webots' console output were each tested and rejected in turn — see
`scripts/launch_with_logs.py`'s docstring for the exact wrong turns, so the
next attempt doesn't repeat them.

## `scripts/launch_with_logs.py` — real console capture

```bash
python launch_with_logs.py path/to/world.wbt --webots-home "C:\Program Files\Webots" --mode fast --minimize --log webots.log
```

Prints `{"pid": <int>, "log": <path>}` immediately, then keeps running,
draining Webots' own process stdout (piped, not file-redirected — a plain
file handle silently produced nothing in direct testing, confirmed to be a
Windows `webots`→`webots-bin` process-chain quirk, not a syntax mistake)
into `--log` for as long as this process stays alive. Ctrl+C stops the
draining; the launched Webots process is independent and keeps running.

**What this reliably captures**: everything Webots' own engine reports —
PROTO load problems, mesh/shadow-casting warnings, physics field validation
messages, and (critically) internal structural rejections like "Cannot
insert Pose node in 'children' field of Pose node in bounding object" that
a plain re-read of the PROTO file would never reveal. This is genuinely the
ground-truth channel — treat a clean run of this over a re-read of the
source file or a rendering screenshot, neither of which can prove collision
geometry is actually valid.

**`--clear-cache` is baked into the launch command and is not optional**
for any edit-relaunch-check loop: Webots caches parsed PROTOs across
launches and does not reliably detect that a local file on disk changed.
Confirmed directly — a real, correct fix kept reporting its exact pre-fix
warning text on every relaunch, file contents on disk notwithstanding,
until this flag was added. If you're iterating on a PROTO fix and the
console still shows the old warning, check this before assuming the fix
didn't work.

**Process hygiene**: before relaunching, confirm no stale Webots processes
are still running from a previous iteration (`Get-Process | Where
ProcessName -like "*webots*"` on Windows) — a leftover instance holding the
default port causes a port-fallback warning that can make you read the
wrong process's log, or two instances' output can interleave in confusing
ways. Kill stale ones first, every time, not just when something looks
wrong.

## `scripts/check_robot_pose.py` — catch a falling/drifting robot
   automatically

```bash
WEBOTS_CONTROLLER_URL=ipc://1234/ARM_LEFT python check_robot_pose.py --duration 10 --max-drift 0.01
```

Run as an extern controller (the target `Robot` node's `controller` field
must be `"<extern>"`, same as any other extern-controlled robot) — needs
`WEBOTS_HOME` set and its controller Python bindings on `sys.path` (the
script adds `$WEBOTS_HOME/lib/controller/python` itself).

Polls the robot's real Supervisor-level world position every
`--sample-interval` seconds for `--duration` seconds, and fails if it moves
more than `--max-drift` meters from its first sampled position. This is the
automated version of a real lesson from the same project: a joint-angle
telemetry check can report a perfectly stable, correctly-settled arm while
the *entire robot* silently free-falls through the floor, because joint
sensors only measure the angle between adjacent links, not the whole
assembly's position — a rigid chain that's free-falling together, with each
joint's own motor still holding its own commanded relative angle, reads as
"stable" on joint telemetry for as long as it keeps falling. That bug was
only found by looking at a screenshot, hours into a debugging session; this
script closes the gap so the next one doesn't need a screenshot to catch
it.

## What this skill deliberately does not attempt

Neither script tries to solve "make the 3D view visible" — a Webots
viewport can render as a blank/black frame under GPU/driver load unrelated
to anything in a PROTO or world file (confirmed as a real, separate,
recurring issue in the same project, not something either script here
fixes). If the console log (via `launch_with_logs.py`) is clean and
`check_robot_pose.py` reports no drift, trust that over a blank-looking
screenshot — the simulation can be correct even when the render isn't
visibly confirming it.
