---
name: webots-arm-inverse-kinematics
description: Solve real inverse kinematics (a Cartesian x/y/z target -> real joint angles) for a Webots-simulated robot arm, using ikpy loaded directly from the arm's own URDF -- confirmed via g-query as the standard, lightweight, URDF-native choice, and verified here against a real 5-DOF arm's URDF with sub-millimeter forward-kinematics error. Use this whenever a task needs an arm to reach a specific position rather than a fixed, scripted joint-angle waypoint (a "pick up the object at (x, y, z)" command instead of "move to this hand-picked pose"), or when reviewing/building a pick-and-place macro that currently uses scripted waypoints and should instead target real coordinates.
---

# Webots Arm Inverse Kinematics (ikpy)

## Why this exists

A scripted sequence of fixed joint-angle waypoints ("reach down, close
gripper, lift" with hand-picked angles) is a real, useful macro, but it is
not IK — it never aims at an actual coordinate and will miss depending on
exactly where an object has settled. This skill closes that gap: load the
same URDF a Webots PROTO was converted from, build a real kinematic chain,
and solve joint angles for a genuine Cartesian target instead.

## Usage

```python
from ik_helper import IKArm

arm = IKArm("path/to/robot.urdf")
angles = arm.solve(target_position=[0.2, 0.0, 0.15])
# angles is ordered to match arm.joint_names -- feed each value into your
# own actuation path in that order (a Webots RotationalMotor.setPosition,
# or a governed action like SIP's set_joint_position).
```

Command-line sanity check against any URDF:

```bash
python scripts/ik_helper.py path/to/robot.urdf --target 0.2 0.0 0.15
```

Prints the solved joint chain, the angles, and a **forward-kinematics
verification** — it re-derives the end-effector position from the solved
angles and reports the error against the requested target. Always check
this number, don't trust a solve() result blindly: an IK solver can
converge to a local optimum that misses badly for an unreachable or
awkwardly-posed target, and the only way to know is to check where the
solution actually puts the end effector.

## Real gotchas found testing this against an actual robot's URDF

1. **ikpy's default `active_links_mask` marks every link active, including
   fixed ones.** A base-mounting link and any fixed end-effector reference
   frame get included in the chain with phantom, meaningless zero-valued
   "joint angles" — ikpy even warns about this itself ("this fixed link
   doesn't provide any transformation so is as it were inactive") but
   doesn't fix its own mask. Confirmed directly: a real 5-DOF arm's URDF
   came back with 7 "active joints" including a `Base link` and a fixed
   gripper reference frame that aren't real motors at all. `IKArm` fixes
   this itself by checking each link's actual `joint_type` (only
   `revolute`/`prismatic` count as active) — if you bypass `IKArm` and call
   `ikpy.chain.Chain` directly, do the same filtering yourself or you'll
   hand a caller more angles than there are real motors to receive them.

2. **A URDF with more than one end-effector branch** (e.g. separate
   leader/follower gripper variants on the same base) needs `base_elements`
   passed explicitly to tell ikpy which branch to build a chain along —
   auto-detection picks one arbitrarily otherwise. `IKArm`'s
   `base_elements` parameter (and the CLI's repeatable `--base-element`
   flag) exists for exactly this.

3. **IK has no idea about your governance/safety limits.** The angles
   `solve()` returns are whatever the kinematic chain says reaches the
   target — they are not automatically clamped to whatever real joint
   limits a safety layer enforces (e.g. this project's own
   `JOINT_LIMITS_RAD` in `so101_bridge/execution.py`). Always validate a
   solved angle against your real limits before sending it to a motor,
   exactly the same way a manually-picked waypoint would need to be —
   IK doesn't grant an exemption from governance, it just picks better
   numbers to govern.

## Dependency

`ikpy` (pip) — not preinstalled; install if the import fails:
`pip install ikpy`. Pulls in `numpy` (usually already present), `scipy`,
and `sympy`.
