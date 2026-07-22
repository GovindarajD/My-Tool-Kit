"""Static analysis of a Webots .proto file for the exact defect classes that
took real, hours-long debugging sessions to find by hand while building the
SO-101 dual-arm workstation (SynapticRobots/so-101-dual-arms). Every check
here corresponds to a bug that actually shipped and was actually fixed in
that project -- this is not a hypothetical lint list.

Usage:
    python check_proto.py path/to/Something.proto

Exits non-zero if any FAIL-level finding is present, so it's safe to wire
into a pre-launch step. WARN-level findings are reported but don't fail.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Finding:
    level: str  # "FAIL" or "WARN"
    line: int
    message: str


def find_matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def check_leading_digit_defs(text: str) -> list[Finding]:
    """VRML/X3D DEF identifiers must match [a-zA-Z_][a-zA-Z0-9_-]* -- a URDF
    material/link name starting with a digit (e.g. "3d_printed") silently
    breaks the whole PROTO with no obvious error pointing at the cause."""
    findings = []
    for m in re.finditer(r"\bDEF\s+([0-9][\w-]*)\b", text):
        findings.append(Finding(
            "FAIL", line_of(text, m.start()),
            f"DEF identifier '{m.group(1)}' starts with a digit -- invalid VRML "
            f"identifier, will silently break PROTO loading. Rename it (e.g. prefix "
            f"with a letter).",
        ))
    return findings


def check_root_robot_physics(text: str) -> list[Finding]:
    """A fixed-base manipulator's root Robot node must NOT have its own
    `physics` field -- if it does, the whole robot is an unconstrained rigid
    body and will fall under gravity the moment the sim actually steps
    (which may not be until an extern controller connects, so this can look
    fine when the world is just opened and left paused)."""
    findings = []
    robot_match = re.search(r"\bRobot\s*\{", text)
    if not robot_match:
        return findings
    open_idx = text.index("{", robot_match.start())
    close_idx = find_matching_brace(text, open_idx)
    if close_idx == -1:
        return findings
    robot_body = text[open_idx:close_idx]

    # Find the top-level `physics` field of the Robot node itself (depth 1
    # relative to the Robot's own opening brace), not one belonging to a
    # nested link's endPoint Solid several levels deeper.
    depth = 0
    i = 0
    while i < len(robot_body):
        ch = robot_body[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 1 and robot_body[i:i + 7] == "physics" and re.match(r"physics\s*Physics\s*\{", robot_body[i:i + 40]):
            findings.append(Finding(
                "FAIL", line_of(text, open_idx + i),
                "Root Robot node has its own `physics Physics {...}` block. A "
                "fixed-base robot's root should have NO physics (immovable, like "
                "every other Webots fixed-base manipulator PROTO) -- otherwise it "
                "is an unconstrained rigid body that falls once the sim steps. "
                "Remove this block (keep boundingObject if present).",
            ))
        i += 1
    return findings


def check_mesh_based_bounding_objects(text: str) -> list[Finding]:
    """boundingObject should reference simplified primitives (Box/Cylinder/
    Sphere/Capsule), not a full concave visual Mesh -- urdf2webots's
    --box-collision flag does NOT reliably guarantee this (confirmed known
    limitation), so it must be checked in the OUTPUT, not assumed from the
    flag having been passed. Reusing the full mesh as its own collision
    geometry produces spurious self-contact points (an assembly's own parts
    legitimately overlap at mounting joints) and can visibly corrupt
    rendering under heavy contact-resolution load.

    This is exactly the bug that shipped in the SO-101 build: every
    boundingObject was `USE <name>` pointing at a DEF'd Mesh elsewhere in
    the file, not an inline `Mesh {...}` -- so the check has to resolve a
    bare USE reference back to its DEF'd node type, not just pattern-match
    the word "Mesh" appearing textually near the boundingObject itself."""
    mesh_def_names = set(re.findall(r"geometry\s+DEF\s+(\w+)\s+Mesh\b", text))
    findings = []
    for m in re.finditer(r"\bboundingObject\s+", text):
        start = m.end()
        brace_idx = text.find("{", start, start + 200)
        use_match = re.match(r"USE\s+(\w+)", text[start:start + 200])

        if use_match and (brace_idx == -1 or use_match.end() < brace_idx - start):
            name = use_match.group(1)
            if name in mesh_def_names:
                findings.append(Finding(
                    "WARN", line_of(text, start),
                    f"boundingObject USE's '{name}', which is DEF'd as a Mesh "
                    f"(full-resolution geometry), not a simplified primitive. This "
                    f"is the exact known urdf2webots collision-complexity trap -- "
                    f"replace with a Box/Cylinder/Sphere sized to the mesh's own "
                    f"AABB, nested in the same Pose so the transform stays correct.",
                ))
            continue

        if brace_idx == -1:
            continue
        close_idx = find_matching_brace(text, brace_idx)
        span = text[start:close_idx if close_idx != -1 else start + 800]
        if re.search(r"\bMesh\s*\{", span):
            findings.append(Finding(
                "WARN", line_of(text, start),
                "boundingObject contains an inline Mesh geometry directly rather "
                "than a simplified primitive (Box/Cylinder/Sphere/Capsule) -- same "
                "collision-complexity trap as a USE'd mesh reference, just written "
                "inline instead of via DEF/USE.",
            ))
    return findings


def check_use_reused_for_visual_and_collision(text: str) -> list[Finding]:
    """A DEF'd mesh reused via USE for BOTH a visual Shape.geometry AND a
    boundingObject is a real trap for any later script that does a blind
    "replace every USE <name>" edit: it will silently break the visual
    instances too. Flag any DEF'd geometry name that appears in both
    contexts so a future edit knows to check brace-context, not just node
    name, before rewriting it."""
    findings = []
    def_names = set(re.findall(r"geometry\s+DEF\s+(\w+)\s+Mesh", text))
    for name in def_names:
        visual_uses = len(re.findall(rf"Shape\s*\{{[^{{}}]*?geometry\s+USE\s+{re.escape(name)}\b", text, re.DOTALL))
        collision_uses = 0
        for m in re.finditer(r"\bboundingObject\s+", text):
            span_end = min(len(text), m.end() + 400)
            if re.search(rf"USE\s+{re.escape(name)}\b", text[m.end():span_end]):
                collision_uses += 1
        if visual_uses > 0 and collision_uses > 0:
            findings.append(Finding(
                "WARN", 0,
                f"Mesh '{name}' is reused via USE for BOTH a visual Shape ({visual_uses}x) "
                f"and a boundingObject ({collision_uses}x). A script rewriting "
                f"'USE {name}' by node name alone will break the visual instances too -- "
                f"any edit here must parse brace-context (inside boundingObject vs "
                f"inside Shape) rather than doing a blind text replace.",
            ))
    return findings


def check_nested_transform_wrappers_in_bounding_object(text: str) -> list[Finding]:
    """boundingObject rejects a transform-type node (Pose OR Transform)
    nested inside another transform-type node -- confirmed via live Webots
    console capture ("Cannot insert Pose node in 'children' field of Pose
    node in bounding object"), and confirmed that renaming the inner
    wrapper's keyword alone does NOT fix it (Transform-in-Transform is
    equally rejected, same error text). The only real fix is a single flat
    wrapper node, with its translation computed by actually composing the
    two transforms (rotate the inner offset by the outer's axis-angle
    rotation, then add) rather than relying on node nesting -- exactly the
    way the *visual* Shape tree elsewhere in the same file safely does, but
    boundingObject does not accept the same pattern. This one is subtle
    enough that it shipped, rendered correctly, and passed a full test
    suite before being caught -- rendering and joint-governance tests don't
    exercise collision geometry at all, so a silently-dropped boundingObject
    looks identical to a working one until something actually collides."""
    findings = []
    for m in re.finditer(r"\bboundingObject\s+", text):
        start = m.end()
        brace_idx = text.find("{", start, start + 50)
        if brace_idx == -1:
            continue
        close_idx = find_matching_brace(text, brace_idx)
        if close_idx == -1:
            continue
        span = text[start:close_idx + 1]  # +1: include the outer node's own closing brace, or its own brace-matching below runs off the end and silently fails to match
        # A transform-type keyword (Pose/Transform) whose own children list
        # contains ANOTHER transform-type keyword, both within this one
        # boundingObject span, is the exact rejected pattern.
        for wrapper_match in re.finditer(r"\b(Pose|Transform)\s*\{", span):
            w_open = span.find("{", wrapper_match.start())
            w_close = find_matching_brace(span, w_open)
            if w_close == -1:
                continue
            inner = span[w_open:w_close]
            if re.search(r"\b(Pose|Transform)\s*\{", inner):
                findings.append(Finding(
                    "FAIL", line_of(text, start + wrapper_match.start()),
                    "boundingObject contains a transform-type node (Pose/Transform) "
                    "nested inside another transform-type node -- Webots rejects this "
                    "combination regardless of which of the two keywords is used. "
                    "Flatten into a single wrapper node, composing the two "
                    "translations/rotations yourself (rotate the inner offset by the "
                    "outer's axis-angle rotation, then add) rather than nesting.",
                ))
                break  # one finding per boundingObject span is enough signal
    return findings


CHECKS = [
    check_leading_digit_defs,
    check_root_robot_physics,
    check_mesh_based_bounding_objects,
    check_use_reused_for_visual_and_collision,
    check_nested_transform_wrappers_in_bounding_object,
]


def main(proto_path: str) -> int:
    path = Path(proto_path)
    text = path.read_text(encoding="utf-8")

    all_findings: list[Finding] = []
    for check in CHECKS:
        all_findings.extend(check(text))

    if not all_findings:
        print(f"OK -- no known-defect patterns found in {path.name}")
        return 0

    fails = [f for f in all_findings if f.level == "FAIL"]
    warns = [f for f in all_findings if f.level == "WARN"]

    for f in sorted(all_findings, key=lambda f: (f.level != "FAIL", f.line)):
        loc = f"line {f.line}" if f.line else "(file-wide)"
        print(f"[{f.level}] {loc}: {f.message}")

    print(f"\n{len(fails)} FAIL, {len(warns)} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python check_proto.py path/to/Something.proto", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
