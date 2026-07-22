---
name: webots-proto-sanity-check
description: Statically scans a Webots .proto file for the specific defect classes that cause silent PROTO-load failures, falling/runaway robots, missing collision geometry, and visual-mesh corruption -- invalid leading-digit DEF identifiers, a fixed-base robot's root Robot node carrying its own physics, mesh-based boundingObjects (urdf2webots's --box-collision flag does not reliably produce Box primitives), a mesh DEF'd node reused via USE for both a visual Shape and a boundingObject (unsafe to blind-replace by name), and a boundingObject containing a transform-type node (Pose/Transform) nested inside another transform-type node (Webots silently drops it -- collision geometry vanishes with no load error, and it still renders and passes joint-governance tests fine, since neither exercises collision). Use this immediately after any urdf2webots conversion, after hand-writing or hand-editing a PROTO's boundingObject/physics fields, or when a robot loads but renders wrong, falls through the floor, drifts/vanishes only once a controller actually connects, or objects pass through it that shouldn't. Run it before first launch, not after debugging for hours.
---

# Webots PROTO Sanity Check

Every check this skill runs corresponds to a real bug that shipped and took
real debugging time to find by hand while building a Webots dual-arm
workstation from a URDF conversion (see
`reference/knowledge/webots-simulation-pitfalls.md` in that project for the
full incident writeups). This skill exists so the same four defect classes
get caught by a script in seconds, next time, instead of by hours of
"why is the arm falling through the floor" / "why did the motor housing
disappear" investigation.

## When to run this

- Immediately after converting a URDF with `urdf2webots`, before ever
  launching the generated world.
- After hand-editing a PROTO's `boundingObject` or `physics` fields (e.g.
  simplifying collision geometry) -- these edits are exactly what caused
  two of the four defect classes in the first place.
- When a robot in Webots renders correctly at first glance but: falls
  through the floor, drifts/vanishes only after an extern controller
  connects (not when the `.wbt` is just opened and left paused), or a part
  of its visual mesh looks wrong/missing after a collision-geometry edit.

## Running it

```bash
python .claude/skills/webots-skills/webots-proto-sanity-check/scripts/check_proto.py path/to/Something.proto
```

Exits non-zero if any FAIL-level finding exists (safe to wire into a
pre-launch check or CI). WARN-level findings are reported but don't fail --
they need a human judgment call (e.g. "is this Mesh boundingObject actually
a problem, or is this part small/simple enough that it's fine").

## What it checks, and why each one is real

1. **Leading-digit `DEF` identifiers** — VRML/X3D identifiers must match
   `[a-zA-Z_][a-zA-Z0-9_-]*`. A URDF material or link name starting with a
   digit (`3d_printed`, `100_grey`) is a very common real name that silently
   breaks the *entire* PROTO with no error pointing at the actual cause —
   Webots just fails to load it.

2. **Physics on the root `Robot` node** — a fixed-base manipulator's base
   must be immovable. If the root node has its own `physics Physics {...}`
   block, the whole robot is an unconstrained rigid body: nothing anchors
   it, so it falls under gravity. This is easy to miss because it only
   manifests once the simulation is actually *stepping* (an extern
   controller connecting and calling `step()`), not when the `.wbt` file is
   simply opened and left paused at t=0 — so "I opened it and it looked
   fine" is not evidence this is OK. Every other link should still keep its
   own physics; only the root should not.

3. **Mesh-based `boundingObject`** — `urdf2webots --box-collision` does
   **not** reliably produce `Box` primitives (a confirmed, known limitation,
   not a one-off misconfiguration) — check the *output* for actual `Box {`
   nodes rather than trusting the flag name. A `boundingObject` that reuses
   the full, high-poly, concave visual mesh directly as collision geometry
   causes real CAD parts' legitimate small overlaps at every mounting joint
   to be treated as violent contacts by the physics engine — thousands of
   spurious contact points on a single articulated arm at rest, and visibly
   corrupted rendering under that load.

4. **A DEF'd mesh reused via `USE` for both a visual `Shape` and a
   `boundingObject`** — this is a trap specifically for whatever script
   fixes finding #3: replacing every `USE <name>` occurrence by node name
   alone will also silently break the *visual* instances of a part that's
   legitimately reused for rendering (e.g. five identical servo housings on
   one arm, only the first fully `DEF`'d, the other four `USE`'d purely for
   display). Any fix here must parse brace-context (inside `boundingObject`
   vs. inside `Shape`) rather than doing a blind text replace.

5. **A transform-type node nested inside another transform-type node,
   inside a `boundingObject`** — found the hard way, live, only after
   building real Webots console capture (see `webots-sim-launch-debug`):
   Webots rejects `Pose`-in-`Pose` (and, confirmed by direct retest,
   `Transform`-in-`Transform` equally) inside a `boundingObject`, even
   though the exact same nesting is completely normal and safe in the
   *visual* `Shape` tree elsewhere in the same file. This is the single
   most dangerous finding here because it fails *silently* — no load
   error, the robot still renders correctly (rendering uses the visual
   tree, untouched), and joint-governance/telemetry tests still pass
   (position control doesn't need working collision geometry) — until
   something actually tries to collide with the affected link. If you're
   fixing #3 by nesting a `Box` inside an offset wrapper inside the mesh's
   original transform, this is exactly the shape that gets silently
   dropped. Fix: flatten to a single wrapper node, composing the two
   translations/rotations yourself (rotate the inner offset vector by the
   outer node's axis-angle rotation — Rodrigues' rotation formula — then
   add), rather than relying on node nesting inside `boundingObject`.

## If you need to actually fix a mesh-based boundingObject

Computing a correct replacement `Box` needs the mesh's real local AABB
(from its STL file) and a *single, flattened* transform composing the
visual `Pose`'s translation/rotation with the box's own local AABB-center
offset (finding #5 above — nesting two wrapper nodes inside
`boundingObject` the way the visual tree safely allows will be silently
dropped). This skill's script only *detects* both problems; if you want
the fix scripted rather than hand-done, say so and it can be built out from
the same brace-parsing approach `check_proto.py` already uses to locate
each `boundingObject` span, plus the rotation-composition math described
above.

## After making any fix here: verify with real console capture, not just
   a re-read of the file

A fix that looks structurally correct on paper (and did, twice, for finding
#5) can still be silently rejected by Webots' own parser. Confirming a fix
actually landed needs `webots-sim-launch-debug`'s real console capture
(with `--clear-cache`, or a stale cached parse will keep reporting the
pre-fix warning regardless of what the file now says) — not another read
of the PROTO text, and not just a screenshot (rendering doesn't exercise
collision geometry at all).
