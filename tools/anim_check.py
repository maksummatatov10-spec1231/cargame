#!/usr/bin/env python3
"""
Verification of the two wheel animations.

Animation 1 — rolling: the wheel spin comes out of the tyre model, so the only
way the visual can be right is if the contact patch is not slipping. This test
drives the car up to speed, lifts off, and checks that spin * radius matches the
real road speed. If the wheels were driven by a "speed * constant" fudge this
test would still pass, but the physics would not; here it is the other way
round, the animation is a read-out of the simulation.

Animation 2 — steering: checks the steering angles that are handed to the wheel
nodes, including Ackermann geometry (inner wheel turns more than the outer one),
that the rear wheels never steer, and that the lock shrinks with speed.

Also validates the visual pivot geometry taken from the converted glTF.

Run:  python3 tools/anim_check.py
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_check as S  # noqa: E402

ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "car")
FAILURES = []


def check(label, ok, detail=""):
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -> " + detail) if detail else ""))
    if not ok:
        FAILURES.append(label)


def test_rolling():
    print("\n== animation 1: wheels roll at the true ground speed ==")
    car = S.Car(S.REST_HEIGHT)
    for _ in range(360):
        car.step()
    car.throttle = 0.55
    for _ in range(1200):
        car.step()
    car.throttle = 0.0
    for _ in range(240):
        car.step()

    v = car.forward_speed()
    worst = 0.0
    for w in car.wheels:
        surface = w.spin * w.radius
        err = abs(surface - v) / abs(v)
        worst = max(worst, err)
        print("  %s: %7.2f rad/s -> %6.2f m/s  (car %6.2f m/s)" % (w.name, w.spin, surface, v))
    check("wheels are rolling, not skating", worst < 0.03, "worst error %.2f%%" % (worst * 100.0))
    rev = v / (2.0 * math.pi * 0.33)
    print("  %.0f km/h = %.2f wheel revolutions per second" % (v * 3.6, rev))
    check("revolutions per second are physically right", 10.0 < rev < 20.0, "%.2f rev/s" % rev)

    # braking must slow the wheels down too
    car.brake = 1.0
    for _ in range(60):
        car.step()
    check("braking slows the wheels", all(abs(w.spin) < 90.0 for w in car.wheels),
          "max spin %.1f rad/s" % max(abs(w.spin) for w in car.wheels))

    # A wheel in the air keeps turning; it only bleeds off slowly through
    # bearing drag, so a jumping car still shows spinning wheels.
    wheel = car.wheels[0]
    wheel.grounded = False
    wheel.brake_torque = 0.0
    wheel.drive_torque = 0.0
    wheel.force = (0.0, 0.0)
    wheel.spin = 90.0
    for _ in range(120):           # one second of flight
        wheel.update_spin()
    ratio = wheel.spin / 90.0
    print("  after 1 s airborne the wheel retains %.1f%% of its spin" % (ratio * 100.0))
    check("airborne wheels coast instead of stopping dead", 0.2 < ratio < 0.999,
          "%.1f%% retained" % (ratio * 100.0))


def test_steering():
    print("\n== animation 2: steering geometry ==")
    car = S.Car(S.REST_HEIGHT)
    for _ in range(120):
        car.step()

    ok_ackermann = True
    ok_rear = True
    for inp in (1.0, 0.5):
        car.steer_input = inp
        car.step()
        fl = next(w for w in car.wheels if w.name == "lf")
        fr = next(w for w in car.wheels if w.name == "rf")
        rear = [w for w in car.wheels if not w.is_steering]
        inner = abs(math.degrees(fl.steer))
        outer = abs(math.degrees(fr.steer))
        print("  input %.1f: inner %.2f deg, outer %.2f deg" % (inp, inner, outer))
        ok_ackermann = ok_ackermann and inner > outer
        ok_rear = ok_rear and all(abs(math.degrees(w.steer)) < 0.5 for w in rear)

    check("inner wheel turns more than the outer (Ackermann)", ok_ackermann)
    check("rear wheels never steer", ok_rear)

    car.steer_input = 1.0
    car.step()
    lock = abs(math.degrees(next(w for w in car.wheels if w.name == "lf").steer))
    check("full lock is a realistic 30-40 deg", 30.0 < lock < 40.0, "%.1f deg" % lock)

    print("\n== speed sensitive steering ==")
    car = S.Car(S.REST_HEIGHT)
    for _ in range(360):
        car.step()
    car.throttle = 1.0
    angles = []
    for target in (0, 40, 80, 140):
        while car.speed_kmh() < target:
            car.step()
        car.steer_input = 1.0
        car.step()
        a = abs(math.degrees(next(w for w in car.wheels if w.name == "lf").steer))
        angles.append(a)
        print("  %3.0f km/h -> %.2f deg" % (car.speed_kmh(), a))
        car.steer_input = 0.0
    check("steering lock shrinks with speed",
          all(angles[i] >= angles[i + 1] - 0.01 for i in range(len(angles) - 1)))


def test_pivots():
    print("\n== visual pivots from the converted model ==")
    gltf = json.load(open(os.path.join(ASSET, "bmw_1m.gltf")))
    nodes = {n["name"]: n for n in gltf["nodes"] if "name" in n}

    for corner in ("lf", "rf", "lr", "rr"):
        wheel = nodes["wheel_" + corner]
        hub = nodes["hub_" + corner]
        wp = wheel["translation"]
        hp = hub["translation"]
        same = all(abs(wp[i] - hp[i]) < 1e-6 for i in range(3))
        check("hub_%s shares the wheel pivot" % corner, same)

    lf = nodes["wheel_lf"]["translation"]
    rf = nodes["wheel_rf"]["translation"]
    lr = nodes["wheel_lr"]["translation"]
    track = abs(lf[0] - rf[0])
    wheelbase = abs(lf[2] - lr[2])
    print("  track %.3f m, wheelbase %.3f m, hub height %.3f m" % (track, wheelbase, lf[1]))
    check("track matches the real car (~1.49 m)", 1.40 < track < 1.58, "%.3f m" % track)
    check("wheelbase matches the real car (~2.63 m)", 2.55 < wheelbase < 2.71,
          "%.3f m" % wheelbase)
    check("hubs sit at the tyre radius", 0.30 < lf[1] < 0.36, "%.3f m" % lf[1])
    check("front wheels are mirrored about the centreline", abs(lf[0] + rf[0]) < 1e-3)

    # the steering wheel must carry a rotation so it spins around its own axis
    steer = nodes["steering"]
    q = steer.get("rotation")
    check("steering wheel has its column rake baked in", q is not None and abs(q[1]) > 0.1,
          "quat %s" % ([round(v, 3) for v in q] if q else None))


def main():
    print("Wheel animation verification")
    test_rolling()
    test_steering()
    test_pivots()
    print("\n%s" % ("ALL CHECKS PASSED" if not FAILURES else "FAILURES: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
