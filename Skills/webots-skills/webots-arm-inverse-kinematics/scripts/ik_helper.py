"""Solve real inverse kinematics for a Webots-simulated arm from its own
URDF, using ikpy -- confirmed via g-query as the standard, lightweight,
URDF-native choice for exactly this, and verified here directly against a
real robot's URDF (not just assumed to work).

Why this exists: a scripted sequence of fixed joint-angle waypoints (a
"reach down, close gripper, lift" macro with hand-picked angles) is a real,
useful demo, but it is not IK -- it does not aim at any actual (x, y, z)
target and will miss depending on exactly where an object has settled. This
module closes that gap: load the arm's own URDF (the same one the Webots
PROTO was converted from), build a kinematic chain, and solve real joint
angles for a genuine Cartesian target.

Usage as a library:
    from ik_helper import IKArm
    arm = IKArm("path/to/robot.urdf", base_link="base_link", tip_link="gripper_link")
    joint_angles = arm.solve(target_position=[0.2, 0.0, 0.15])
    # joint_angles is ordered to match arm.joint_names -- feed each value
    # into your own actuation path (a Webots RotationalMotor.setPosition,
    # or this project's own SIP set_joint_position action) in that order.

Usage from the command line (quick sanity check against a URDF):
    python ik_helper.py path/to/robot.urdf --target 0.2 0.0 0.15
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence

from ikpy.chain import Chain


@dataclass
class IKArm:
    urdf_path: str
    base_elements: Optional[List[str]] = None  # ikpy's URDF link-name chain, if auto-detection picks the wrong branch (e.g. a URDF with multiple end-effectors, like this project's separate leader/follower grippers)
    active_links_mask: Optional[List[bool]] = None

    def __post_init__(self) -> None:
        kwargs = {}
        if self.base_elements is not None:
            kwargs["base_elements"] = self.base_elements
        self.chain = Chain.from_urdf_file(self.urdf_path, **kwargs)
        if self.active_links_mask is not None:
            self.chain.active_links_mask = self.active_links_mask
        else:
            # ikpy's own default active_links_mask marks EVERY link active,
            # including fixed ones (a base-mounting link, a fixed gripper
            # reference frame, etc.) -- it warns about this itself ("this
            # fixed link doesn't provide any transformation so is as it
            # were inactive") but doesn't fix its own mask. Left alone, a
            # caller gets phantom zero-valued "joints" in the result that
            # don't correspond to any real motor -- confirmed directly
            # against the SO-101 URDF, which has exactly this (a fixed
            # "Base link" and a fixed "gripper_frame_joint" reference link
            # both appeared in the raw output). Only revolute/prismatic
            # (real, motor-driven) links should count as active.
            self.chain.active_links_mask = [
                getattr(link, "joint_type", None) in ("revolute", "prismatic")
                for link in self.chain.links
            ]
        self.joint_names = [
            link.name for link, active in zip(self.chain.links, self.chain.active_links_mask)
            if active
        ]

    def solve(
        self,
        target_position: Sequence[float],
        target_orientation: Optional[Sequence[Sequence[float]]] = None,
        initial_position: Optional[Sequence[float]] = None,
    ) -> List[float]:
        """Returns joint angles (radians) in the same order as
        self.joint_names, for the given (x, y, z) target position in the
        chain's base frame. Pass target_orientation (a 3x3 rotation matrix)
        only if you need the end-effector's orientation constrained too --
        most pick-style tasks only need position."""
        full_solution = self.chain.inverse_kinematics(
            target_position=list(target_position),
            target_orientation=target_orientation,
            orientation_mode="all" if target_orientation is not None else None,
            initial_position=initial_position,
        )
        return [
            angle for angle, active in zip(full_solution, self.chain.active_links_mask)
            if active
        ]

    def forward(self, joint_angles: Sequence[float]) -> List[float]:
        """Sanity-check helper: given joint angles in self.joint_names
        order, returns the resulting end-effector (x, y, z) position --
        use this to verify a solve() result actually reaches the target
        you asked for (IK solvers can converge to a local optimum that
        misses badly for an unreachable target; always check, don't trust
        blindly)."""
        full_angles = [0.0] * len(self.chain.links)
        it = iter(joint_angles)
        for i, active in enumerate(self.chain.active_links_mask):
            if active:
                full_angles[i] = next(it)
        matrix = self.chain.forward_kinematics(full_angles)
        return list(matrix[:3, 3])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("urdf_path")
    parser.add_argument("--target", type=float, nargs=3, required=True, metavar=("X", "Y", "Z"))
    parser.add_argument("--base-element", action="append", default=None,
                         help="Restrict ikpy to one link chain (repeatable) -- needed for a URDF with more than one end-effector branch")
    args = parser.parse_args()

    arm = IKArm(args.urdf_path, base_elements=args.base_element)
    print(f"joint chain ({len(arm.joint_names)} active joints): {arm.joint_names}")

    angles = arm.solve(target_position=args.target)
    print("solved joint angles (radians):")
    for name, angle in zip(arm.joint_names, angles):
        print(f"  {name}: {angle:.4f}")

    reached = arm.forward(angles)
    error = sum((a - b) ** 2 for a, b in zip(reached, args.target)) ** 0.5
    print(f"forward-kinematics check: solved angles reach {reached}, "
          f"target was {list(args.target)}, error = {error:.5f} m")
    if error > 0.01:
        print("WARNING: reached position is >1cm from target -- target may be unreachable, or base_elements needs setting for this URDF", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
