#!/usr/bin/env python3
"""
Verification of the chase camera.

This exists because of a real bug: the first version wrote to the camera's own
position every frame, which fights the SpringArm3D that owns it. The camera
collapsed onto the pivot, ended up inside the bodywork, and the screen filled
with the inside of the car (reported as a "white flickering screen"). The
look_at() call then failed too, because the camera position and the point it
was told to look at were the same, so the direction vector was zero:

    "Up vector and direction between node origin and target are aligned"
    "The target vector and up vector can't be parallel to each other"

The camera rig is pure geometry, so it can be reproduced exactly here. This
replays the rig against the car's real motion and asserts that the camera never
gets close enough to the car to clip inside it, and that the view direction is
never degenerate.

Run:  python3 tools/camera_check.py
"""

import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_check as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []

# must match scripts/chase_camera.gd
BASE_DISTANCE = 6.2
SPEED_DISTANCE = 2.1
HEIGHT = 1.55
SPEED_REFERENCE = 55.0
ROTATION_SMOOTHING = 5.0
POSITION_SMOOTHING = 14.0
BASE_PITCH = -9.0
SPEED_PITCH = -12.0
DT = 1.0 / 120.0


def check(label, ok, detail=""):
    # The detail is the *reason*, so only show it when there is something to
    # explain. Printing "[PASS] the dead setting is gone -> it is still there"
    # is worse than useless: several checks were phrased as failure messages
    # and read as though they had failed while passing.
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  -> " + detail) if (detail and not ok) else ""))
    if not ok:
        FAILURES.append(label)


def lerp_angle(a, b, w):
    return a + ((b - a + math.pi) % math.tau - math.pi) * max(0.0, min(1.0, w))


class Rig:
    """Mirrors the transform chain ChaseCamera -> SpringArm3D -> Camera3D."""

    def __init__(self, car):
        self.pos = list(car.body.pos)
        self.yaw = 0.0

    def update(self, car):
        vel = car.body.vel
        speed = S.v_len(vel)
        t = max(0.0, min(1.0, speed / SPEED_REFERENCE))

        target_yaw = math.atan2(-car.body.basis[0][2], -car.body.basis[2][2])
        if speed > 4.0:
            flat = math.hypot(vel[0], vel[2])
            if flat > 0.5:
                travel_yaw = math.atan2(-vel[0], -vel[2])
                target_yaw = lerp_angle(target_yaw, travel_yaw, 0.45)
        self.yaw = lerp_angle(self.yaw, target_yaw,
                              min(1.0, ROTATION_SMOOTHING * DT * (0.5 + t)))

        focus = car.body.pos
        w = min(1.0, POSITION_SMOOTHING * DT)
        for i in range(3):
            self.pos[i] += (focus[i] - self.pos[i]) * w

        # pivot -> arm origin
        pivot = (self.pos[0], self.pos[1] + HEIGHT, self.pos[2])
        pitch = math.radians(BASE_PITCH + (SPEED_PITCH - BASE_PITCH) * t)
        length = BASE_DISTANCE + SPEED_DISTANCE * t

        # the arm pushes the camera along its local -Z, after pitch then yaw
        local = (0.0, -math.sin(pitch) * -length, -length * math.cos(pitch))
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        offset = (local[0] * cy + local[2] * sy, local[1],
                  -local[0] * sy + local[2] * cy)
        cam = (pivot[0] + offset[0], pivot[1] + offset[1], pivot[2] + offset[2])
        return cam, pivot, length


