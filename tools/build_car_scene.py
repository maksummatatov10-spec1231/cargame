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


# Per-vehicle setup. The BMW is the stage 1 sports car; the pickup is heavier,
# taller, softer and slower, which is most of what makes it feel different.
PRESETS = {
    "bmw": {
        "meta": "bmw_1m_collision.json",
        "model": "res://assets/car/bmw_1m.gltf",
        "mass": 1495.0,
        "front_weight": 0.523,
        "travel": 0.16,
        "ride_drop": 0.075,
        "com": [0.0, 0.46, 0.06],
        "radius": {"lf": 0.323, "rf": 0.323, "lr": 0.330, "rr": 0.330},
        "width": {"lf": 0.245, "rf": 0.245, "lr": 0.265, "rr": 0.265},
        "front_rate": 52800.0, "rear_rate": 53500.0,
        "front_bump": 3090.0, "front_reb": 5270.0,
        "rear_bump": 2970.0, "rear_reb": 5070.0,
        "front_arb": 14000.0, "rear_arb": 9500.0,
        "camber": {"front": -1.4, "rear": -1.9},
        "toe": {"front": 0.05, "rear": 0.12},
        "mu": {"front": 1.55, "rear": 1.58},
        "peak_sa": {"front": 8.0, "rear": 8.8},
        "wheel_mass": {"front": 20.0, "rear": 22.0},
        "body_size": [1.80, 1.42, 4.38],
    },
    "pickup": {
        "meta": "pickup_meta.json",
        "model": None,          # the pickup ships as one glTF per part
        "parts": "res://assets/pickup/",
        "mass": 2450.0,         # full-size pickup, kerb
        "front_weight": 0.56,   # engine over the front axle, empty bed
        "travel": 0.24,         # long travel suspension
        # Static sag, computed from the corner masses and spring rates:
        # front 686 kg / 49000 N/m = 0.137 m, rear 539 kg / 44000 = 0.120 m.
        # Using anything less makes the body settle below the wheel mounts.
        "ride_drop": 0.129,
        # The centre of mass has to agree with front_weight_bias or the springs
        # carry a different split than the rest of the model assumes. For 56%
        # on the front axle it sits 0.44 of the wheelbase behind it:
        #   z = -1.451 + 0.44 * 3.130 = -0.074
        "com": [0.0, 0.72, -0.074],
        "radius": {"lf": 0.470, "rf": 0.470, "lr": 0.470, "rr": 0.470},
        "width": {"lf": 0.285, "rf": 0.285, "lr": 0.285, "rr": 0.285},
        # Softer springs for the longer travel, sized to ~1.35 Hz front.
        "front_rate": 49000.0, "rear_rate": 44000.0,
        "front_bump": 4200.0, "front_reb": 7100.0,
        "rear_bump": 3800.0, "rear_reb": 6400.0,
        "front_arb": 9000.0, "rear_arb": 5000.0,
        "camber": {"front": -0.5, "rear": -0.5},
        "toe": {"front": 0.10, "rear": 0.05},
        # Chunky off-road tyres: less peak grip on tarmac, more slip angle.
        "mu": {"front": 1.28, "rear": 1.30},
        "peak_sa": {"front": 10.5, "rear": 11.0},
        "wheel_mass": {"front": 34.0, "rear": 36.0},
        "body_size": [2.05, 2.00, 5.50],
    },
    "defender": {
        "meta": "defender_meta.json",
        "model": None,
        "parts": "res://assets/defender/",
        # Real Defender 110 station wagon, kerb, with the expedition kit this
        # model is wearing (roof rack, tent, spare, snorkel, winch).
        "mass": 2550.0,
        "front_weight": 0.51,   # near enough 50/50, engine forward but long tail
        "travel": 0.26,         # live axles, a lot of articulation
        # Static sag from the corner masses and rates: front 650 kg / 44000 =
        # 0.145 m, rear 625 kg / 41000 = 0.149 m.
        "ride_drop": 0.147,
        # Sits high: the centre of mass is the reason a Defender leans. The z
        # must agree with front_weight_bias or the springs carry a different
        # split than the rest of the model assumes:
        #   z = -1.395 + 0.49 * 2.790 = -0.028
        "com": [0.0, 0.82, -0.028],
        "radius": {"lf": 0.433, "rf": 0.433, "lr": 0.433, "rr": 0.433},
        "width": {"lf": 0.255, "rf": 0.255, "lr": 0.255, "rr": 0.255},
        # ~1.25 Hz: softer than the pickup, which is what long-travel 4x4s run.
        "front_rate": 44000.0, "rear_rate": 41000.0,
        "front_bump": 4000.0, "front_reb": 6800.0,
        "rear_bump": 3700.0, "rear_reb": 6200.0,
        # Deliberately soft bars: articulation matters more than roll control.
        "front_arb": 6500.0, "rear_arb": 4000.0,
        "camber": {"front": 0.0, "rear": 0.0},
        "toe": {"front": 0.08, "rear": 0.0},
        # All-terrain tyres: modest tarmac grip, generous slip angle.
        "mu": {"front": 1.22, "rear": 1.24},
        "peak_sa": {"front": 11.5, "rear": 12.0},
        "wheel_mass": {"front": 32.0, "rear": 34.0},
        "body_size": [1.97, 1.97, 4.70],
    },
}


