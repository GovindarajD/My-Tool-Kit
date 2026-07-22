"""Catch a falling/drifting robot automatically by watching its actual
world-frame pose over time -- not just its joint sensors.

Why this exists: while debugging the SO-101 dual-arm workstation, every
joint-angle telemetry check looked perfectly correct (shoulder_pan,
elbow_flex, etc. all settled near 0) while the ENTIRE robot was silently
falling through the floor, because joint sensors only measure the angle
BETWEEN adjacent links -- they say nothing about the whole assembly's
position in the world. A rigid multi-body chain free-falling together, with
each joint's own motor holding its own commanded relative angle, reads as
"stable" on joint telemetry the entire time it's falling. The bug was only
found by looking at a screenshot, hours later. This script closes that gap
by polling Supervisor-level world position directly, so "the robot didn't
move" is actually checked, not assumed from joint angles alone.

Requirements:
  - The target Robot node's `controller` field must be `"<extern>"` in the
    world file (same as any extern-controlled robot).
  - Identify which robot to connect as via the WEBOTS_CONTROLLER_URL
    environment variable, set to the exact URL Webots printed for that
    robot via its --extern-urls flag (e.g. ipc://1234/ARM_LEFT) -- NOT a
    plain robot-name variable. Confirmed the hard way: an earlier version
    of this docstring claimed WEBOTS_ROBOT_NAME was enough; tested directly
    against a real running world and it was not -- Webots reported "No
    robot name provided" and listed every extern-controlled robot visible
    across every running instance, not just the intended one. Python's
    path must also include Webots' own controller bindings:
        $WEBOTS_HOME/lib/controller/python   (added to sys.path)
    On Windows, also put $WEBOTS_HOME/lib/controller on PATH so the native
    libController DLL resolves.

Usage:
    WEBOTS_CONTROLLER_URL=ipc://1234/ARM_LEFT python check_robot_pose.py --duration 10 --max-drift 0.01

Exits non-zero and prints a warning if the robot's position moved more than
--max-drift meters from its first sampled position at any point during
--duration seconds of real sim time.
"""
from __future__ import annotations

import argparse
import math
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=10.0, help="seconds of sim time to watch")
    parser.add_argument("--max-drift", type=float, default=0.01, help="meters of tolerated movement from the first sample")
    parser.add_argument("--sample-interval", type=float, default=0.5, help="seconds between samples")
    args = parser.parse_args()

    webots_home = os.environ.get("WEBOTS_HOME")
    if not webots_home:
        print("error: WEBOTS_HOME must be set (points at the Webots install)", file=sys.stderr)
        return 2
    sys.path.insert(0, os.path.join(webots_home, "lib", "controller", "python"))

    try:
        from controller import Supervisor
    except ImportError as exc:
        print(f"error: could not import Webots' controller module: {exc}", file=sys.stderr)
        print("check WEBOTS_HOME and that this process was launched the way an extern controller expects", file=sys.stderr)
        return 2

    robot = Supervisor()
    timestep = int(robot.getBasicTimeStep())
    self_node = robot.getSelf()

    first_position = None
    max_observed_drift = 0.0
    elapsed = 0.0
    next_sample_at = 0.0

    while elapsed < args.duration:
        if robot.step(timestep) == -1:
            break
        elapsed += timestep / 1000.0
        if elapsed < next_sample_at:
            continue
        next_sample_at += args.sample_interval

        position = self_node.getPosition()
        if first_position is None:
            first_position = position
            print(f"t={elapsed:.2f}s: baseline position = {position}")
            continue

        drift = math.dist(position, first_position)
        max_observed_drift = max(max_observed_drift, drift)
        print(f"t={elapsed:.2f}s: position={position} drift={drift:.5f}m")

    if first_position is None:
        print("error: never got a single successful step -- is the world actually running?", file=sys.stderr)
        return 2

    if max_observed_drift > args.max_drift:
        print(
            f"\nFAIL: robot moved {max_observed_drift:.4f}m from its starting position "
            f"(tolerance {args.max_drift}m). If this wasn't a commanded motion, check "
            f"whether the root Robot node has an unanchored `physics` block (see "
            f"webots-proto-sanity-check).",
            file=sys.stderr,
        )
        return 1

    print(f"\nOK: max drift {max_observed_drift:.5f}m stayed within tolerance ({args.max_drift}m)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