def test_rig():
    print("\n== chase camera geometry over a full drive ==")
    car = S.Car(S.REST_HEIGHT)
    for _ in range(240):
        car.step()
    rig = Rig(car)

    min_dist = 1e9
    min_height = 1e9
    degenerate = 0
    samples = 0

    # accelerate, corner hard, brake - the whole envelope
    script = ([(1.0, 0.0, 0.0)] * 900
              + [(0.7, 0.0, 0.8)] * 900
              + [(0.0, 1.0, -0.6)] * 600
              + [(0.4, 0.0, 0.0)] * 300)
    for throttle, brake, steer in script:
        car.throttle, car.brake, car.steer_input = throttle, brake, steer
        car.step()
        cam, pivot, _length = rig.update(car)
        samples += 1

        d = S.v_len(S.v_sub(cam, car.body.pos))
        min_dist = min(min_dist, d)
        min_height = min(min_height, cam[1])

        look = S.v_sub((pivot[0], pivot[1] + 0.55, pivot[2]), cam)
        if S.v_len(look) < 0.05:
            degenerate += 1
        else:
            up = (0.0, 1.0, 0.0)
            if S.v_len(S.v_cross(up, look)) < 1e-4:
                degenerate += 1

    print("  %d frames, closest approach %.2f m, lowest camera %.2f m"
          % (samples, min_dist, min_height))
    check("camera never gets inside the car", min_dist > 2.5, "%.2f m" % min_dist)
    check("camera never goes underground", min_height > 0.3, "%.2f m" % min_height)
    check("view direction is never degenerate (no look_at failure)",
          degenerate == 0, "%d bad frames" % degenerate)


def test_scene_wiring():
    print("\n== camera nodes in main.tscn ==")
    text = open(os.path.join(ROOT, "scenes", "main.tscn")).read()

    block = re.search(r'\[node name="Camera3D"[^\]]*\](.*?)(?=\n\[node|\Z)', text, re.S)
    check("Camera3D exists", block is not None)
    if block:
        body = block.group(1)
        m = re.search(r"transform = Transform3D\(([^)]+)\)", body)
        check("Camera3D has an identity transform (the arm places it)",
              m is not None and all(
                  abs(float(v) - e) < 1e-6 for v, e in
                  zip(m.group(1).split(","), [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])),
              m.group(1) if m else "missing")
        check("Camera3D is the current camera", "current = true" in body)

    arm = re.search(r'\[node name="SpringArm3D"[^\]]*\](.*?)(?=\n\[node|\Z)', text, re.S)
    check("SpringArm3D exists", arm is not None)
    if arm:
        check("spring arm has a collision mask", "collision_mask" in arm.group(1))

    src = open(os.path.join(ROOT, "scripts", "chase_camera.gd")).read()
    check("script excludes the car from the spring arm",
          "add_excluded_object" in src)
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    check("script no longer calls look_at (the arm already aims it)",
          "look_at" not in code)
    check("script never writes camera.position directly",
          not re.search(r"camera\.position\s*=", src))
    check("rig is top_level so it is not moved twice", "top_level = true" in src)


def test_wheel_pivots_still_valid():
    print("\n== model is still intact after the normal repair ==")
    gltf = json.load(open(os.path.join(ROOT, "assets", "car", "bmw_1m.gltf")))
    names = {n.get("name") for n in gltf["nodes"]}
    required = {"body", "steering"} | {p + c for p in ("wheel_", "hub_")
                                       for c in ("lf", "rf", "lr", "rr")}
    check("all animated parts survived", required <= names,
          ", ".join(sorted(required - names)))


