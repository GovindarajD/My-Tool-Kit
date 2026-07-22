"""Get a real image out of a running Webots simulation for Claude (or any
other vision-capable model) to view directly -- the practical, buildable
version of "Vision-Language-Action orchestration": no separate VLM
(LLaVA/PaliGemma/etc.) needs to be hosted or fine-tuned, since Claude
already reasons over images. This script's only job is getting a frame
onto disk; the semantic reasoning happens in the calling conversation.

COST MATTERS HERE. The expensive step isn't taking the screenshot -- it's
a vision-capable model actually looking at it, and image cost scales with
resolution. This script builds two concrete controls in rather than leaving
"use this sparingly" as a comment nobody reads:

  --max-dimension (default 768)   Downscales the raw capture before it ever
                                   reaches disk for a caller to view. A raw
                                   Supervisor.exportImage() capture came
                                   back at 2560x1440 (6+MB) in direct
                                   testing -- full desktop resolution is
                                   almost never needed for scene
                                   understanding, and every extra pixel is
                                   tokens a vision model has to spend
                                   reading it.

  --skip-if-unchanged             Compares the new capture's perceptual
                                   hash against the previous capture at the
                                   SAME --out path (a sidecar .phash file).
                                   If the scene hasn't meaningfully changed,
                                   prints "UNCHANGED" instead of a path --
                                   the signal a caller should treat as "do
                                   not spend a vision call re-viewing this,
                                   nothing new happened." Use this for any
                                   repeated/polling-style check (e.g.
                                   "did the grasp succeed yet") instead of
                                   viewing every single capture regardless
                                   of whether the scene moved.

Two capture modes, since a world doesn't always have a Camera device:

  --camera-name <name>   Reads a named Camera device attached to the robot
                          this script connects as. Gives an eye-in-hand or
                          fixed-camera view exactly as the robot itself
                          would see it -- the real sensor data an actual
                          vision pipeline would consume.

  --viewport              Uses Supervisor.exportImage() to capture the
                          WHOLE 3D view Webots is currently rendering --
                          the same picture a human watching the simulator
                          window sees, needs no Camera device at all, only
                          `supervisor TRUE` on the connecting robot. Use
                          this as the fallback whenever the world has no
                          camera wired up yet, or to get situational
                          awareness of the whole scene rather than one
                          robot's eye view.

Requirements (same extern-controller pattern as
webots-sim-launch-debug/scripts/check_robot_pose.py):
  - The target Robot node's `controller` field must be `"<extern>"`.
  - For --viewport: that Robot node must also have `supervisor TRUE`.
  - Identify which robot to connect as via WEBOTS_CONTROLLER_URL (the
    exact ipc://.../<name> or tcp://.../<name> URL Webots prints via its
    --extern-urls flag) -- NOT a plain robot-name env var. Confirmed the
    hard way: an earlier version of this project's scripts assumed
    WEBOTS_ROBOT_NAME was enough and it silently was not (Webots reported
    "No robot name provided" and listed every extern-controlled robot it
    could see across every running instance, not just the intended one).
  - Run with WEBOTS_HOME set; this script adds
    $WEBOTS_HOME/lib/controller/python to sys.path itself.

Usage:
    # camera device present:
    WEBOTS_CONTROLLER_URL=ipc://1234/ARM_LEFT python capture_camera_frame.py --camera-name gripper_camera --out frame.png

    # no camera device -- whole-viewport fallback (needs supervisor TRUE):
    WEBOTS_CONTROLLER_URL=ipc://1234/ARM_LEFT python capture_camera_frame.py --viewport --out frame.png

    # polling a slow state change without re-viewing every unchanged frame:
    WEBOTS_CONTROLLER_URL=ipc://1234/ARM_LEFT python capture_camera_frame.py --viewport --out frame.png --skip-if-unchanged

Prints the saved image path on success, or "UNCHANGED" if
--skip-if-unchanged was set and the scene didn't meaningfully change.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _downscale_in_place(path: Path, max_dimension: int) -> None:
    from PIL import Image
    with Image.open(path) as img:
        if max(img.size) <= max_dimension:
            return
        scale = max_dimension / max(img.size)
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img.convert("RGB").resize(new_size, Image.LANCZOS).save(path)


def _perceptual_hash(path: Path) -> str:
    """A simple 64-bit average-hash: cheap, dependency-free (just PIL,
    already required for downscaling), plenty sensitive for "did the scene
    actually change" -- not meant to be a robust image-similarity metric,
    just a fast difference signal."""
    from PIL import Image
    with Image.open(path) as img:
        small = img.convert("L").resize((8, 8), Image.LANCZOS)
        pixels = list(small.getdata()) if not hasattr(small, "get_flattened_data") else list(small.get_flattened_data())
    avg = sum(pixels) / len(pixels)
    return "".join("1" if p >= avg else "0" for p in pixels)


def _hamming_distance(a: str, b: str) -> int:
    return sum(c1 != c2 for c1, c2 in zip(a, b))


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--camera-name", help="a Camera device's own `name` field")
    mode.add_argument("--viewport", action="store_true", help="capture the whole 3D view instead (needs supervisor TRUE)")
    parser.add_argument("--out", default="frame.png")
    parser.add_argument("--warmup-steps", type=int, default=5,
                         help="steps to run before capturing -- a camera enabled this same tick has not rendered a real frame yet")
    parser.add_argument("--max-dimension", type=int, default=768,
                         help="downscale so the longer edge is at most this many pixels -- keeps vision-model cost down; 0 disables downscaling")
    parser.add_argument("--skip-if-unchanged", action="store_true",
                         help="compare against the previous capture at this --out path; print UNCHANGED instead of re-emitting a path to view if the scene didn't meaningfully change")
    parser.add_argument("--change-threshold", type=int, default=4,
                         help="Hamming distance (out of 64) above which the scene counts as changed -- higher = less sensitive")
    args = parser.parse_args()

    webots_home = os.environ.get("WEBOTS_HOME")
    if not webots_home:
        print("error: WEBOTS_HOME must be set (points at the Webots install)", file=sys.stderr)
        return 2
    sys.path.insert(0, os.path.join(webots_home, "lib", "controller", "python"))

    if "WEBOTS_CONTROLLER_URL" not in os.environ:
        print(
            "error: WEBOTS_CONTROLLER_URL must be set to the exact URL Webots printed "
            "for this robot (via its --extern-urls flag), e.g. ipc://1234/ARM_LEFT -- "
            "a plain robot-name env var is not enough.",
            file=sys.stderr,
        )
        return 2

    try:
        from controller import Robot, Supervisor
    except ImportError as exc:
        print(f"error: could not import Webots' controller module: {exc}", file=sys.stderr)
        return 2

    robot = Supervisor() if args.viewport else Robot()
    timestep = int(robot.getBasicTimeStep())

    out_path = Path(args.out).resolve()

    if args.viewport:
        for _ in range(max(1, args.warmup_steps)):
            if robot.step(timestep) == -1:
                print("error: simulation ended before a frame could be captured", file=sys.stderr)
                return 1
        # Supervisor.exportImage's Python binding does not return a
        # meaningful status code (confirmed directly -- it returned None on
        # a run that produced a perfectly valid file), so the only reliable
        # success signal is checking the file actually landed on disk.
        robot.exportImage(str(out_path), quality=100)
        if not out_path.exists() or out_path.stat().st_size == 0:
            print(f"error: exportImage did not produce a file at {out_path} "
                  f"-- confirm this robot's world-file entry has `supervisor TRUE`", file=sys.stderr)
            return 1
    else:
        camera = robot.getDevice(args.camera_name)
        if camera is None:
            print(f"error: no device named {args.camera_name!r} on this robot -- check the Camera node's `name` field, or use --viewport if there is no camera", file=sys.stderr)
            return 2

        camera.enable(timestep)
        # A camera needs at least one full simulation step after enable() to
        # actually populate a frame -- saving immediately on the same tick it
        # was enabled can capture a blank/stale buffer.
        for _ in range(max(1, args.warmup_steps)):
            if robot.step(timestep) == -1:
                print("error: simulation ended before a frame could be captured", file=sys.stderr)
                return 1

        result = camera.saveImage(str(out_path), quality=100)
        if result != 0:
            print(f"error: Camera.saveImage returned {result} (non-zero = failure)", file=sys.stderr)
            return 1

    if args.max_dimension > 0:
        _downscale_in_place(out_path, args.max_dimension)

    if args.skip_if_unchanged:
        hash_path = out_path.with_suffix(out_path.suffix + ".phash")
        new_hash = _perceptual_hash(out_path)
        if hash_path.exists():
            old_hash = hash_path.read_text(encoding="utf-8").strip()
            if _hamming_distance(old_hash, new_hash) <= args.change_threshold:
                hash_path.write_text(new_hash, encoding="utf-8")
                print("UNCHANGED")
                return 0
        hash_path.write_text(new_hash, encoding="utf-8")

    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
