---
name: webots-camera-vision-bridge
description: Capture a real image from a running Webots simulation for Claude to view and reason over directly -- either a named Camera device's own view, or (when no camera exists in the world) the whole 3D viewport via Supervisor.exportImage() -- with built-in cost controls (automatic downscaling, and a change-detection mode that skips re-viewing an unchanged scene) since every view a vision model takes of a captured frame has a real cost proportional to image size. This is the practical, buildable version of "Vision-Language-Action orchestration" -- no separate vision-language model needs to be hosted or fine-tuned, since Claude already reasons over images natively. Use this whenever a task needs to actually see the simulated scene (locate an object, read a state off a simulated display, verify a visual outcome) rather than infer it from telemetry alone -- but treat every capture-and-view as a deliberate, costed action, not something to do on a timer or "just in case."

## Use this sparingly and deliberately -- cost discipline is part of this skill, not an afterthought

The screenshot itself is free; a vision-capable model actually **looking**
at it is not, and cost scales with image resolution. Follow this order of
preference every time, don't reach for a capture by default:

1. **Prefer telemetry/Supervisor state first.** If the question can be
   answered from joint angles, a Supervisor position, or any other numeric
   state this project's own SIP/webots_bridge layer already exposes, answer
   it from that — it's free and already proven reliable. Only fall back to
   a visual capture when the question is genuinely about *what something
   looks like* (is the object actually inside the gripper's jaws, does a
   simulated display show the right state) — something no numeric signal
   answers.
2. **Never capture on a timer or poll loop "just to watch."** Capture at a
   specific decision point (after a grasp attempt, before confirming a
   placement) — one deliberate look, not a running video feed sampled every
   few seconds.
3. **Use `--skip-if-unchanged` for any repeated check on the same question**
   (e.g. "has the state I'm watching for finally changed"). It downscales
   and hashes the new capture, compares it against the last capture at the
   same `--out` path, and prints `UNCHANGED` instead of a path when nothing
   meaningfully moved — that's the signal to *not* spend a view on it this
   round, not just a note in a log.
4. **Leave `--max-dimension` at its default (768) unless a task specifically
   needs finer detail.** A raw `--viewport` capture came back at full
   desktop resolution (2560x1440, 6+MB) in direct testing; downscaled by
   default to roughly 543x768 (~150KB) — confirmed empirically, not assumed
   — with no loss of information relevant to normal scene-understanding
   questions ("where is the cube," "is the gripper open"). Only raise it for
   a task that genuinely needs to read small text or fine detail off the
   simulated scene.
5. **Capture only the one mode the question actually needs.** Don't grab
   both a camera-device frame and a viewport capture "to be safe" — pick
   whichever answers the actual question (see "Choosing a mode" below) and
   view only that one.

# Webots Camera / Vision Bridge

One script, two capture modes, both tested against a real running Webots
world (a camera device gives real floor-texture image data; `--viewport`
gives a real full 3D-view capture including the background/sky and arena
edge) — not just written and assumed to work. Both cost controls above
(downscaling, change-detection) are tested too, not just described: a
6+MB/2560x1440 raw capture came back downscaled to ~150KB/543x768, and two
consecutive captures of an unchanged static scene correctly produced a path
then `UNCHANGED` on the second call.

## Usage

```bash
# A Camera device exists on the connecting robot:
WEBOTS_CONTROLLER_URL=ipc://1234/ARM_LEFT python scripts/capture_camera_frame.py --camera-name gripper_camera --out frame.png

# No camera device in the world -- capture the whole viewport instead
# (needs `supervisor TRUE` on the connecting robot):
WEBOTS_CONTROLLER_URL=ipc://1234/ARM_LEFT python scripts/capture_camera_frame.py --viewport --out frame.png

# Repeated check for a state change, without re-viewing every unchanged frame:
WEBOTS_CONTROLLER_URL=ipc://1234/ARM_LEFT python scripts/capture_camera_frame.py --viewport --out frame.png --skip-if-unchanged
```