def test_heading_not_euler():
    """The camera must take the car's heading from its forward axis.

    v2.7 replaced `_target.global_rotation.y` with
    `_curr_target.basis.get_euler().y`. Those agree only while the car is
    level: Euler decomposition in YXZ order folds pitch and roll into the yaw
    term, so over a crest or leaning in a corner the "yaw" the camera chases
    stops being the direction the car faces. That is what threw the chase
    view off.
    """
    print("\n== camera heading must survive pitch and roll ==")

    def basis_from(yaw, pitch, roll):
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cr, sr = math.cos(roll), math.sin(roll)
        ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
        rx = [[1, 0, 0], [0, cp, -sp], [0, sp, cp]]
        rz = [[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]]

        def mul(a, b):
            return [[sum(a[i][k] * b[k][j] for k in range(3))
                     for j in range(3)] for i in range(3)]
        return mul(mul(ry, rx), rz)

    def euler_y(m):
        """Basis::get_euler(EULER_ORDER_YXZ), the buggy source."""
        sx = m[2][1]
        if -1.0 + 1e-7 < sx < 1.0 - 1e-7:
            return math.atan2(-m[2][0], m[2][2])
        return 0.0

    def heading(m):
        """What chase_camera.gd::_heading_of does: the -Z axis, flattened."""
        fwd = (-m[0][2], -m[1][2], -m[2][2])
        return math.atan2(-fwd[0], -fwd[2])

    worst_euler = 0.0
    worst_fixed = 0.0
    print("  pitch  roll   euler error   forward-axis error")
    for pitch_d, roll_d in ((0, 0), (10, 10), (20, 15), (30, 20), (45, 30),
                            (60, 40)):
        p, r = math.radians(pitch_d), math.radians(roll_d)
        for yaw_d in (0.0, 37.0, -128.0):
            y = math.radians(yaw_d)
            m = basis_from(y, p, r)
            e = abs((math.degrees(euler_y(m)) - yaw_d + 180.0) % 360.0 - 180.0)
            h = abs((math.degrees(heading(m)) - yaw_d + 180.0) % 360.0 - 180.0)
            worst_euler = max(worst_euler, e)
            worst_fixed = max(worst_fixed, h)
        print("  %5.0f  %5.0f   %8.2f deg   %10.3f deg"
              % (pitch_d, roll_d, worst_euler, worst_fixed))

    check("the Euler version really was broken", worst_euler > 5.0,
          "only %.2f deg of error, nothing to fix" % worst_euler)
    check("the forward-axis heading is exact", worst_fixed < 0.01,
          "%.3f deg of error" % worst_fixed)

    src = open(os.path.join(ROOT, "scripts", "chase_camera.gd")).read()
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    check("the camera no longer reads yaw from Euler angles",
          "get_euler()" not in body, "get_euler() is back")
    check("it uses the forward axis instead", "_heading_of" in body)


def test_frame_rate_independent_smoothing():
    """Camera smoothing must feel the same at any frame rate.

    Moving the camera from _physics_process (fixed 1/120 s) to _process
    (frame time) meant `rate * delta` no longer had a constant delta, so its
    response started varying with fps. 1-exp(-rate*dt) does not.
    """
    print("\n== camera smoothing must not depend on frame rate ==")
    rate = POSITION_SMOOTHING

    def settle(weight_fn, fps):
        """Fraction of the gap closed after 100 ms."""
        dt = 1.0 / fps
        remaining = 1.0
        for _ in range(int(round(0.1 * fps))):
            remaining *= 1.0 - min(weight_fn(dt), 1.0)
        return 1.0 - remaining

    linear = [settle(lambda dt: rate * dt, f) for f in (30, 60, 75, 120, 240)]
    expo = [settle(lambda dt: 1.0 - math.exp(-rate * dt), f)
            for f in (30, 60, 75, 120, 240)]
    print("  fps          30     60     75    120    240")
    print("  rate*dt   " + "".join("%6.1f%%" % (v * 100) for v in linear))
    print("  1-exp     " + "".join("%6.1f%%" % (v * 100) for v in expo))

    spread_linear = max(linear) - min(linear)
    spread_expo = max(expo) - min(expo)
    check("the old form really did vary with fps", spread_linear > 0.03,
          "only %.1f%% spread" % (spread_linear * 100))
    check("the shipped form is fps independent", spread_expo < 0.03,
          "%.1f%% spread" % (spread_expo * 100))

    src = open(os.path.join(ROOT, "scripts", "chase_camera.gd")).read()
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    check("the camera uses exponential smoothing", "_smoothing(" in body)
    check("no raw rate*delta weights are left",
          not re.search(r"clampf\(\s*(?:position|rotation)_smoothing\s*\*", body),
          "a linear weight is still there")


def main():
    print("Chase camera verification")
    test_rig()
    test_scene_wiring()
    test_wheel_pivots_still_valid()
    test_heading_not_euler()
    test_frame_rate_independent_smoothing()
    print("\n%s" % ("ALL CHECKS PASSED" if not FAILURES else "FAILURES: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