def build(asset_dir, out_path, preset_name="bmw"):
    preset = PRESETS[preset_name]
    info = json.load(open(os.path.join(asset_dir, preset["meta"])))
    hulls = info["body_shapes"]
    wheels = info["wheel_positions"]

    mass = preset["mass"]
    travel = preset["travel"]
    drop = preset["ride_drop"]
    front_weight = preset["front_weight"]
    is_pickup = preset["model"] is None

    lines = []
    ext_count = 5 if is_pickup else 5
    lines.append('[gd_scene load_steps=%d format=3]\n' % (len(hulls) + ext_count + 7))

    if is_pickup:
        # The pickup is exported as one glTF per animated part, so the model
        # node loads them itself rather than instancing a single scene.
        lines.append('[ext_resource type="Script" path="res://scripts/pickup_model.gd" id="1_model"]')
    else:
        lines.append('[ext_resource type="PackedScene" path="%s" id="1_model"]' % preset["model"])
    lines.append('[ext_resource type="Script" path="res://scripts/vehicle.gd" id="2_vehicle"]')
    lines.append('[ext_resource type="Script" path="res://scripts/wheel.gd" id="3_wheel"]')
    if not is_pickup:
        lines.append('[ext_resource type="Script" path="res://scripts/car_model.gd" id="4_model"]')
    lines.append('[ext_resource type="Script" path="res://scripts/exhaust_smoke.gd" id="5_smoke"]')
    lines.append('[ext_resource type="Script" path="res://scripts/tyre_marks.gd" id="6_marks"]')
    lines.append('[ext_resource type="Script" path="res://scripts/ground_particles.gd" id="7_dirt"]')
    lines.append('[ext_resource type="Script" path="res://scripts/smoothing.gd" id="8_smooth"]\n')

    for i, pts in enumerate(hulls):
        flat = ", ".join("%.4f" % c for p in pts for c in p)
        lines.append('[sub_resource type="ConvexPolygonShape3D" id="Hull%d"]' % i)
        lines.append("points = PackedVector3Array(%s)\n" % flat)

    lines.append('[node name="Car" type="RigidBody3D"]')
    lines.append("mass = %.1f" % mass)
    lines.append("continuous_cd = true")
    lines.append("contact_monitor = true")
    lines.append("max_contacts_reported = 4")
    lines.append("can_sleep = false")
    lines.append('script = ExtResource("2_vehicle")')
    lines.append("kerb_mass = %.1f" % mass)
    lines.append("centre_of_mass = %s" % fmt_vec(preset["com"]))
    lines.append("front_weight_bias = %.3f" % front_weight)
    lines.append("body_extents = %s" % fmt_vec(preset["body_size"]))
    if preset_name == "defender":
        # A 2.0 turbo diesel: strong low-down torque, low redline, short gears.
        lines.append("peak_torque = 550.0")
        lines.append("peak_torque_rpm = 1600.0")
        lines.append("peak_power_rpm = 3500.0")
        lines.append("redline_rpm = 4400.0")
        lines.append("idle_rpm = 650.0")
        lines.append("engine_inertia = 0.48")
        lines.append("final_drive = 3.54")
        lines.append("gear_ratios = Array[float]([4.71, 3.14, 2.11, 1.67, 1.29, 1.00])")
        lines.append("front_brake_torque = 3300.0")
        lines.append("rear_brake_torque = 2700.0")
        lines.append("handbrake_torque = 3400.0")
        lines.append("max_steer_deg = 38.0")
        # A brick with a roof rack.
        lines.append("drag_area = 1.62")
        lines.append("front_downforce = 0.0")
        lines.append("rear_downforce = 0.0")
        lines.append("all_wheel_drive = true")
        lines.append("front_torque_split = 0.5")
        lines.append("differential_lock = 0.85")
        # Tall and softly sprung, so it needs the assists more than the others.
        lines.append("stability_control = 0.75")
        lines.append("traction_control = 0.9")
    elif is_pickup:
        # A tall, heavy 4x4 is geared shorter and revs lower than the coupe.
        lines.append("peak_torque = 620.0")
        lines.append("peak_torque_rpm = 2100.0")
        lines.append("peak_power_rpm = 4200.0")
        lines.append("redline_rpm = 5200.0")
        lines.append("idle_rpm = 700.0")
        lines.append("engine_inertia = 0.42")
        lines.append("final_drive = 3.73")
        lines.append("gear_ratios = Array[float]([4.70, 3.13, 2.10, 1.67, 1.29, 1.00])")
        lines.append("front_brake_torque = 3400.0")
        lines.append("rear_brake_torque = 2600.0")
        lines.append("handbrake_torque = 3200.0")
        lines.append("max_steer_deg = 36.0")
        lines.append("drag_area = 1.35")
        lines.append("front_downforce = 0.05")
        lines.append("rear_downforce = 0.05")
        # Four wheel drive: the reason to have a pickup at all.
        lines.append("all_wheel_drive = true")
        lines.append("front_torque_split = 0.4")
        lines.append("differential_lock = 0.75")
    lines.append("")

    for i in range(len(hulls)):
        lines.append('[node name="Hull%d" type="CollisionShape3D" parent="."]' % i)
        lines.append('shape = SubResource("Hull%d")\n' % i)

    lines.append('[node name="Wheels" type="Node3D" parent="."]\n')

    for corner in CORNERS:
        pos = wheels[corner]
        front = corner.endswith("f")
        radius = preset["radius"][corner]
        origin = [pos[0], pos[1] + travel - drop, pos[2]]
        lines.append('[node name="%s" type="RayCast3D" parent="Wheels"]' % corner.upper())
        lines.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, %.4f, %.4f, %.4f)"
                     % tuple(origin))
        lines.append("target_position = %s" % fmt_vec([0, -(travel + radius), 0]))
        lines.append("collision_mask = 1")
        lines.append('script = ExtResource("3_wheel")')
        lines.append("tyre_radius = %.3f" % radius)
        lines.append("tyre_width = %.3f" % preset["width"][corner])
        lines.append("wheel_mass = %.1f"
                     % preset["wheel_mass"]["front" if front else "rear"])
        lines.append("is_steering = %s" % ("true" if front else "false"))
        lines.append("is_driven = %s"
                     % ("true" if (is_pickup or not front) else "false"))
        lines.append("toe_deg = %.2f" % preset["toe"]["front" if front else "rear"])
        lines.append("camber_deg = %.2f" % preset["camber"]["front" if front else "rear"])
        lines.append("spring_length = %.3f" % travel)
        lines.append("spring_rate = %.1f"
                     % (preset["front_rate"] if front else preset["rear_rate"]))
        lines.append("bump_damping = %.1f"
                     % (preset["front_bump"] if front else preset["rear_bump"]))
        lines.append("rebound_damping = %.1f"
                     % (preset["front_reb"] if front else preset["rear_reb"]))
        lines.append("anti_roll_rate = %.1f"
                     % (preset["front_arb"] if front else preset["rear_arb"]))
        corner_mass = mass * (front_weight if front else 1.0 - front_weight) * 0.5
        lines.append("nominal_load = %.1f" % (corner_mass * 9.81))
        lines.append("friction_coefficient = %.2f"
                     % preset["mu"]["front" if front else "rear"])
        lines.append("peak_slip_angle_deg = %.1f"
                     % preset["peak_sa"]["front" if front else "rear"])
        lines.append("")

    # The visual model hangs off a smoothing node rather than the body itself.
    # Physics is 120 Hz and the display is not, so without interpolation the
    # car steps between ticks; see scripts/smoothing.gd.
    lines.append('[node name="Smooth" type="Node3D" parent="."]')
    lines.append('script = ExtResource("8_smooth")')
    lines.append('teleport_threshold = 4.0\n')

    lines.append('[node name="Model" type="Node3D" parent="Smooth"]')
    if is_pickup:
        lines.append('script = ExtResource("1_model")')
        lines.append('asset_dir = "%s"\n' % preset["parts"])
    else:
        lines.append('script = ExtResource("4_model")')
        lines.append('model_scene = ExtResource("1_model")\n')

    lines.append('[node name="ExhaustSmoke" type="Node3D" parent="."]')
    lines.append('script = ExtResource("5_smoke")\n')

    lines.append('[node name="TyreMarks" type="Node3D" parent="."]')
    lines.append('script = ExtResource("6_marks")\n')

    lines.append('[node name="GroundParticles" type="Node3D" parent="."]')
    lines.append('script = ExtResource("7_dirt")\n')

    lines.append('[node name="CameraTarget" type="Marker3D" parent="."]')
    cam_h = 1.35 if is_pickup else 0.95
    lines.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, %.2f, 0)\n" % cam_h)

    open(out_path, "w").write("\n".join(lines))
    print("wrote %s (%s, %d hulls)" % (out_path, preset_name, len(hulls)))


if __name__ == "__main__":
    preset = sys.argv[3] if len(sys.argv) > 3 else "bmw"
    build(sys.argv[1], sys.argv[2], preset)