Prints the saved path on success, or `UNCHANGED` (see cost-discipline
section above — that means don't bother viewing this one). When a path
comes back, then `Read` it directly — that's the entire "VLA" step: Claude
looks at the image and reasons about what's in it, in the same
conversation, with no separate model in between.

## Three real gotchas found empirically, not assumed

1. **Identify the target robot via `WEBOTS_CONTROLLER_URL`, not a plain
   robot-name variable.** An earlier draft of this skill (and of
   `webots-sim-launch-debug`'s `check_robot_pose.py`) assumed
   `WEBOTS_ROBOT_NAME=<name>` was enough to pick which extern-controlled
   robot to connect as. Tested directly against a real running world: it
   was not — Webots reported "No robot name provided" and listed every
   extern-controlled robot visible across every running instance. The
   actual mechanism is the full connection URL Webots itself prints via its
   `--extern-urls` flag (e.g. `ipc://1234/ARM_LEFT`) — confirmed by reading
   this exact project's own already-working connection code
   (`webots_bridge/connection_worker.py`), which sets
   `os.environ["WEBOTS_CONTROLLER_URL"]`, not a robot-name variable.

2. **`Supervisor.exportImage()`'s Python binding does not return a
   meaningful status code.** A first version checked `if result != 0` and
   reported failure on every single call — even ones that produced a
   perfectly valid multi-megabyte image file. Tested directly: the call
   returns `None` regardless of outcome in this binding. The only reliable
   success signal is checking the output file actually landed on disk with
   non-zero size afterward, which is what the script does now.

3. **`Camera.saveImage()` and `Supervisor.exportImage()` are not
   API-consistent with each other despite looking like siblings.**
   `saveImage()` genuinely does return a real integer status code (0 =
   success) — confirmed by the camera-mode test succeeding without
   tripping its own `!= 0` check — while `exportImage()` doesn't, per #2.
   Don't assume one method's return-value contract transfers to a
   similar-looking method on a different class just because the call shape
   looks the same.

4. **The multi-extern lock-step requirement (pitfalls doc item 6) is not
   a one-time, connect-phase-only rule — it holds continuously for the
   whole session.** Confirmed directly while debugging a visual-verify
   loop: a one-shot capture script (connect → `--warmup-steps` → save →
   exit) is fine used ONCE, but a *second* extern robot that stays
   connected and stepping in the background (e.g. a driver holding a knob
   at a target angle) hangs the moment the camera script disconnects —
   Webots keeps that robot's step() blocked waiting for a NEW connection
   on the now-vacant slot, not just at initial startup. A repeated
   "connect camera, capture, disconnect" pattern therefore stalls every
   OTHER long-lived extern connection in the same world after the first
   disconnect, not merely during the very first handshake. If a script
   needs several captures across a session while another robot is being
   actively driven throughout, either keep the camera connection open too
   (see gotcha #5) or accept a robot needs to be freshly (re)connected
   after any camera disconnect before it steps again.

5. **Calling `Camera.saveImage()` more than once on the SAME live
   connection is unreliable** — confirmed directly: a persistent capture
   loop (one connection, camera enabled once, `saveImage()` called
   repeatedly on trigger across many simulation steps) produced a genuine
   file for its first call, then printed a real Webots-side "Insufficient
   permissions to write file" warning on every subsequent call to a
   *different* output path in the same session, and no file was written
   for any of those later calls, despite nothing else about the calling
   code changing between them. The one-shot pattern (fresh `Robot()`
   connection → enable → warm up → save → exit) that this script already
   uses has never shown this failure. Until root-caused further, treat
   repeated saves on one open connection as unreliable and prefer
   reconnecting fresh for each individual capture, exactly as this
   script's own one-shot design already does — don't build a
   "stay-connected, save on demand" camera loop expecting every save after
   the first to work.

## Choosing a mode

- **`--camera-name`**: gives the real sensor view a vision pipeline would
  actually consume — an eye-in-hand or fixed camera's own image, at
  whatever resolution/field-of-view the `Camera` node was configured with.
  Use this whenever the task is about what the robot itself would see.
- **`--viewport`**: gives situational awareness of the *whole* scene (both
  arms, the workpiece, the arena) regardless of whether any camera device
  exists — the same picture a human watching the simulator window sees.
  Needs `supervisor TRUE` on the connecting robot, since `exportImage` is a
  Supervisor-only capability. Use this as the default fallback for "let me
  look at the simulation" when the world wasn't built with a robot's-eye
  camera in mind.
- **In practice, on a Windows Webots install, prefer `--camera-name` even
  when `--viewport` looks like the more natural fit.** Confirmed repeatedly
  in this project (see `reference/knowledge/webots-simulation-pitfalls.md`
  item 13): `--viewport` consistently returned a blank/dark image on this
  environment across many attempts (with and without `--minimize`), while a
  real `Camera` device on a small, purpose-built "observer" robot added
  temporarily to the world under test consistently returned a correct
  frame. If a world doesn't already have a camera positioned usefully,
  adding one costs nothing structural — see the pattern below — and is more
  reliable here than the theoretically-simpler `--viewport` call.

## Self-debugging a visual defect yourself, without relying on the user to look and report back

The scenario this section exists for: something about a generated or
modified object's *appearance* is suspect — an orientation, a color, a
texture, a geometric proportion — and the only way to know for certain is
to actually render it and look, not to re-read the generator code or the
VRML more carefully. Static analysis and structural checks
(`webots-proto-sanity-check`) and clean joint telemetry are real but
orthogonal signals — a shape can be rotated 90 degrees from correct, or
textured upside-down, while every structural check stays green and every
joint reports exactly its commanded value, because none of those checks
look at the rendered pixels. Confirmed directly in this project (pitfalls
doc items 12 and 13): a console's screen orientation and its buttons'
90-degree tilt both slipped past every automated check that existed at the
time, and were only found by actually capturing and viewing a frame.

The loop:

1. **Build the smallest world that isolates the question.** Don't debug an
   orientation/geometry doubt inside the full target scene — build a
   throwaway `.wbt` with just the one suspect element (or, for an even
   sharper isolation, a single plain primitive with zero rotation applied,
   to nail down an engine default before blaming your own math on top of
   it — this is exactly how pitfalls item 13 found Webots' Cylinder default
   axis was Z, not the assumed Y).
2. **Add a small observer robot with a `Camera` device**, positioned and
   oriented to frame the suspect area, per the "prefer `--camera-name`"
   guidance above. Give it `controller "<extern>"` like any other
   externally-driven robot in this project.
3. **If the world already has another `<extern>`-controlled robot in it**
   (e.g. the object under test itself, declared as a Supervisor but not
   what you're actually here to test), it still needs *something* connected
   to it or your observer robot's own `step()` will hang forever — this
   project's multi-extern lock-step rule (pitfalls item 6) applies to a
   temporary debug world exactly as much as a real one. Run a minimal no-op
   keep-alive controller against it in the background:
   ```python
   from controller import Robot
   robot = Robot()
   timestep = int(robot.getBasicTimeStep())
   while robot.step(timestep) != -1:
       pass
   ```
4. **Launch the world** (via `webots-sim-launch-debug`'s
   `launch_with_logs.py`, so a structural warning surfaces at the same time
   as the visual capture), then **capture and actually `Read` the image
   yourself** — don't describe what you expect it to show, look at what it
   actually shows.
5. **Name the concrete mismatch** against what correct should look like
   (a flat circular top vs. a curved "D"/crescent sliver; a texture right-
   side-up vs. upside-down) — specific enough that the fix is obvious, the
   same standard `webots-object-design`'s self-check loop already holds
   generated objects to.
6. **Fix the root cause, not the symptom.** If a rotation/orientation
   assumption turns out wrong, re-derive it from the *confirmed* correct
   axis/behavior (Rodrigues' rotation formula worked twice in this project
   for exactly this), not by nudging a sign or angle until one specific
   case looks right — a nudge that isn't re-derived from the actual
   confirmed default tends to fix one case while silently breaking another.
7. **Re-capture and re-view after the fix**, on the same isolated world,
   before touching the real target scene — confirm the specific defect you
   named in step 5 is actually gone, not just that the file regenerated
   without errors.
8. **Clean up.** Stop every Webots/keep-alive process this loop started and
   delete the throwaway world file once the fix is confirmed and carried
   over to the real object.

## What this does not replace

This is not a substitute for `webots-sim-launch-debug`'s console-log
capture when verifying something structural (PROTO warnings, collision
geometry) — rendering can look completely correct while collision geometry
is silently broken, since neither a camera frame nor a viewport capture
exercises physics/collision at all. Use this skill for "what does the scene
actually look like," and the console-capture skill for "is the simulation
actually structurally correct."
