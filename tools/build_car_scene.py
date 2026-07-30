#!/usr/bin/env python3
"""
Generate scenes/car.tscn from the converted glTF + collision side car.

The collision body is a convex decomposition of the car's outer shell (nine
slabs along the length of the car), which gives accurate collisions against
kerbs, walls and the terrain while staying cheap enough for the solver. The
four RayWheel corners are placed exactly on the hub centres taken from the
original FBX.
"""

import json
import os
import sys

CORNERS = ["lf", "rf", "lr", "rr"]
CORNER_NAMES = {"lf": "Front left", "rf": "Front right",
                "lr": "Rear left", "rr": "Rear right"}

# --- vehicle setup ---------------------------------------------------------
MASS = 1495.0
FRONT_WEIGHT = 0.523           # BMW 1M is 52.3 / 47.7
SPRING_TRAVEL = 0.16
RIDE_HEIGHT_DROP = 0.075       # static sag, so the car sits on its springs
TYRE_RADIUS = {"lf": 0.323, "rf": 0.323, "lr": 0.330, "rr": 0.330}
TYRE_WIDTH = {"lf": 0.245, "rf": 0.245, "lr": 0.265, "rr": 0.265}

FRONT_RATE, REAR_RATE = 52800.0, 53500.0
FRONT_BUMP, FRONT_REB = 3090.0, 5270.0
REAR_BUMP, REAR_REB = 2970.0, 5070.0
FRONT_ARB, REAR_ARB = 14000.0, 9500.0
CAMBER = {"front": -1.4, "rear": -1.9}
TOE = {"front": 0.05, "rear": 0.12}


def fmt_vec(v):
    return "Vector3(%s, %s, %s)" % tuple(("%.4f" % c).rstrip("0").rstrip(".") or "0" for c in v)


def build(asset_dir, out_path):
    info = json.load(open(os.path.join(asset_dir, "bmw_1m_collision.json")))
    hulls = info["body_shapes"]
    wheels = info["wheel_positions"]

    res = []           # sub resources
    lines = []
    ext_id = "1_model"
    lines.append('[gd_scene load_steps=%d format=3]\n' % (len(hulls) + 5))
    lines.append('[ext_resource type="PackedScene" path="res://assets/car/bmw_1m.gltf" id="%s"]' % ext_id)
    lines.append('[ext_resource type="Script" path="res://scripts/vehicle.gd" id="2_vehicle"]')
    lines.append('[ext_resource type="Script" path="res://scripts/wheel.gd" id="3_wheel"]')
    lines.append('[ext_resource type="Script" path="res://scripts/car_model.gd" id="4_model"]\n')

    for i, pts in enumerate(hulls):
        flat = ", ".join("%.4f" % c for p in pts for c in p)
        lines.append('[sub_resource type="ConvexPolygonShape3D" id="Hull%d"]' % i)
        lines.append("points = PackedVector3Array(%s)\n" % flat)

    lines.append('[node name="Car" type="RigidBody3D"]')
    lines.append("mass = %.1f" % MASS)
    lines.append("physics_material_override = null")
    lines.append("continuous_cd = true")
    lines.append("contact_monitor = true")
    lines.append("max_contacts_reported = 4")
    lines.append("can_sleep = false")
    lines.append('script = ExtResource("2_vehicle")')
    lines.append("kerb_mass = %.1f" % MASS)
    lines.append("centre_of_mass = %s\n" % fmt_vec([0.0, 0.46, 0.06]))

    for i in range(len(hulls)):
        lines.append('[node name="Hull%d" type="CollisionShape3D" parent="."]' % i)
        lines.append('shape = SubResource("Hull%d")\n' % i)

    lines.append('[node name="Wheels" type="Node3D" parent="."]\n')

    for corner in CORNERS:
        pos = wheels[corner]
        front = corner.endswith("f")
        # The ray starts at the top of the damper travel.
        origin = [pos[0], pos[1] + SPRING_TRAVEL - RIDE_HEIGHT_DROP, pos[2]]
        lines.append('[node name="%s" type="RayCast3D" parent="Wheels"]' % corner.upper())
        lines.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, %.4f, %.4f, %.4f)" % tuple(origin))
        lines.append("target_position = %s" % fmt_vec([0, -(SPRING_TRAVEL + TYRE_RADIUS[corner]), 0]))
        lines.append("collision_mask = 1")
        lines.append('script = ExtResource("3_wheel")')
        lines.append("tyre_radius = %.3f" % TYRE_RADIUS[corner])
        lines.append("tyre_width = %.3f" % TYRE_WIDTH[corner])
        lines.append("wheel_mass = %.1f" % (20.0 if front else 22.0))
        lines.append("is_steering = %s" % ("true" if front else "false"))
        lines.append("is_driven = %s" % ("false" if front else "true"))
        lines.append("toe_deg = %.2f" % (TOE["front"] if front else TOE["rear"]))
        lines.append("camber_deg = %.2f" % (CAMBER["front"] if front else CAMBER["rear"]))
        lines.append("spring_length = %.3f" % SPRING_TRAVEL)
        lines.append("spring_rate = %.1f" % (FRONT_RATE if front else REAR_RATE))
        lines.append("bump_damping = %.1f" % (FRONT_BUMP if front else REAR_BUMP))
        lines.append("rebound_damping = %.1f" % (FRONT_REB if front else REAR_REB))
        lines.append("anti_roll_rate = %.1f" % (FRONT_ARB if front else REAR_ARB))
        corner_mass = MASS * (FRONT_WEIGHT if front else 1.0 - FRONT_WEIGHT) * 0.5
        lines.append("nominal_load = %.1f" % (corner_mass * 9.81))
        lines.append("friction_coefficient = %.2f" % (1.55 if front else 1.58))
        lines.append("peak_slip_angle_deg = %.1f" % (8.0 if front else 8.8))
        lines.append("")

    lines.append('[node name="Model" type="Node3D" parent="."]')
    lines.append('script = ExtResource("4_model")')
    lines.append('model_scene = ExtResource("%s")\n' % ext_id)

    lines.append('[node name="CameraTarget" type="Marker3D" parent="."]')
    lines.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.95, 0)\n")

    open(out_path, "w").write("\n".join(lines))
    print("wrote", out_path, "with", len(hulls), "collision hulls")


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
