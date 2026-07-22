"""Generate a Webots PROTO for an interactive object (a console, panel, or
similar prop with knobs/buttons/a screen) directly in native VRML, from a
structured JSON spec -- rather than sourcing/importing an external CAD/mesh
file. This is the deliberate choice this skill is built around, not a
shortcut: a downloaded mesh arrives with generic origins, un-separated
geometry, and no physics properties, and (per this project's own repeated,
empirically-confirmed experience converting a real URDF into
so-101-dual-arms/protos/so101/So101.proto) mesh-based collision geometry is
exactly what caused hours of real debugging -- spurious self-contacts,
Webots silently rejecting invalid boundingObject structures, and a root
node whose own physics made an entire assembly fall through the floor.
Building natively from primitives (Box/Cylinder/Sphere) sidesteps all of
that: collision geometry IS the primitive, there's no mesh to simplify
after the fact.

Spec format (see examples/ct_scanner_console.json for a full worked
example):

{
  "proto_name": "MyConsole",
  "default_name": "my_console",
  "chassis": {
    "parts": [
      {"name": "body", "shape": "box", "size": [x,y,z], "translation": [x,y,z],
       "rotation": [x,y,z,angle] (optional, default no rotation),
       "color": [r,g,b], "roughness": 0.5 (optional)}
    ]
  },
  "knobs": [
    {"name": "...", "anchor": [x,y,z], "axis": [0,1,0] (one cardinal axis),
     "min_stop": -3.14, "max_stop": 3.14, "radius": 0.02, "height": 0.03,
     "color": [r,g,b]}
  ],
  "buttons": [
    {"name": "...", "anchor": [x,y,z], "axis": [0,-1,0] (press direction),
     "max_travel": 0.01, "radius": 0.015, "height": 0.01, "color": [r,g,b]}
  ],
  "screen": {
    "name": "...", "translation": [x,y,z], "rotation": [x,y,z,angle],
    "width_m": 0.3, "height_m": 0.2, "pixel_width": 320, "pixel_height": 240,
    "with_touch_sensor": true
  }
}

Deliberate design choices baked into the generator, not left to chance:

- The chassis (the console's own body/base) gets a `boundingObject` (so
  it's collidable) but NEVER a `physics` field on the root -- confirmed the
  hard way in this same project (so-101-dual-arms) that a root node with
  its own physics is an unconstrained rigid body that falls through the
  floor the moment a simulation actually steps, not when the world is just
  opened and left paused. A fixed prop like a console should be immovable,
  exactly like every fixed-base Webots PROTO.
- Only joint-connected parts (knob shafts, button caps) get their own
  `physics` block, since THEY need mass/inertia for realistic joint
  dynamics -- the same distinction this project's own So101.proto follows
  correctly for every link except the one that caused the bug.
- A knob/button's local geometry is rotated to align a Cylinder's default
  Y-axis with whichever cardinal axis (X/Y/Z) the joint itself rotates or
  slides along, so the visible shape's long axis actually matches the
  motion direction instead of looking rotated 90 degrees from how it moves.

Usage:
    python generate_proto.py spec.json --out MyConsole.proto
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _vec(v: List[float]) -> str:
    return " ".join(f"{x:.6f}" for x in v)


def _rot(v: List[float]) -> str:
    return f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f} {v[3]:.6f}"


def _geometry_node(part: Dict[str, Any]) -> str:
    shape = part["shape"]
    if shape == "box":
        return f"Box {{ size {_vec(part['size'])} }}"
    if shape == "cylinder":
        return f"Cylinder {{ radius {part['radius']:.6f} height {part['height']:.6f} }}"
    if shape == "sphere":
        return f"Sphere {{ radius {part['radius']:.6f} }}"
    raise ValueError(f"unknown shape {shape!r} -- use box, cylinder, or sphere")


def _cylinder_alignment_rotation(axis: List[float]) -> str:
    """Rotate a Cylinder Shape so its own long axis lines up with whichever
    cardinal direction a joint moves along, so a rendered knob/button
    actually looks oriented the way it moves.

    CORRECTED THE HARD WAY: this function originally assumed a Webots
    Cylinder's default long axis is Y (the standard X3D convention) and
    rotated every Z-axis knob/button (the common case -- pressing/turning
    "into" the console's top surface) by 90 degrees to "align Y to Z". That
    was backwards. Empirically confirmed by rendering a single, completely
    unrotated Cylinder and viewing it from directly above: it appeared as a
    plain circle, meaning its default long axis is ALREADY vertical (Z) in
    this Webots version -- not Y. The old function was tipping correctly-
    oriented buttons/knobs onto their side by "fixing" an axis that was
    never wrong, which is exactly the "buttons look 90 degrees flipped"
    defect a direct visual comparison caught (the numeric joint behavior
    was always correct -- set_position moved the sensor value exactly as
    commanded -- because the joint itself doesn't care which way the
    VISUAL Shape is rotated; only the rendered look was wrong, and no
    structural or functional check could ever have caught that).

    Rotations below are re-derived (Rodrigues' rotation formula, the same
    method used to fix the boundingObject nesting bug) from the confirmed
    default axis = Z, and verified by rendering each case, not assumed
    correct from the math alone a second time.
    """
    ax, ay, az = (abs(v) for v in axis)
    if az >= ax and az >= ay:
        return "0 0 1 0"  # target is Z, already the Cylinder's default -- identity
    if ax >= ay and ax >= az:
        return "0 1 0 1.570796"  # align default Z -> X
    return "1 0 0 -1.570796"  # align default Z -> Y


def _chassis_solid(chassis: Dict[str, Any]) -> str:
    parts = chassis["parts"]
    shapes = []
    bounds = []
    for part in parts:
        rotation = _rot(part.get("rotation", [0, 0, 1, 0]))
        color = part.get("color", [0.7, 0.7, 0.7])
        roughness = part.get("roughness", 0.5)
        geometry = _geometry_node(part)
        shapes.append(
            f"""          Pose {{
            translation {_vec(part['translation'])}
            rotation {rotation}
            children [
              Shape {{
                appearance PBRAppearance {{ baseColor {_vec(color)} roughness {roughness} }}
                geometry {geometry}
              }}
            ]
          }}"""
        )
        bounds.append(
            f"""          Pose {{
            translation {_vec(part['translation'])}
            rotation {rotation}
            children [ {geometry} ]
          }}"""
        )
    shapes_str = "\n".join(shapes)
    bounds_str = "\n".join(bounds)
    return f"""    Solid {{
      name "chassis"
      children [
{shapes_str}
      ]
      boundingObject Group {{
        children [
{bounds_str}
        ]
      }}
    }}"""
    # Deliberately NO `physics` field here -- see module docstring.


def _hinge_stops(knob: Dict[str, Any]) -> str:
    """Webots only honors HingeJoint minStop/maxStop within (-pi, pi) --
    confirmed directly (a real generated PROTO with +-6.28 stops loaded with
    a warning that they'd be ignored). A knob spec with no min_stop/max_stop
    at all gets a genuinely unconstrained, continuously-rotating joint (the
    correct native way to model a real continuous-turn value knob, not an
    approximation), rather than silently clamping an out-of-range request to
    something the caller didn't ask for."""
    if "min_stop" not in knob and "max_stop" not in knob:
        return ""
    min_stop = knob.get("min_stop", -3.14159)
    max_stop = knob.get("max_stop", 3.14159)
    if not (-3.14159 < min_stop < 3.14159) or not (-3.14159 < max_stop < 3.14159):
        raise ValueError(
            f"knob {knob['name']!r}: min_stop/max_stop must both be strictly within "
            f"(-pi, pi) for Webots to honor them -- got {min_stop}, {max_stop}. "
            f"Omit both fields entirely for a freely, continuously rotating knob instead."
        )
    return f"        minStop {min_stop}\n        maxStop {max_stop}\n"


def _knob_node(knob: Dict[str, Any]) -> str:
    axis = knob["axis"]
    anchor = knob["anchor"]
    shape_rotation = _cylinder_alignment_rotation(axis)
    color = knob.get("color", [0.2, 0.2, 0.2])
    radius = knob["radius"]
    height = knob["height"]
    mass = knob.get("mass", 0.05)
    stops = _hinge_stops(knob)
    # A plain, uniformly-colored cylinder gives a human viewer no way to
    # SEE that it has actually rotated -- confirmed directly: from a static
    # screenshot, a turned knob and an untouched one are pixel-identical,
    # since a cylinder is rotationally symmetric about its own axis. Real
    # physical knobs solve this with a printed line or a raised pointer;
    # this generator does the same by placing a small, contrasting-colored
    # indicator bar on the knob's own visible face (the local +Z face,
    # before `shape_rotation` re-orients it -- Cylinder's default axis is
    # confirmed Z, see `_cylinder_alignment_rotation`'s own docstring),
    # offset from center toward the rim so it visibly sweeps as the knob
    # turns. It lives inside the SAME rotated Pose as the knob's own Shape,
    # so it's carried along by the HingeJoint's real rotation automatically
    # -- not a separate node that would need its own motor wiring.
    indicator_color = knob.get("indicator_color", [0.05, 0.05, 0.05])
    indicator_width = radius * 0.16
    indicator_length = radius * 0.8
    indicator_thickness = 0.0015
    return f"""    HingeJoint {{
      jointParameters HingeJointParameters {{
        axis {_vec(axis)}
        anchor {_vec(anchor)}
{stops}      }}
      device [
        RotationalMotor {{ name "{knob['name']}_motor" }}
        PositionSensor {{ name "{knob['name']}_sensor" }}
      ]
      endPoint Solid {{
        translation {_vec(anchor)}
        name "{knob['name']}"
        children [
          Pose {{
            rotation {shape_rotation}
            children [
              Shape {{
                appearance PBRAppearance {{ baseColor {_vec(color)} roughness 0.3 metalness 0.6 }}
                geometry Cylinder {{ radius {radius:.6f} height {height:.6f} }}
              }}
              Pose {{
                translation 0.000000 {indicator_length / 2:.6f} {height / 2 + indicator_thickness / 2:.6f}
                children [
                  Shape {{
                    appearance PBRAppearance {{ baseColor {_vec(indicator_color)} roughness 0.4 }}
                    geometry Box {{ size {indicator_width:.6f} {indicator_length:.6f} {indicator_thickness:.6f} }}
                  }}
                ]
              }}
            ]
          }}
        ]
        boundingObject Pose {{
          rotation {shape_rotation}
          children [ Cylinder {{ radius {radius:.6f} height {height:.6f} }} ]
        }}
        physics Physics {{ density -1 mass {mass} }}
      }}
    }}"""


def _button_node(button: Dict[str, Any]) -> str:
    # Note: Webots' SliderJoint uses a plain `JointParameters` node, which
    # has NO `anchor` field at all (unlike HingeJointParameters) -- a real
    # error caught by loading a first generated PROTO in real Webots
    # ("Skipped unknown 'anchor' field in JointParameters node"), not
    # something obvious from the VRML reference alone. A slider's start
    # position comes entirely from its endPoint's own `translation`; only
    # `axis` (direction of travel) and the stops belong in JointParameters.
    axis = button["axis"]
    anchor = button["anchor"]
    shape_rotation = _cylinder_alignment_rotation(axis)
    color = button.get("color", [0.8, 0.1, 0.1])
    radius = button["radius"]
    height = button["height"]
    mass = button.get("mass", 0.02)
    # A button's slide axis has no reason to point anywhere but toward
    # gravity in most console layouts (pressed downward or inward) -- with
    # no spring, a lightweight endPoint just falls under gravity along its
    # own axis, past its own maxStop, with nothing commanding it. Confirmed
    # directly: a first generated button with no spring read 0.28 on its
    # position sensor -- 35x past an 0.008 maxStop -- for a joint no one had
    # even commanded away from rest yet. A real physical button has a
    # return spring holding it at the unpressed position; springConstant/
    # dampingConstant here is that spring, not an arbitrary physics tweak.
    # A button endPoint's own weight, uncommanded, otherwise drifts under
    # gravity along whatever direction its own slide axis points -- tested
    # directly across several tuning attempts, from none (drifted to 0.28m,
    # 35x past an 0.008m maxStop) through a soft spring (drifted to 0.065m)
    # and an explicit motor maxForce with no spring (unstable oscillation,
    # -0.29 to 0.37m). 800/4 is what a prior pass settled on and *believed*
    # held still at rest (a single ~0.009m reading looked like settled rest).
    #
    # That belief was wrong, found only once a live console was watched
    # continuously instead of sampled once: an UNTOUCHED button (never
    # commanded all session) oscillated forever between roughly +0.009m and
    # -0.0086m, a real, sustained limit cycle, not noise -- because
    # `minStop` was hardcoded to exactly 0.0, the same value as the button's
    # own unpressed rest target. A joint whose resting/uncommanded state
    # sits EXACTLY on its own hard mechanical limit is a classic
    # constraint-chatter condition for any physics engine (like a ball
    # resting exactly on a contact plane) -- confirmed directly: raising
    # damping 5x only shrank the oscillation's amplitude, never stopped it,
    # while giving `minStop` a small negative margin so 0.0 sits safely
    # *inside* the joint's own range (not on its boundary) eliminated it
    # completely on the very first test, still using this same original
    # 800/4 spring/damping unchanged -- proving the boundary condition, not
    # the spring tuning, was the actual root cause. See
    # reference/knowledge/webots-simulation-pitfalls.md item 14.
    spring = button.get("spring_constant", 800.0)
    damping = button.get("damping_constant", 4.0)
    stop_margin = button.get("stop_margin", min(0.001, button["max_travel"] * 0.25))
    return f"""    SliderJoint {{
      jointParameters JointParameters {{
        axis {_vec(axis)}
        minStop {-stop_margin:.6f}
        maxStop {button['max_travel']:.6f}
        springConstant {spring}
        dampingConstant {damping}
      }}
      device [
        LinearMotor {{ name "{button['name']}_motor" }}
        PositionSensor {{ name "{button['name']}_sensor" }}
      ]
      endPoint Solid {{
        translation {_vec(anchor)}
        name "{button['name']}"
        children [
          Pose {{
            rotation {shape_rotation}
            children [
              Shape {{
                appearance PBRAppearance {{ baseColor {_vec(color)} roughness 0.4 }}
                geometry Cylinder {{ radius {radius:.6f} height {height:.6f} }}
              }}
            ]
          }}
        ]
        boundingObject Pose {{
          rotation {shape_rotation}
          children [ Cylinder {{ radius {radius:.6f} height {height:.6f} }} ]
        }}
        physics Physics {{ density -1 mass {mass} }}
      }}
    }}"""


def _screen_node(screen: Dict[str, Any]) -> str:
    # A Webots Display device is for LIVE, programmatically-drawn content
    # -- it renders solid black until something draws to it at runtime,
    # which looks wrong in a static scene or a screenshot-based comparison
    # (confirmed directly: a first console's screen was a flat dark box
    # next to nothing readable, and looked nothing like its reference
    # photo's real touchscreen UI). If a `texture` path is given (a PNG
    # from generate_faceplate_texture.py), a second, very thin, textured
    # Box is placed just in front of the dark bezel -- so the screen shows
    # a real, readable idle UI by default, while the Display device is
    # still present alongside it (slightly behind, same footprint) for
    # genuine live-drawn content later if the object gets wired up to one.
    width_m = screen["width_m"]
    height_m = screen["height_m"]
    pixel_w = screen["pixel_width"]
    pixel_h = screen["pixel_height"]
    devices = [f'Display {{ name "{screen["name"]}" width {pixel_w} height {pixel_h} }}']
    if screen.get("with_touch_sensor"):
        # A "bumper"-type TouchSensor only reports whether contact is
        # happening, not tap coordinates -- a real simplification, not
        # hidden: getting exact (x, y) tap position typically needs a
        # Supervisor-level contact-point query, out of scope for a single
        # device node. Documented here and in the skill's SKILL.md.
        devices.append(f'TouchSensor {{ name "{screen["name"]}_touch" type "bumper" }}')
    devices_str = "\n          ".join(devices)

    texture_shape = ""
    if screen.get("texture"):
        tex_w = width_m * 0.94
        tex_h = height_m * 0.94
        texture_shape = f"""
        Pose {{
          translation 0.006 0 0
          children [
            Shape {{
              appearance PBRAppearance {{
                baseColorMap ImageTexture {{ url ["{screen['texture']}"] }}
                roughness 0.35
                metalness 0
              }}
              geometry Box {{ size 0.002 {tex_w:.6f} {tex_h:.6f} }}
            }}
          ]
        }}"""

    return f"""    Solid {{
      translation {_vec(screen['translation'])}
      rotation {_rot(screen['rotation'])}
      name "{screen['name']}_bezel"
      children [
        Shape {{
          appearance PBRAppearance {{ baseColor 0.08 0.08 0.09 roughness 0.4 }}
          geometry Box {{ size 0.01 {width_m:.6f} {height_m:.6f} }}
        }}{texture_shape}
        {devices_str}
      ]
      boundingObject Box {{ size 0.01 {width_m:.6f} {height_m:.6f} }}
    }}"""


def generate(spec: Dict[str, Any]) -> str:
    proto_name = spec["proto_name"]
    default_name = spec.get("default_name", proto_name.lower())

    children_nodes = [_chassis_solid(spec["chassis"])]
    for knob in spec.get("knobs", []):
        children_nodes.append(_knob_node(knob))
    for button in spec.get("buttons", []):
        children_nodes.append(_button_node(button))
    if "screen" in spec:
        children_nodes.append(_screen_node(spec["screen"]))

    children_str = "\n".join(children_nodes)

    return f"""#VRML_SIM R2025a utf8
# {proto_name} -- generated by webots-object-design/scripts/generate_proto.py
# from a structured spec, natively in VRML primitives (no imported mesh).

PROTO {proto_name} [
  field SFVec3f    translation 0 0 0
  field SFRotation rotation    0 0 1 0
  field SFString   name        "{default_name}"
  field SFString   controller  "<extern>"
  field SFBool     supervisor  FALSE
]
{{
  Robot {{
    translation IS translation
    rotation IS rotation
    name IS name
    controller IS controller
    supervisor IS supervisor
    children [
{children_str}
    ]
  }}
}}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec_path")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec_path).read_text(encoding="utf-8"))
    proto_text = generate(spec)
    Path(args.out).write_text(proto_text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
