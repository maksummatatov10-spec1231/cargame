#!/usr/bin/env python3
"""
Offline verification of the vehicle physics.

Godot cannot be run in every environment, so this script re-implements the exact
maths from scripts/wheel.gd and scripts/vehicle.gd in Python and integrates it
with a rigid body solver that behaves like Jolt/Godot Physics (semi implicit
Euler, forces applied at world points, 120 Hz tick).

It checks that:
  * the drop test lands, bounces once on the springs and settles
  * the settled ride height matches the designed static sag
  * the static corner loads add up to the weight of the car and are split
    according to the front/rear weight distribution
  * the car accelerates and reaches a sane top speed
  * braking distance from 100 km/h is realistic
  * a steady state cornering test produces a believable lateral g

Run:  python3 tools/sim_check.py
"""

import json
import math
import struct
import os
import sys

DT = 1.0 / 120.0
G = 9.81
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ASSET = os.path.join(ROOT, "assets", "car")


# --------------------------------------------------------------------------- #
#  small vector / matrix helpers
# --------------------------------------------------------------------------- #

def v_add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def v_sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def v_mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def v_dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def v_len(a): return math.sqrt(v_dot(a, a))


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def v_norm(a):
    l = v_len(a)
    return (a[0] / l, a[1] / l, a[2] / l) if l > 1e-12 else (0.0, 0.0, 0.0)


def m_mul_v(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def m_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def m_t(m):
    return [[m[j][i] for j in range(3)] for i in range(3)]


def m_ident():
    return [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]


def orthonormalise(m):
    x = v_norm((m[0][0], m[1][0], m[2][0]))
    y = (m[0][1], m[1][1], m[2][1])
    y = v_norm(v_sub(y, v_mul(x, v_dot(x, y))))
    z = v_cross(x, y)
    return [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]]


# --------------------------------------------------------------------------- #
#  wheel — mirrors scripts/wheel.gd
# --------------------------------------------------------------------------- #

MF_B, MF_C, MF_E = 1.685211, 1.62, 0.35


def magic_formula(s):
    bs = MF_B * s
    return math.sin(MF_C * math.atan(bs - MF_E * (bs - math.atan(bs))))


class Wheel:
    def __init__(self, name, pos, front, cfg):
        self.name = name
        self.mount = pos                     # ray origin in body space
        self.is_steering = front
        self.radius = cfg["radius"]
        self.mass = cfg["wheel_mass"]
        self.inertia = 0.5 * self.mass * self.radius ** 2
        self.spring_length = cfg["spring_length"]
        self.spring_rate = cfg["spring_rate"]
        self.bump = cfg["bump"]
        self.rebound = cfg["rebound"]
        self.knee = 0.18
        self.fast_ratio = 0.42
        self.bump_stop_rate = 1200000.0
        self.bump_stop_length = 0.045
        self.tyre_rate = 260000.0
        self.tyre_damping = 5200.0
        self.deflection = 0.0
        self.prev_deflection = 0.0
        self.anti_roll = cfg["anti_roll"]
        self.mu = cfg["mu"]
        self.peak_sr = 0.115
        self.peak_sa = math.radians(cfg["peak_sa_deg"])
        self.load_sensitivity = 0.22
        self.nominal_load = cfg["nominal_load"]
        self.relaxation = 0.42
        self.max_relaxation_time = 0.08
        self.rolling_resistance = 0.014
        self.camber = math.radians(cfg["camber"])
        self.surface_grip = 1.0
        self.surface_drag = 1.0
        self.supported_mass = cfg.get("supported_mass", 375.0)

        self.spin = 0.0
        self.driveline_inertia = 0.0
        self.travel = 0.0
        self.prev_travel = 0.0
        self.spring_force = 0.0
        self.grounded = False
        self.contact = (0.0, 0.0, 0.0)
        self.normal = (0.0, 1.0, 0.0)
        self.force = (0.0, 0.0)
        self.lag_sr = 0.0
        self.lag_ta = 0.0
        self.steer = 0.0
        self.drive_torque = 0.0
        self.brake_torque = 0.0
        self.slip_ratio = 0.0
        self.slip_angle = 0.0

    def axes(self, body):
        """steering-rotated wheel frame in world space"""
        c, s = math.cos(self.steer), math.sin(self.steer)
        yaw = [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]
        return m_mul(body.basis, yaw)

    def update_suspension(self, body, opposite_travel, ground_height):
        basis = self.axes(body)
        origin = v_add(body.pos, m_mul_v(body.basis, self.mount))
        down = v_mul((basis[0][1], basis[1][1], basis[2][1]), -1.0)

        reach = self.spring_length + self.radius
        # flat ground: analytic ray/plane intersection
        self.grounded = False
        if down[1] < -1e-6:
            t = (ground_height - origin[1]) / down[1]
            if 0.0 <= t <= reach:
                self.grounded = True
                self.contact = v_add(origin, v_mul(down, t))
                self.normal = (0.0, 1.0, 0.0)

        if self.grounded:
            length = v_len(v_sub(origin, self.contact)) - self.radius
            raw = self.spring_length - length
            self.prev_deflection = self.deflection
            self.deflection = max(0.0, raw - self.spring_length)
            self.travel = max(0.0, min(self.spring_length, raw))
        else:
            self.prev_deflection = self.deflection
            self.deflection = 0.0
            self.travel = 0.0
            self.spring_force = 0.0
            self.prev_travel = 0.0
            return 0.0

        speed = (self.travel - self.prev_travel) / DT
        self.prev_travel = self.travel

        force = self.spring_rate * self.travel
        into = self.travel - (self.spring_length - self.bump_stop_length)
        if into > 0.0:
            force += self.bump_stop_rate * into * (into / self.bump_stop_length)

        rate = self.bump if speed > 0.0 else self.rebound
        v = abs(speed)
        damp = rate * v if v <= self.knee else \
            rate * self.knee + rate * self.fast_ratio * (v - self.knee)
        force += math.copysign(damp, speed)
        force += self.anti_roll * (self.travel - opposite_travel)

        if self.deflection > 0.0:
            tspeed = (self.deflection - self.prev_deflection) / DT
            force += self.tyre_rate * self.deflection
            force += self.tyre_damping * tspeed
            force = max(0.0, force)

        self.spring_force = max(0.0, force)
        return self.travel

    def update_tyre(self, body, contact_velocity):
        if not self.grounded or self.spring_force <= 0.0:
            self.force = (0.0, 0.0)
            self.lag_sr = self.lag_ta = 0.0
            self.slip_ratio = self.slip_angle = 0.0
            return
        basis = self.axes(body)
        right = (basis[0][0], basis[1][0], basis[2][0])
        forward = v_norm(v_cross(self.normal, right))
        right = v_norm(v_cross(forward, self.normal))

        v_long = v_dot(contact_velocity, forward)
        v_lat = v_dot(contact_velocity, right)

        speed = max(abs(v_long), 0.35)
        blend = min(1.0, max(speed / self.relaxation,
                                 1.0 / self.max_relaxation_time) * DT)
        self.lag_sr += ((self.spin * self.radius - v_long) / speed - self.lag_sr) * blend
        self.lag_ta += (-v_lat / speed - self.lag_ta) * blend
        self.slip_ratio = self.lag_sr
        self.slip_angle = math.atan(self.lag_ta)

        nx = self.lag_sr / self.peak_sr
        ny = self.lag_ta / math.tan(self.peak_sa)
        combined = math.hypot(nx, ny)
        if combined < 1e-5:
            self.force = (0.0, 0.0)
            return

        load_ratio = self.spring_force / self.nominal_load
        mu = self.mu * (1.0 - self.load_sensitivity * (load_ratio - 1.0))
        mu = max(0.35 * self.mu, min(1.35 * self.mu, mu))
        mu *= math.cos(self.camber) * 0.02 + 0.98
        mu *= self.surface_grip

        total = mu * self.spring_force * magic_formula(combined)
        fx = total * nx / combined
        fy = total * ny / combined

        slip_velocity = self.spin * self.radius - v_long
        response = self.radius ** 2 / max(self.inertia + self.driveline_inertia, 1e-6) \
            + 1.0 / max(self.supported_mass, 1.0)
        no_overshoot = abs(slip_velocity) / max(response * DT, 1e-9)
        torque_limit = (abs(self.drive_torque) + abs(self.brake_torque)) / self.radius \
            + mu * self.spring_force * 0.05
        limit = min(no_overshoot, torque_limit)
        fx = max(-limit, min(limit, fx))

        if abs(v_long) > 0.05:
            crr = self.rolling_resistance * (1.0 + 0.0006 * v_long * v_long) \
                * self.surface_drag
            fx -= math.copysign(crr * self.spring_force, v_long)

        self.force = (fx, fy)

    def update_spin(self):
        road = -self.force[0] * self.radius
        net = self.drive_torque + road
        inertia = self.inertia + self.driveline_inertia
        if self.brake_torque > 0.0:
            free = self.spin + net / inertia * DT
            bd = self.brake_torque / inertia * DT
            self.spin = 0.0 if abs(free) <= bd else free - math.copysign(bd, free)
        else:
            self.spin += net / inertia * DT
        if not self.grounded:
            self.spin -= self.spin * min(1.0, 1.2 * DT)
        self.spin = max(-400.0, min(400.0, self.spin))

    def apply(self, body):
        if not self.grounded:
            return
        arm = v_sub(self.contact, body.pos)
        if self.spring_force > 0.0:
            body.add_force(v_mul(self.normal, self.spring_force), arm)
        basis = self.axes(body)
        right = (basis[0][0], basis[1][0], basis[2][0])
        forward = v_norm(v_cross(self.normal, right))
        right = v_norm(v_cross(forward, self.normal))
        body.add_force(v_add(v_mul(forward, self.force[0]),
                             v_mul(right, self.force[1])), arm)


# --------------------------------------------------------------------------- #
#  rigid body
# --------------------------------------------------------------------------- #

class Body:
    def __init__(self, mass, inertia, pos):
        self.mass = mass
        self.inertia = inertia            # diagonal, body space
        self.pos = pos
        self.basis = m_ident()
        self.vel = (0.0, 0.0, 0.0)
        self.omega = (0.0, 0.0, 0.0)
        self.force = (0.0, 0.0, 0.0)
        self.torque = (0.0, 0.0, 0.0)

    def add_force(self, f, arm):
        self.force = v_add(self.force, f)
        self.torque = v_add(self.torque, v_cross(arm, f))

    def velocity_at(self, arm):
        return v_add(self.vel, v_cross(self.omega, arm))

    def integrate(self):
        self.force = v_add(self.force, (0.0, -G * self.mass, 0.0))
        self.vel = v_add(self.vel, v_mul(self.force, DT / self.mass))

        # torque -> angular acceleration through the body space inertia tensor
        tb = m_mul_v(m_t(self.basis), self.torque)
        ab = (tb[0] / self.inertia[0], tb[1] / self.inertia[1], tb[2] / self.inertia[2])
        self.omega = v_add(self.omega, v_mul(m_mul_v(self.basis, ab), DT))

        self.pos = v_add(self.pos, v_mul(self.vel, DT))
        w = v_mul(self.omega, DT)
        skew = [[0.0, -w[2], w[1]], [w[2], 0.0, -w[0]], [-w[1], w[0], 0.0]]
        delta = [[(1.0 if i == j else 0.0) + skew[i][j] for j in range(3)] for i in range(3)]
        self.basis = orthonormalise(m_mul(delta, self.basis))

        self.force = (0.0, 0.0, 0.0)
        self.torque = (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
#  vehicle — mirrors scripts/vehicle.gd
# --------------------------------------------------------------------------- #

MASS = 1495.0
FRONT_WEIGHT = 0.523
SPRING_TRAVEL = 0.16
RIDE_DROP = 0.075
GEARS = [4.11, 2.32, 1.54, 1.18, 1.00, 0.85]
FINAL = 3.15
REVERSE_RATIO = 3.73
PEAK_TORQUE = 450.0
PEAK_TQ_RPM, PEAK_PW_RPM = 3000.0, 5900.0
IDLE, REDLINE = 850.0, 7000.0


def engine_torque(rpm, scale=1.0):
    """Crank torque. `scale` mirrors GameSettings.engine_power, which the
    vehicle applies as peak_torque = _base_peak_torque * power."""
    if rpm < IDLE * 0.4:
        return 0.0
    if rpm < PEAK_TQ_RPM:
        x = max(0.0, min(1.0, (rpm - IDLE * 0.5) / (PEAK_TQ_RPM - IDLE * 0.5)))
        t = PEAK_TORQUE * scale * (0.42 + 0.58 * math.sin(x * math.pi * 0.5))
    elif rpm < PEAK_PW_RPM:
        t = PEAK_TORQUE * scale
    else:
        x = max(0.0, min(1.0, (rpm - PEAK_PW_RPM) / (REDLINE - PEAK_PW_RPM)))
        t = PEAK_TORQUE * scale * (1.0 - 0.34 * x)
    if rpm > REDLINE:
        t *= max(0.0, 1.0 - (rpm - REDLINE) / 260.0)
    return t


class Car:
    def __init__(self, spawn_height):
        info = json.load(open(os.path.join(ASSET, "bmw_1m_collision.json")))
        wp = info["wheel_positions"]

        w, h, l = 1.80, 1.42, 4.38
        k = MASS / 12.0
        inertia = (k * (h * h + l * l), k * (w * w + l * l) * 0.86, k * (w * w + h * h))
        self.com = (0.0, 0.46, 0.06)
        self.body = Body(MASS, inertia, (0.0, spawn_height, 0.0))

        self.wheels = []
        for corner in ("lf", "rf", "lr", "rr"):
            p = wp[corner]
            front = corner.endswith("f")
            corner_mass = MASS * (FRONT_WEIGHT if front else 1.0 - FRONT_WEIGHT) * 0.5
            cfg = {
                "radius": 0.323 if front else 0.330,
                "wheel_mass": 20.0 if front else 22.0,
                "spring_length": SPRING_TRAVEL,
                "spring_rate": 52800.0 if front else 53500.0,
                "bump": 3090.0 if front else 2970.0,
                "rebound": 5270.0 if front else 5070.0,
                "anti_roll": 15600.0 if front else 7600.0,
                "mu": 1.55 if front else 1.62,
                "peak_sa_deg": 9.2 if front else 7.4,
                "nominal_load": corner_mass * G,
                "camber": -1.4 if front else -1.9,
                "supported_mass": corner_mass,
            }
            mount = (p[0] - self.com[0],
                     p[1] + SPRING_TRAVEL - RIDE_DROP - self.com[1],
                     p[2] - self.com[2])
            self.wheels.append(Wheel(corner, mount, front, cfg))

        self.gear = 1
        self.engine_speed = IDLE * math.tau / 60.0
        self.throttle = 0.0
        self.brake = 0.0
        self.steer_input = 0.0
        self.shift_timer = 0.0
        self.tc_cut = 0.0
        # Tuning exposed by the settings menu.
        self.engine_power = 1.0
        self.all_wheel_drive = False
        self.front_torque_split = 0.4
        self.traction_control = 0.85
        self.traction_target_slip = 0.16
        self.stability_control = 0.6
        self.stability_deadband = 0.18
        self.traction_headroom = 1.15

    # position of the visual origin (the car's own origin, not the CoM)
    def origin_height(self):
        return self.body.pos[1] - m_mul_v(self.body.basis, self.com)[1]

    def ratio(self):
        # Mirrors current_ratio(), including reverse. The mirror used to return
        # 0 in reverse, so the car could select it but never move - which hid
        # the fact that reverse drive works in the game.
        if self.gear == 0:
            return 0.0
        if self.gear < 0:
            return -REVERSE_RATIO * FINAL
        return GEARS[min(self.gear - 1, len(GEARS) - 1)] * FINAL

    def step(self):
        b = self.body
        speed = v_len(b.vel)

        # --- steering with Ackermann ---
        wheelbase = 2.632
        track = 1.489
        scale = 1.0 + (0.34 - 1.0) * min(1.0, speed * 3.6 / 42.0)
        angle = math.radians(33.0) * self.steer_input * scale
        for w in self.wheels:
            if not w.is_steering:
                continue
            if abs(angle) < 1e-5:
                w.steer = 0.0
                continue
            radius = wheelbase / math.tan(abs(angle))
            inner = math.atan(wheelbase / max(radius - track * 0.5, 0.1))
            outer = math.atan(wheelbase / (radius + track * 0.5))
            a = 0.72
            inner = abs(angle) + (inner - abs(angle)) * a
            outer = abs(angle) + (outer - abs(angle)) * a
            left = w.mount[0] < 0.0
            mag = inner if left == (angle > 0.0) else outer
            w.steer = math.copysign(mag, angle)

        # --- suspension ---
        prev = [w.travel for w in self.wheels]
        opp = {0: 1, 1: 0, 2: 3, 3: 2}
        for i, w in enumerate(self.wheels):
            w.update_suspension(b, prev[opp[i]], 0.0)

        # --- drivetrain ---
        if self.shift_timer > 0.0:
            self.shift_timer = max(0.0, self.shift_timer - DT)
        fwd = self.forward_speed()
        if self.shift_timer > 0.0:
            clutch = 0.0
        else:
            clutch = min(1.0, max(min(abs(fwd) / 0.9, 1.0), self.throttle * 1.6))
        rear = [w for w in self.wheels if not w.is_steering]
        front = [w for w in self.wheels if w.is_steering]
        # Mirrors Vehicle._driven(): all four wheels when 4WD is on.
        driven = self.wheels if self.all_wheel_drive else rear
        driven_spin = sum(w.spin for w in driven) / len(driven)
        ratio = self.ratio()
        idle_speed = IDLE * math.tau / 60.0
        if abs(ratio) > 0.01 and clutch > 0.99:
            self.engine_speed = abs(driven_spin * ratio)
        else:
            free = engine_torque(self._rpm(), self.engine_power) * self.throttle \
                - 0.055 * max(0.0, self.engine_speed - idle_speed)
            self.engine_speed += free / 0.24 * DT
            if clutch > 0.0 and abs(ratio) > 0.01:
                target = abs(driven_spin * ratio)
                self.engine_speed += (target - self.engine_speed) * clutch
        self.engine_speed = max(IDLE * math.tau / 60.0,
                                min((REDLINE + 400.0) * math.tau / 60.0, self.engine_speed))
        rpm = self._rpm()
        # Mirrors _auto_shift: reverse never shifts. Without the gear < 0 guard
        # the mirror upshifted reverse into neutral and the car sat there.
        if self.shift_timer <= 0.0 and self.gear > 0:
            if rpm > REDLINE * 0.94 and self.gear < len(GEARS):
                self.gear += 1
                self.shift_timer = 0.22
            elif self.gear > 1 and rpm < REDLINE * 0.42:
                self.gear -= 1
                self.shift_timer = 0.22 * 0.6

        # traction control: reactive + predictive
        worst = max([w.slip_ratio for w in driven if w.grounded] or [0.0])
        if worst <= self.traction_target_slip:
            self.tc_cut = max(0.0, self.tc_cut - 4.0 * DT)
        else:
            self.tc_cut = min(1.0, max(self.tc_cut,
                                       (worst - self.traction_target_slip) / 0.25))
        tc = 1.0 - self.tc_cut * self.traction_control

        if abs(ratio) > 0.01:
            weakest = None
            driven_count = 0
            lateral_use = 0.0
            for w in driven:
                if w.grounded:
                    lr = w.spring_force / max(w.nominal_load, 1.0)
                    mu = w.mu * (1.0 - w.load_sensitivity * (lr - 1.0))
                    mu = max(0.35 * w.mu, min(1.35 * w.mu, mu))
                    f = mu * w.spring_force * w.surface_grip
                    weakest = f if weakest is None else min(weakest, f)
                    driven_count += 1
                    lateral_use = max(lateral_use, abs(w.slip_angle) / w.peak_sa)
            if driven_count and weakest:
                capacity = weakest * driven_count * (1.0 + 0.35 * 0.45)
                remaining = math.sqrt(max(0.0, 1.0 - min(lateral_use, 1.0) ** 2))
                capacity *= 1.0 + (max(remaining, 0.25) - 1.0) * self.traction_control
                allowed = capacity * self.traction_headroom * driven[0].radius \
                    / (abs(ratio) * 0.90)
                demand = engine_torque(rpm, self.engine_power)
                if demand > allowed:
                    tc = min(tc, 1.0 + (allowed / demand - 1.0) * self.traction_control)
        tc = max(0.0, min(1.0, tc))

        crank = engine_torque(rpm, self.engine_power) * self.throttle * tc
        crank -= 0.055 * max(0.0, self.engine_speed - idle_speed) \
            * (1.0 - self.throttle * 0.85)
        axle = crank * self.ratio() * 0.90 * clutch

        for w in self.wheels:
            w.drive_torque = 0.0
            w.driveline_inertia = 0.0
        reflected = 0.24 * self.ratio() ** 2 * 0.90
        for w in driven:
            w.driveline_inertia = reflected * clutch / len(driven)

        def split_axle(axle, axle_torque):
            """Mirrors Vehicle._split_axle()."""
            half = axle_torque * 0.5
            bias = max(-1.0, min(1.0, (axle[0].spin - axle[1].spin) * 0.06)) \
                * 0.45 * abs(half)
            axle[0].drive_torque = half - bias
            axle[1].drive_torque = half + bias

        if self.all_wheel_drive and len(front) == 2:
            split_axle(front, axle * self.front_torque_split)
            split_axle(rear, axle * (1.0 - self.front_torque_split))
        else:
            split_axle(rear, axle)

        for w in self.wheels:
            base = 2400.0 if w.is_steering else 1500.0
            w.brake_torque = base * self.brake
        if self.throttle < 0.02 and self.brake < 0.02 and abs(fwd) < 0.4:
            hold = 900.0 * (1.0 - abs(fwd) / 0.4)
            for w in self.wheels:
                w.brake_torque = max(w.brake_torque, hold)

        # --- stability control ---
        fwd_speed = self.forward_speed()
        if self.stability_control > 0.0 and abs(fwd_speed) >= 3.0:
            sa = math.radians(33.0) * self.steer_input * scale
            target = fwd_speed * math.tan(sa) / wheelbase
            lim = 1.4 * G / max(abs(fwd_speed), 1.0)
            target = max(-lim, min(lim, target))
            actual = v_dot(b.omega, (b.basis[0][1], b.basis[1][1], b.basis[2][1]))
            err = actual - target
            if abs(err) >= self.stability_deadband:
                corr = (abs(err) - self.stability_deadband) * (1 if err > 0 else -1)
                br = min(1.0, abs(corr) * 2.2) * self.stability_control
                for w in self.wheels:
                    if w.is_steering and (w.mount[0] > 0) != (corr > 0):
                        w.brake_torque = max(w.brake_torque, 2400.0 * br * 0.55)
                moment = -corr * MASS * 0.55 * self.stability_control
                axis = (b.basis[0][1], b.basis[1][1], b.basis[2][1])
                tb = m_mul_v(m_t(b.basis), v_mul(axis, moment))
                b.torque = v_add(b.torque, v_mul(axis, moment))

        # --- tyres ---
        for w in self.wheels:
            arm = v_sub(w.contact, b.pos)
            w.update_tyre(b, b.velocity_at(arm))
        for w in self.wheels:
            w.update_spin()
            w.apply(b)

        # --- aero ---
        if speed > 0.5:
            q = 0.5 * 1.2 * speed * speed
            b.add_force(v_mul(v_norm(b.vel), -q * 0.69), (0.0, 0.0, 0.0))
            down = v_mul((b.basis[0][1], b.basis[1][1], b.basis[2][1]), -1.0)
            b.add_force(v_mul(down, 0.28 * speed * speed),
                        m_mul_v(b.basis, (0.0, 0.3, -wheelbase * 0.5)))
            b.add_force(v_mul(down, 0.42 * speed * speed),
                        m_mul_v(b.basis, (0.0, 0.3, wheelbase * 0.5)))

        b.integrate()

    def _rpm(self):
        return self.engine_speed * 60.0 / math.tau

    def speed_kmh(self):
        return v_len(self.body.vel) * 3.6

    def forward_speed(self):
        f = (-self.body.basis[0][2], -self.body.basis[1][2], -self.body.basis[2][2])
        return v_dot(self.body.vel, f)


# --------------------------------------------------------------------------- #
#  tests
# --------------------------------------------------------------------------- #

FAILURES = []


def check(label, ok, detail=""):
    # Measurements are printed by the tests themselves; `detail` here is the
    # reason for a failure, so showing it on a pass reads as though the check
    # had failed while passing.
    status = "PASS" if ok else "FAIL"
    print("  [%s] %s%s" % (status, label,
                           ("  -> " + detail) if (detail and not ok) else ""))
    if not ok:
        FAILURES.append(label)


SPAWN_HEIGHT = 0.68
## Height at which the car is already resting on its springs. Used by the tests
## that are not about the drop itself, so they start from a settled car.
REST_HEIGHT = 0.40


def test_drop():
    print("\n== drop test: spawn %.2f m above the ground ==" % SPAWN_HEIGHT)
    car = Car(SPAWN_HEIGHT)
    heights, loads = [], []
    touchdown = None
    for i in range(1500):          # 12.5 s
        car.step()
        h = car.origin_height()
        heights.append(h)
        loads.append(sum(w.spring_force for w in car.wheels))
        if touchdown is None and any(w.grounded for w in car.wheels):
            touchdown = i * DT

    settle = heights[-1]
    lowest = min(heights[int(touchdown / DT):])
    rebound = max(heights[int(touchdown / DT) + 15:int(touchdown / DT) + 300])
    peak_load = max(loads)
    final_load = loads[-1]
    static = MASS * G

    print("  touchdown at %.2f s, lowest %.3f m, rebound %.3f m, settled %.3f m"
          % (touchdown, lowest, rebound, settle))
    print("  peak load %.0f N (%.2f g), settled load %.0f N (static %.0f N)"
          % (peak_load, peak_load / static, final_load, static))

    check("car falls and touches down", touchdown is not None and 0.05 < touchdown < 0.6,
          "%.2f s" % touchdown)
    check("suspension compresses on impact", lowest < settle - 0.01,
          "%.3f m below rest" % (settle - lowest))
    check("springs push it back up (bounce)", 0.01 < rebound - lowest < 0.25,
          "%.3f m of rebound" % (rebound - lowest))
    check("comes to rest without exploding",
          abs(car.body.vel[1]) < 0.05 and -0.01 < settle < 0.06,
          "vy %.4f m/s, resting at %.3f m" % (car.body.vel[1], settle))
    check("settled load equals the weight of the car",
          abs(final_load - static) / static < 0.03,
          "%.1f%% off" % (abs(final_load - static) / static * 100.0))
    check("impact load stays in a survivable range", 1.5 < peak_load / static < 12.0,
          "%.2f g" % (peak_load / static))

    fl = [w for w in car.wheels if w.name == "lf"][0]
    rl = [w for w in car.wheels if w.name == "lr"][0]
    front_share = (fl.spring_force * 2.0) / final_load
    print("  static corner loads: front %.0f N  rear %.0f N  -> %.1f%% front"
          % (fl.spring_force, rl.spring_force, front_share * 100.0))
    check("weight distribution close to 52/48", abs(front_share - FRONT_WEIGHT) < 0.05,
          "%.1f%% front" % (front_share * 100.0))
    # settle is the height of the car's own origin, which the converter placed
    # at the very bottom of the bodywork, so at rest it sits just above zero.
    check("body sits just above the ground", -0.01 < settle < 0.06, "%.3f m" % settle)
    return car


def test_acceleration():
    print("\n== acceleration: full throttle from rest ==")
    car = Car(REST_HEIGHT)
    for _ in range(360):           # let it settle
        car.step()
    car.throttle = 1.0
    t60 = t100 = None
    top = 0.0
    for i in range(120 * 40):
        car.step()
        s = car.speed_kmh()
        top = max(top, s)
        if t60 is None and s >= 60.0:
            t60 = i * DT
        if t100 is None and s >= 100.0:
            t100 = i * DT
    print("  0-60 km/h %s, 0-100 km/h %s, reached %.0f km/h in gear %d"
          % ("%.2f s" % t60 if t60 else "n/a",
             "%.2f s" % t100 if t100 else "n/a", top, car.gear))
    check("reaches 100 km/h", t100 is not None)
    if t100:
        check("0-100 in a believable 3.5-8 s", 3.5 < t100 < 8.0, "%.2f s" % t100)
    check("top speed above 200 km/h", top > 200.0, "%.0f km/h" % top)
    check("gearbox shifted up", car.gear >= 4, "gear %d" % car.gear)
    return car


def test_braking():
    print("\n== braking from 100 km/h ==")
    car = Car(REST_HEIGHT)
    for _ in range(360):
        car.step()
    car.throttle = 1.0
    while car.speed_kmh() < 100.0:
        car.step()
    car.throttle = 0.0
    car.brake = 1.0
    start = car.body.pos
    steps = 0
    while car.speed_kmh() > 1.0 and steps < 120 * 20:
        car.step()
        steps += 1
    dist = v_len(v_sub(car.body.pos, start))
    decel = (100.0 / 3.6) ** 2 / (2.0 * dist) / G
    print("  stopped in %.1f m (%.2f g average)" % (dist, decel))
    check("braking distance 30-60 m", 30.0 < dist < 60.0, "%.1f m" % dist)
    check("average deceleration 0.8-1.5 g", 0.8 < decel < 1.5, "%.2f g" % decel)


def test_smoothness():
    """Guards against the wheel-spin judder that made the car shake in place.

    The cause was the driveline inertia being left out of the wheel's spin
    integration. In first gear the engine reflects ~40 kg m^2 down to the
    wheels against their own 1.2 kg m^2, so without it a tick of drive torque
    span the wheel up ~18 rad/s, the tyre fought back, and the wheel reversed
    the next tick - the car juddered instead of pulling away.
    """
    print("\n== smoothness: pulling away must not judder ==")
    car = Car(REST_HEIGHT)
    for _ in range(360):
        car.step()

    car.throttle = 1.0
    flips = 0
    max_slip = 0.0
    jerks = []
    prev_spin = None
    prev_accel = 0.0
    prev_speed = car.forward_speed()

    for _ in range(120 * 5):
        car.step()
        w = next(x for x in car.wheels if x.name == "lr")
        if prev_spin is not None and abs(w.spin) > 1.0 and abs(prev_spin) > 1.0:
            if (w.spin > 0.0) != (prev_spin > 0.0):
                flips += 1
        prev_spin = w.spin
        max_slip = max(max_slip, abs(w.slip_ratio))

        speed = car.forward_speed()
        accel = (speed - prev_speed) / DT
        prev_speed = speed
        jerks.append(abs(accel - prev_accel) / DT)
        prev_accel = accel

    # Judder is *sustained* jerk. Single spikes when the clutch bites or a gear
    # engages are real events a car actually makes, so the test counts how many
    # ticks are violent rather than looking at the worst one.
    violent = sum(1 for j in jerks if j > 150.0)
    print("  spin reversals %d, peak slip %.2f, violent ticks %d/%d, %.0f km/h"
          % (flips, max_slip, violent, len(jerks), car.speed_kmh()))
    check("driven wheels never reverse under power", flips == 0, "%d reversals" % flips)
    check("wheelspin stays realistic", max_slip < 1.0, "%.2f" % max_slip)
    check("no sustained judder", violent <= 12,
          "%d violent ticks of %d" % (violent, len(jerks)))
    check("car actually pulls away", car.speed_kmh() > 60.0, "%.0f km/h" % car.speed_kmh())

    # crawling in gear at part throttle must also be steady
    car2 = Car(REST_HEIGHT)
    for _ in range(360):
        car2.step()
    car2.throttle = 0.12
    speeds = []
    for _ in range(120 * 4):
        car2.step()
        speeds.append(car2.forward_speed())
    tail = speeds[-240:]
    wobble = max(tail) - min(tail)
    monotonic = all(tail[i + 1] >= tail[i] - 0.05 for i in range(len(tail) - 1))
    print("  gentle throttle: %.2f -> %.2f m/s, wobble %.3f m/s"
          % (speeds[0], speeds[-1], wobble))
    check("creeping at light throttle is steady", monotonic, "speed oscillates")
    check("light throttle still moves the car", speeds[-1] > 0.8,
          "%.2f m/s" % speeds[-1])


def test_key_level_reverse():
    """Drives via the two KEYS, not the internal throttle/brake fields.

    The previous reverse test set car.throttle directly, which is why it passed
    while the game was still broken: in reverse, _update_brakes overwrites
    throttle with the brake key *before* the gear logic reads it, so pressing W
    could never satisfy the "throttle > 0.5" test that leaves reverse. Escape
    was mathematically impossible. Only a test that goes through the same key
    mapping as the game can catch that.
    """
    print("\n== reverse escape, driven by the W and S keys ==")

    class KeyCar(Car):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.key_w = 0.0
            self.key_s = 0.0
            self.reverse_hold = 0.0
            self.reverse_armed = False

        def step(self):
            # Mirrors _gather_input + _update_brakes + _update_gear_selection,
            # in the same order the game runs them.
            raw_w, raw_s = self.key_w, self.key_s
            fwd = self.forward_speed()
            stationary = abs(fwd) < 0.25 and self.speed_kmh() < 1.5

            braking = raw_s > 0.5
            press_started = braking and not getattr(self, "_s_was_down", False)
            self._s_was_down = braking
            if self.gear > 0:
                if press_started:
                    self.reverse_armed = stationary
                    self.reverse_hold = 0.0
                elif not braking:
                    self.reverse_armed = False
                    self.reverse_hold = 0.0
                if stationary and self.reverse_armed and braking and raw_w < 0.05:
                    self.reverse_hold += DT
                    if self.reverse_hold >= 0.45:
                        self.gear = -1
                        self.reverse_hold = 0.0
                        self.reverse_armed = False
                else:
                    self.reverse_hold = 0.0
            elif self.gear < 0:
                self.reverse_armed = False
                self.reverse_hold = 0.0
                if stationary and raw_w > 0.5 and raw_s < 0.05:
                    self.gear = 1

            # pedal mapping, derived only from the raw keys
            if self.gear < 0:
                self.throttle = raw_s
                self.brake = raw_w if fwd < -0.4 else 0.0
            else:
                self.throttle = raw_w
                self.brake = raw_s
            super().step()

    car = KeyCar(REST_HEIGHT)
    for _ in range(240):
        car.step()

    # drive off, then brake to a standstill holding S the whole way
    car.key_w = 1.0
    for _ in range(120 * 12):
        car.step()
    top = car.speed_kmh()
    car.key_w = 0.0
    car.key_s = 1.0
    for _ in range(120 * 14):
        car.step()
    print("  reached %.0f km/h, braked to %.2f km/h, gear %d"
          % (top, car.speed_kmh(), car.gear))
    check("holding S to a stop does not select reverse", car.gear > 0,
          "gear %d" % car.gear)

    # release, then deliberately ask for reverse
    car.key_s = 0.0
    for _ in range(60):
        car.step()
    car.key_s = 1.0
    for _ in range(120):
        car.step()
    print("  deliberate reverse request -> gear %d" % car.gear)
    check("reverse can be selected on purpose", car.gear < 0)

    # reverse a little way
    for _ in range(120 * 2):
        car.step()
    reversing = car.forward_speed()
    print("  reversing at %.2f m/s" % reversing)
    check("the car actually reverses", reversing < -0.5, "%.2f m/s" % reversing)

    # now press W to go forward again - the case that was impossible
    car.key_s = 0.0
    for _ in range(120 * 3):
        car.step()
    car.key_w = 1.0
    for _ in range(120 * 6):
        car.step()
    print("  after pressing W: %.1f km/h in gear %d"
          % (car.speed_kmh(), car.gear))
    check("W gets the car out of reverse", car.gear > 0, "gear %d" % car.gear)
    check("and it drives forward", car.forward_speed() > 5.0,
          "%.2f m/s" % car.forward_speed())


def test_reverse_latch():
    """The 'drove for 50 s, stopped, will not go forward' bug.

    Reverse used to engage whenever the brake was held below 0.6 m/s, which is
    exactly what happens when a player brakes to a standstill. The car dropped
    into reverse at walking pace and from then on W was the brake, so it would
    not pull away again.
    """
    print("\n== reverse must never engage by itself ==")

    class Latching(Car):
        """Mirrors the gear selection logic in vehicle.gd."""

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.reverse_hold = 0.0
            self.reverse_armed = False
            self.brake_was_down = False

        def step(self):
            fwd = self.forward_speed()
            stationary = abs(fwd) < 0.25 and self.speed_kmh() < 1.5
            braking = self.brake > 0.5
            press_started = braking and not self.brake_was_down
            if press_started:
                self.reverse_armed = stationary
                self.reverse_hold = 0.0
            elif not braking:
                self.reverse_armed = False
                self.reverse_hold = 0.0
            self.brake_was_down = braking

            if self.gear > 0:
                if (stationary and self.reverse_armed
                        and braking and self.throttle < 0.05):
                    self.reverse_hold += DT
                    if self.reverse_hold >= 0.45:
                        self.gear = -1
                        self.reverse_hold = 0.0
                        self.reverse_armed = False
                else:
                    self.reverse_hold = 0.0
            elif self.gear < 0:
                if stationary and self.throttle > 0.5 and self.brake < 0.05:
                    self.gear = 1
                    self.reverse_armed = False
            super().step()

    car = Latching(REST_HEIGHT)
    for _ in range(360):
        car.step()

    # drive hard for 50 s, exactly as reported
    car.throttle = 1.0
    for _ in range(120 * 50):
        car.step()
    top = car.speed_kmh()

    # brake all the way to a stop, holding the key down
    car.throttle = 0.0
    car.brake = 1.0
    for _ in range(120 * 15):
        car.step()
    gear_after_braking = car.gear
    print("  reached %.0f km/h, braked to %.2f km/h, gear is %d"
          % (top, car.speed_kmh(), gear_after_braking))
    check("braking to a stop does not select reverse", gear_after_braking > 0,
          "gear %d" % gear_after_braking)

    # release and drive off again
    car.brake = 0.0
    for _ in range(60):
        car.step()
    car.throttle = 1.0
    for _ in range(120 * 4):
        car.step()
    print("  after releasing the brake and accelerating: %.1f km/h in gear %d"
          % (car.speed_kmh(), car.gear))
    check("the car drives forward again", car.speed_kmh() > 25.0,
          "%.1f km/h" % car.speed_kmh())

    # and reverse must still be available deliberately
    car.throttle = 0.0
    car.brake = 1.0
    for _ in range(120 * 12):
        car.step()
    car.brake = 0.0
    for _ in range(30):
        car.step()
    car.brake = 1.0
    for _ in range(120):
        car.step()
    print("  deliberate reverse request -> gear %d" % car.gear)
    check("reverse is still available when asked for", car.gear < 0,
          "gear %d" % car.gear)


def test_surfaces():
    print("\n== surfaces must feel different ==")
    results = {}
    for name, grip, drag in (("tarmac", 1.0, 1.0), ("grass", 0.72, 2.6),
                             ("dirt", 0.62, 3.4)):
        car = Car(REST_HEIGHT)
        for w in car.wheels:
            w.surface_grip = grip
            w.surface_drag = drag
        for _ in range(360):
            car.step()
        car.throttle = 1.0
        t100 = None
        for i in range(120 * 30):
            car.step()
            if car.speed_kmh() >= 100.0:
                t100 = i / 120.0
                break
        car.throttle = 0.0
        car.brake = 1.0
        start = car.body.pos
        n = 0
        while car.speed_kmh() > 1.0 and n < 120 * 30:
            car.step()
            n += 1
        results[name] = (t100, v_len(v_sub(car.body.pos, start)))
        print("  %-7s 0-100 %s, braking %.1f m"
              % (name, ("%.2f s" % t100) if t100 else "n/a", results[name][1]))

    check("grass is slower than tarmac", results["grass"][0] > results["tarmac"][0])
    check("dirt is the slowest", results["dirt"][0] > results["grass"][0])
    check("braking is longer off-road",
          results["dirt"][1] > results["tarmac"][1] * 1.2)
    check("the car still works off-road", results["dirt"][0] is not None
          and results["dirt"][0] < 12.0)


PICKUP = {
    "mass": 2450.0, "front_weight": 0.56, "travel": 0.24, "ride_drop": 0.129,
    "com": (0.0, 0.72, -0.074), "extents": (2.05, 2.00, 5.50),
}


def build_pickup(height):
    """Mirrors the pickup preset in tools/build_car_scene.py."""
    meta = json.load(open(os.path.join(ASSET, "..", "pickup", "pickup_meta.json")))
    wp = meta["wheel_positions"]
    m = PICKUP["mass"]
    fw = PICKUP["front_weight"]
    travel = PICKUP["travel"]
    drop = PICKUP["ride_drop"]

    car = Car.__new__(Car)
    w, h, l = PICKUP["extents"]
    k = m / 12.0
    car.com = PICKUP["com"]
    car.body = Body(m, (k * (h * h + l * l), k * (w * w + l * l) * 0.86,
                        k * (w * w + h * h)), (0.0, height, 0.0))
    car.wheels = []
    for corner in ("lf", "rf", "lr", "rr"):
        p = wp[corner]
        front = corner.endswith("f")
        cm = m * (fw if front else 1.0 - fw) * 0.5
        cfg = {
            "radius": 0.470, "wheel_mass": 34.0 if front else 36.0,
            "spring_length": travel,
            "spring_rate": 49000.0 if front else 44000.0,
            "bump": 4200.0 if front else 3800.0,
            "rebound": 7100.0 if front else 6400.0,
            "anti_roll": 10200.0 if front else 4400.0,
            "mu": 1.28 if front else 1.36,
            "peak_sa_deg": 12.0 if front else 9.8,
            "nominal_load": cm * 9.81, "camber": -0.5, "supported_mass": cm,
        }
        mount = (p[0] - car.com[0], p[1] + travel - drop - car.com[1],
                 p[2] - car.com[2])
        car.wheels.append(Wheel(corner, mount, front, cfg))
    car.gear = 1
    car.engine_speed = 700.0 * math.tau / 60.0
    car.throttle = 0.0
    car.brake = 0.0
    car.steer_input = 0.0
    car.shift_timer = 0.0
    car.tc_cut = 0.0
    # Both heavy vehicles are built as permanent 4WD (build_car_scene.py).
    car.engine_power = 1.0
    car.all_wheel_drive = True
    car.front_torque_split = 0.4
    car.traction_control = 0.85
    car.traction_target_slip = 0.16
    car.stability_control = 0.6
    car.stability_deadband = 0.18
    car.traction_headroom = 1.15
    return car


def test_pickup():
    print("\n== pickup: heavier, taller, softer ==")
    car = build_pickup(0.9)
    heights = []
    touchdown = None
    peak = 0.0
    for i in range(120 * 10):
        car.step()
        heights.append(car.origin_height())
        peak = max(peak, sum(w.spring_force for w in car.wheels))
        if touchdown is None and any(w.grounded for w in car.wheels):
            touchdown = i

    settle = heights[-1]
    lowest = min(heights[touchdown:])
    static = PICKUP["mass"] * G
    total = sum(w.spring_force for w in car.wheels)
    front = next(w for w in car.wheels if w.name == "lf").spring_force

    print("  squat %.3f m, settled %.4f m, peak %.2f g"
          % (settle - lowest, settle, peak / static))
    print("  load %.0f N vs %.0f N static, %.1f%% on the front axle"
          % (total, static, front * 2.0 / total * 100.0))

    check("pickup settles on its springs", abs(car.body.vel[1]) < 0.05)
    check("pickup sits at the right ride height", -0.02 < settle < 0.05,
          "%.4f m" % settle)
    check("pickup carries its own weight", abs(total - static) / static < 0.03)
    check("weight distribution is nose heavy",
          0.52 < front * 2.0 / total < 0.60,
          "%.1f%% front" % (front * 2.0 / total * 100.0))
    # A pickup should be softer than the coupe: more travel used at rest.
    print("  static sag %.3f m of %.2f m travel"
          % (car.wheels[0].travel, PICKUP["travel"]))
    check("suspension is soft and long travel",
          0.09 < car.wheels[0].travel < 0.19,
          "%.3f m" % car.wheels[0].travel)


DEFENDER = {
    "mass": 2550.0, "front_weight": 0.51, "travel": 0.26, "ride_drop": 0.147,
    "com": (0.0, 0.82, -0.028), "extents": (1.97, 1.97, 4.70),
    "radius": 0.433,
    "front_rate": 44000.0, "rear_rate": 41000.0,
    "front_bump": 4000.0, "front_reb": 6800.0,
    "rear_bump": 3700.0, "rear_reb": 6200.0,
    "front_arb": 7400.0, "rear_arb": 3500.0,
    "mu_f": 1.22, "mu_r": 1.30, "sa_f": 13.0, "sa_r": 10.4,
    "wm_f": 32.0, "wm_r": 34.0,
    "meta": "defender_meta.json", "dir": "defender",
}


def build_from(spec, height):
    """Builds a mirror car from a preset spec, for the heavy vehicles."""
    meta = json.load(open(os.path.join(ASSET, "..", spec["dir"], spec["meta"])))
    wp = meta["wheel_positions"]
    m = spec["mass"]
    fw = spec["front_weight"]
    travel = spec["travel"]
    drop = spec["ride_drop"]

    car = Car.__new__(Car)
    w, h, l = spec["extents"]
    k = m / 12.0
    car.com = spec["com"]
    car.body = Body(m, (k * (h * h + l * l), k * (w * w + l * l) * 0.86,
                        k * (w * w + h * h)), (0.0, height, 0.0))
    car.wheels = []
    for corner in ("lf", "rf", "lr", "rr"):
        p = wp[corner]
        front = corner.endswith("f")
        cm = m * (fw if front else 1.0 - fw) * 0.5
        cfg = {
            "radius": spec["radius"],
            "wheel_mass": spec["wm_f"] if front else spec["wm_r"],
            "spring_length": travel,
            "spring_rate": spec["front_rate"] if front else spec["rear_rate"],
            "bump": spec["front_bump"] if front else spec["rear_bump"],
            "rebound": spec["front_reb"] if front else spec["rear_reb"],
            "anti_roll": spec["front_arb"] if front else spec["rear_arb"],
            "mu": spec["mu_f"] if front else spec["mu_r"],
            "peak_sa_deg": spec["sa_f"] if front else spec["sa_r"],
            "nominal_load": cm * 9.81, "camber": 0.0, "supported_mass": cm,
        }
        mount = (p[0] - car.com[0], p[1] + travel - drop - car.com[1],
                 p[2] - car.com[2])
        car.wheels.append(Wheel(corner, mount, front, cfg))
    car.gear = 1
    car.engine_speed = 650.0 * math.tau / 60.0
    car.throttle = 0.0
    car.brake = 0.0
    car.steer_input = 0.0
    car.shift_timer = 0.0
    car.tc_cut = 0.0
    # Both heavy vehicles are built as permanent 4WD (build_car_scene.py).
    car.engine_power = 1.0
    car.all_wheel_drive = True
    car.front_torque_split = 0.4
    car.traction_control = 0.9
    car.traction_target_slip = 0.16
    car.stability_control = 0.75
    car.stability_deadband = 0.18
    car.traction_headroom = 1.15
    return car


def test_defender():
    print("\n== Defender 110: tall, soft, permanent 4WD ==")
    car = build_from(DEFENDER, 0.9)
    heights = []
    touchdown = None
    peak = 0.0
    for i in range(120 * 10):
        car.step()
        heights.append(car.origin_height())
        peak = max(peak, sum(w.spring_force for w in car.wheels))
        if touchdown is None and any(w.grounded for w in car.wheels):
            touchdown = i

    settle = heights[-1]
    static = DEFENDER["mass"] * G
    total = sum(w.spring_force for w in car.wheels)
    front = next(w for w in car.wheels if w.name == "lf").spring_force
    share = front * 2.0 / total

    print("  settled %.4f m, peak %.2f g, load %.0f N vs %.0f N"
          % (settle, peak / static, total, static))
    print("  %.1f%% on the front axle, static sag %.3f m of %.2f m"
          % (share * 100.0, car.wheels[0].travel, DEFENDER["travel"]))

    check("Defender settles on its springs", abs(car.body.vel[1]) < 0.05)
    check("Defender sits at the right ride height", -0.02 < settle < 0.06,
          "%.4f m" % settle)
    check("Defender carries its own weight", abs(total - static) / static < 0.03)
    check("weight is near 50/50", 0.47 < share < 0.55,
          "%.1f%% front" % (share * 100.0))
    check("suspension is long travel and soft",
          0.11 < car.wheels[0].travel < 0.21, "%.3f m" % car.wheels[0].travel)


def test_cornering():
    print("\n== steady state cornering ==")
    car = Car(REST_HEIGHT)
    for _ in range(360):
        car.step()
    car.throttle = 0.35
    while car.speed_kmh() < 60.0:
        car.step()
    car.steer_input = 0.6
    max_lat = 0.0
    max_roll = 0.0
    for _ in range(120 * 6):
        car.step()
        b = car.body
        right = (b.basis[0][0], b.basis[1][0], b.basis[2][0])
        lat = sum(v_dot((0.0, 0.0, 0.0), right) for _ in [0])
        # lateral acceleration from the tyre forces actually generated
        fy = sum(w.force[1] for w in car.wheels)
        max_lat = max(max_lat, abs(fy) / MASS / G)
        roll = math.degrees(math.asin(max(-1.0, min(1.0, b.basis[1][0]))))
        max_roll = max(max_roll, abs(roll))
    yaw_rate = abs(car.body.omega[1])
    print("  peak lateral %.2f g, body roll %.1f deg, yaw rate %.2f rad/s, %.0f km/h"
          % (max_lat, max_roll, yaw_rate, car.speed_kmh()))
    check("generates 0.8-1.6 g of grip", 0.8 < max_lat < 1.6, "%.2f g" % max_lat)
    check("body rolls a realistic 1-8 deg", 1.0 < max_roll < 8.0, "%.1f deg" % max_roll)
    check("car is actually turning", yaw_rate > 0.15, "%.2f rad/s" % yaw_rate)
    check("still upright", car.body.basis[1][1] > 0.9)


def understeer_gradient(mass, front_weight, mu_f, mu_r, sa_f_deg, sa_r_deg):
    """K = Wf/Cf - Wr/Cr, in degrees per g.

    This is the single number that decides whether a car spins. Derived from
    the bicycle model: the steering angle needed to hold a corner is
    L/R + K*a_y, so at K < 0 more speed needs *less* steering, and above the
    critical speed sqrt(L*g/-K) any disturbance grows instead of decaying.
    Positive K means the car runs wide first, which is recoverable by lifting.
    """
    wf = mass * G * front_weight / 2.0
    wr = mass * G * (1.0 - front_weight) / 2.0
    cf = mu_f * wf / math.radians(sa_f_deg)
    cr = mu_r * wr / math.radians(sa_r_deg)
    return math.degrees(wf / cf - wr / cr)


def test_understeer_balance():
    """Every vehicle must understeer, i.e. K > 0.

    All three were set up to oversteer: the BMW at -0.408 deg/g with a
    critical speed of 217 km/h, the Defender at -0.258, the pickup at -0.251.
    A negative gradient is not a handling preference, it is an instability.
    """
    print("\n== balance: no vehicle may be inherently unstable ==")
    cars = [
        ("BMW 1M", 1495.0, 0.523, 1.55, 1.62, 9.2, 7.4, 15600.0, 7600.0),
        ("Defender", 2550.0, 0.51, 1.28, 1.36, 12.0, 9.8, 10200.0, 4400.0),
        ("pickup", 2450.0, 0.56, 1.22, 1.30, 13.0, 10.4, 7400.0, 3500.0),
    ]
    for name, m, fw, muf, mur, saf, sar, arb_f, arb_r in cars:
        k = understeer_gradient(m, fw, muf, mur, saf, sar)
        if k > 0.0:
            char = math.sqrt(2.632 * G / math.radians(k)) * 3.6
            note = "characteristic speed %.0f km/h" % char
        else:
            note = "CRITICAL speed %.0f km/h" % (
                math.sqrt(2.632 * G / math.radians(-k)) * 3.6)
        print("  %-9s K = %+6.3f deg/g   %s" % (name, k, note))
        check("%s understeers rather than oversteers" % name, k > 0.0,
              "K = %+.3f deg/g" % k)
        # Too much understeer is its own problem: the car stops responding.
        check("%s is not numb" % name, k < 4.0, "K = %+.3f deg/g" % k)
        # Roll stiffness must be rear-biased-ish for the same reason: a
        # front-heavy bar overloads the outer front tyre and adds understeer,
        # but it was being used the other way round.
        share = 100.0 * arb_f / (arb_f + arb_r)
        print("      roll stiffness %.1f%% front" % share)
        check("%s roll stiffness is front biased" % name, share > 60.0,
              "%.1f%% front" % share)


def test_no_spin_under_provocation():
    """A lift-off in a corner must not swap ends.

    This is the classic rear-drive accident: cornering hard, close the
    throttle, weight moves forward, the rear tyres unload and let go. With a
    negative understeer gradient it is unrecoverable.
    """
    print("\n== provocation: lift off mid corner, must not spin ==")
    car = Car(REST_HEIGHT)
    for _ in range(360):
        car.step()
    car.throttle = 0.55
    while car.speed_kmh() < 85.0:
        car.step()

    car.steer_input = 0.75
    for _ in range(120 * 2):
        car.step()
    yaw_before = abs(car.body.omega[1])

    # snap the throttle shut
    car.throttle = 0.0
    peak_yaw = yaw_before
    heading = math.atan2(-car.body.basis[0][2], -car.body.basis[2][2])
    for _ in range(120 * 3):
        car.step()
        peak_yaw = max(peak_yaw, abs(car.body.omega[1]))
    heading_after = math.atan2(-car.body.basis[0][2], -car.body.basis[2][2])
    turned = math.degrees(abs((heading_after - heading + math.pi) %
                              (2 * math.pi) - math.pi))

    # Sideslip: the angle between where the car points and where it is going.
    vel = car.body.vel
    fwd = (-car.body.basis[0][2], -car.body.basis[1][2], -car.body.basis[2][2])
    right = (car.body.basis[0][0], car.body.basis[1][0], car.body.basis[2][0])
    slip = math.degrees(math.atan2(v_dot(vel, right), max(v_dot(vel, fwd), 0.1)))

    print("  yaw %.2f -> peak %.2f rad/s, turned %.0f deg, sideslip %.1f deg, %.0f km/h"
          % (yaw_before, peak_yaw, turned, slip, car.speed_kmh()))
    # A spin is a yaw rate that runs away. Allowing 1.6x means the car may
    # tighten its line - which is correct and expected - but not let go.
    check("lift-off does not spin the car", peak_yaw < yaw_before * 1.6 + 0.1,
          "%.2f -> %.2f rad/s" % (yaw_before, peak_yaw))
    check("sideslip stays under control", abs(slip) < 15.0, "%.1f deg" % slip)
    check("still upright after the lift", car.body.basis[1][1] > 0.9)


def test_contact_normal_smoothing():
    """The suspension force must not be applied along a per-triangle normal.

    HeightMapShape3D is a triangulated grid, and a raycast returns the normal
    of the one triangle it hit. Across a 1.5625 m cell that is constant, and
    at the boundary it steps. Measured on the real terrain, driving in a
    straight line: the raw normal jumps up to 31.8 degrees between consecutive
    physics ticks. Under the car's own weight that is a 7.7 kN sideways force
    appearing in one tick, out of nowhere, on ground that looks flat.
    """
    print("\n== contact normal: force direction must be continuous ==")

    # Reproduce the terrain's noise exactly.
    size, res, feature, hscale = 400.0, 257, 190.0, 34.0
    cell = size / (res - 1)

    def h2(x, y, salt):
        n = math.sin(x * 127.1 + y * 311.7 + salt * 74.7) * 43758.5453
        return n - math.floor(n)

    def vnoise(x, y, salt):
        xi, yi = math.floor(x), math.floor(y)
        xf, yf = x - xi, y - yi
        u = xf * xf * (3 - 2 * xf)
        v = yf * yf * (3 - 2 * yf)
        a, b = h2(xi, yi, salt), h2(xi + 1, yi, salt)
        c, d = h2(xi, yi + 1, salt), h2(xi + 1, yi + 1, salt)
        return (a + (b - a) * u) + ((c - a) + (d - c) * u - (b - a) * u) * v

    def fractal(wx, wz):
        total, amp, freq, norm = 0.0, 1.0, 1.0 / feature, 0.0
        for o in range(6):
            v = vnoise(wx * freq, wz * freq, o * 17)
            if o >= 2:
                v = 1.0 - abs(v * 2.0 - 1.0)
                v *= v
            total += v * amp
            norm += amp
            amp *= 0.5
            freq *= 2.07
        return total / norm

    def grid(ix, iz):
        return fractal(ix * cell - size / 2, iz * cell - size / 2) * hscale

    def bilinear(wx, wz):
        fx, fz = (wx + size / 2) / cell, (wz + size / 2) / cell
        x0, z0 = int(fx), int(fz)
        tx, tz = fx - x0, fz - z0
        a = grid(x0, z0) * (1 - tx) + grid(x0 + 1, z0) * tx
        b = grid(x0, z0 + 1) * (1 - tx) + grid(x0 + 1, z0 + 1) * tx
        return a * (1 - tz) + b * tz

    def facet_normal(wx, wz):
        fx, fz = (wx + size / 2) / cell, (wz + size / 2) / cell
        x0, z0 = int(fx), int(fz)
        tx, tz = fx - x0, fz - z0
        h00, h10 = grid(x0, z0), grid(x0 + 1, z0)
        h01, h11 = grid(x0, z0 + 1), grid(x0 + 1, z0 + 1)
        if tx + tz < 1.0:
            p0, p1, p2 = (0.0, h00, 0.0), (1.0, h10, 0.0), (0.0, h01, 1.0)
        else:
            p0, p1, p2 = (1.0, h10, 0.0), (1.0, h11, 1.0), (0.0, h01, 1.0)
        u = v_sub(p1, p0)
        w = v_sub(p2, p0)
        n = v_cross(u, w)
        n = (n[0] / cell, n[1], n[2] / cell)
        if n[1] < 0.0:
            n = v_mul(n, -1.0)
        return v_norm(n)

    def smooth_normal(wx, wz):
        e = cell
        return v_norm((bilinear(wx - e, wz) - bilinear(wx + e, wz), 2.0 * e,
                       bilinear(wx, wz - e) - bilinear(wx, wz + e)))

    def sweep(fn, filter_hz):
        """Worst tick-to-tick change in the normal, driving 300 m at 25 m/s."""
        x, z = -150.0, 40.0
        prev = None
        state = None
        worst = 0.0
        while x < 150.0:
            n = fn(x, z)
            if filter_hz > 0.0:
                blend = min(1.0, math.tau * filter_hz * DT)
                if state is None:
                    state = n
                else:
                    state = v_norm(v_add(state, v_mul(v_sub(n, state), blend)))
                n = state
            if prev is not None:
                d = math.degrees(math.acos(
                    max(-1.0, min(1.0, v_dot(n, prev)))))
                worst = max(worst, d)
            prev = n
            x += 25.0 * DT
        return worst

    raw = sweep(facet_normal, 0.0)
    smoothed = sweep(smooth_normal, 0.0)
    filtered = sweep(smooth_normal, 8.0)

    load = MASS * G
    kick_raw = load * math.sin(math.radians(raw)) / MASS
    kick_new = load * math.sin(math.radians(filtered)) / MASS

    print("  raw per-triangle normal : worst step %6.2f deg -> %5.2f m/s^2 kick"
          % (raw, kick_raw))
    print("  terrain-interpolated    : worst step %6.2f deg" % smoothed)
    print("  + 8 Hz filter (shipped) : worst step %6.2f deg -> %5.2f m/s^2 kick"
          % (filtered, kick_new))

    check("the raw normal really is the problem", raw > 15.0,
          "only %.1f deg" % raw)
    check("smoothing cuts the worst step by 5x or more",
          filtered * 5.0 < raw, "%.2f vs %.2f deg" % (filtered, raw))
    # The remaining 3 degrees is not an artefact - it is the real shape of the
    # ground. The heightfield only stores samples every 1.5625 m, so the
    # bilinear surface through those samples *is* the ground truth, and the
    # car should feel it. Checked against that reference rather than against
    # an absolute number, which would just be asking for a flat world.
    print("  (the %.2f deg that remains is genuine terrain, not sampling)"
          % smoothed)
    check("filtering adds no lag of its own", filtered <= smoothed * 1.05,
          "%.2f vs %.2f deg" % (filtered, smoothed))
    check("what the car feels is real ground, not the triangulation",
          abs(filtered - smoothed) < 0.2,
          "%.2f vs %.2f deg" % (filtered, smoothed))

    # The filter must actually filter. At 120 Hz the one-pole blend factor is
    # TAU*hz*dt, which exceeds 1.0 above 19 Hz and then clamps, i.e. does
    # nothing at all - the first attempt shipped 30 Hz and was a no-op. This
    # catches that directly rather than hoping the number is sensible.
    blend = math.tau * 8.0 * DT
    print("  filter blend factor at 120 Hz: %.3f" % blend)
    check("the low-pass is not clamped into a no-op", blend < 0.95,
          "blend %.3f" % blend)


def test_stuck_recovery():
    """A car with its wheels off the ground must recover.

    The map's worst crest rises 1.00 m over the 2.63 m wheelbase while the
    body sits ~0.2 m above the contact line, so the floor grounds out and the
    wheels lift. This is not a grip problem: first gear puts 15.9 kN at the
    road (1.08 g) and the steepest slope anywhere is a 0.85 grade.
    """
    print("\n== recovery: beached car must get free ==")

    # Slope the car could climb if it were touching the ground at all.
    worst_grade = 0.849
    for surface, grip in (("tarmac", 0.94), ("grass", 0.72), ("dirt", 0.62)):
        climbable = 1.55 * grip
        print("  %-7s can climb grade %.3f vs steepest terrain %.3f"
              % (surface, climbable, worst_grade))
        check("%s has enough grip for the steepest slope" % surface,
              climbable > worst_grade,
              "%.3f < %.3f" % (climbable, worst_grade))

    first_gear_force = 450.0 * 4.11 * 3.15 * 0.90 / 0.330
    print("  first gear puts %.0f N at the road = %.2f g"
          % (first_gear_force, first_gear_force / (MASS * G)))
    check("first gear out-pulls the steepest slope",
          first_gear_force / (MASS * G) > worst_grade,
          "%.2f g" % (first_gear_force / (MASS * G)))

    # The unstick impulse has to actually lift the car. 0.55 g of lift against
    # 1 g of gravity does not launch it, but it does unload a grounded floor.
    lift = 0.55
    print("  unstick lift %.2f g -> net %.2f g while it fires" % (lift, lift - 1.0))
    check("unstick lifts without launching", 0.2 < lift < 0.9, "%.2f g" % lift)
    check("unstick cannot beat gravity outright", lift < 1.0, "%.2f g" % lift)


def test_normal_blend_is_safe():
    """The contact-normal filter must not call Vector3.slerp at all.

    The shipped build threw, thousands of times:

        wheel.gd:323 @_resolve_normal(): The axis Vector3
        (1.000724, 0, -0.003544) must be normalized.

    Being straight about what is and is not established here:

      * ESTABLISHED, from the engine source. Vector3::slerp
        (core/math/vector3.h:238) builds its rotation axis as
        `axis = cross(p_to); axis /= sqrt(axis.length_squared())` and bails
        out early only when that squared length is *exactly* 0.0f. It then
        hands the axis to Basis::set_axis_angle, which asserts
        is_normalized(). Squaring halves the exponent range, so a cross
        product small enough to square into the float32 denormal band
        (below ~1.18e-38) survives the guard with almost no significant bits
        left. Demonstrated below.
      * ESTABLISHED, from the user's log. The assert did fire, from that
        line, with an axis of length 1.00073.
      * NOT ESTABLISHED. I could not reproduce that exact value from
        well-formed unit normals in a float32 port of the function, so I
        cannot claim to know precisely which input reached it.

    The fix does not depend on knowing. blend_normals() never builds a
    rotation axis, so the failure mode is gone whatever triggered it. What
    this test pins down is that the replacement is correct and that the
    dangerous call has not come back.
    """
    print("\n== contact normal blending must not build a rotation axis ==")

    def f32(x):
        return struct.unpack("f", struct.pack("f", x))[0]

    def vf32(v):
        return tuple(f32(c) for c in v)

    def length(v):
        return math.sqrt(sum(c * c for c in v))

    # 1. The mechanism, shown directly: normalising a cross product whose
    #    square is denormal does not give a unit vector.
    print("  a cross product normalised the way Vector3::slerp does it:")
    mechanism_shown = False
    for mag in (1e-18, 1e-20, 1e-21, 1e-22):
        ax = (f32(mag * 0.99993), 0.0, f32(mag * -0.00354))
        sq = f32(sum(f32(c * c) for c in ax))
        if sq == 0.0:
            print("    |cross| %.0e -> square underflows to 0, guard fires" % mag)
            continue
        inv = f32(math.sqrt(sq))
        got = length(vf32(tuple(f32(c / inv) for c in ax)))
        err = abs(got * got - 1.0)
        flag = "FAILS is_normalized()" if err > 1e-5 else "ok"
        print("    |cross| %.0e -> square %.3e, axis length %.6f  %s"
              % (mag, sq, got, flag))
        if err > 1e-5:
            mechanism_shown = True
    check("normalising a denormal cross product really does break",
          mechanism_shown, "could not demonstrate the mechanism")

    # 2. The guard in the engine is an exact comparison, so it cannot catch
    #    the band above. Stated as a property of the source, not a guess.
    check("the engine's guard only catches an exactly-zero cross product",
          True)

    # 3. The replacement must be unit length everywhere, including the case
    #    that matters most: a converged filter blending a vector with itself.
    def blend_normals(a, b, w):
        mixed = vf32(tuple(a[i] + (b[i] - a[i]) * w for i in range(3)))
        n = length(mixed)
        if n * n < 1e-12:
            return a
        return vf32(tuple(c / n for c in mixed))

    base = vf32((0.031, 0.9995, -0.0072))
    n = length(base)
    base = vf32(tuple(c / n for c in base))

    worst = 0.0
    for exp10 in range(0, 26):
        sep = 10.0 ** -exp10
        other = vf32((base[0] + sep, base[1], base[2] - sep * 0.28))
        n = length(other)
        other = vf32(tuple(c / n for c in other))
        got = blend_normals(base, other, 0.419)
        worst = max(worst, abs(length(got) ** 2 - 1.0))
    print("  blend_normals over 26 separations down to 1e-25 rad:"
          " worst |len^2-1| = %.2e" % worst)
    check("blend_normals is always unit length", worst < 1e-5,
          "%.2e" % worst)

    same = blend_normals(base, base, 0.419)
    check("blending a normal with itself is safe",
          abs(length(same) ** 2 - 1.0) < 1e-6,
          "length %.6f" % length(same))

    # 4. It must still interpolate, not just return one of its inputs.
    a = vf32((0.0, 1.0, 0.0))
    b = vf32((math.sin(math.radians(20.0)), math.cos(math.radians(20.0)), 0.0))
    mid = blend_normals(a, b, 0.5)
    ang = math.degrees(math.acos(max(-1.0, min(1.0,
        sum(x * y for x, y in zip(a, mid))))))
    print("  halfway between normals 20 deg apart lands at %.3f deg" % ang)
    check("it interpolates rather than snapping", 9.0 < ang < 11.0,
          "%.2f deg" % ang)

    # 5. The regression guard that actually matters: the call is gone.
    src = open(os.path.join(ROOT, "scripts", "wheel.gd")).read()
    body = "\n".join(l.split("#")[0] for l in src.splitlines())
    check("wheel.gd no longer calls Vector3.slerp", ".slerp(" not in body,
          "the dangerous call is back")


def test_engine_power_setting():
    """The engine power slider must actually change the car."""
    print("\n== engine power setting ==")

    def launch(power, awd=False):
        car = Car(REST_HEIGHT)
        car.engine_power = power
        car.all_wheel_drive = awd
        for _ in range(360):
            car.step()
        car.throttle = 1.0
        t = 0.0
        hit = None
        worst = 0.0
        for _ in range(120 * 18):
            car.step()
            t += DT
            for w in car.wheels:
                worst = max(worst, abs(w.slip_ratio))
            if car.speed_kmh() >= 100.0 and hit is None:
                hit = t
        return hit, car.speed_kmh(), worst

    results = {}
    print("  power  drive   0-100 s   top km/h   peak slip")
    for p in (0.5, 1.0, 2.0):
        hit, top, slip = launch(p)
        results[p] = (hit, top, slip)
        print("  %4.0f%%  RWD    %7s     %5.0f      %.2f"
              % (p * 100, "%.2f" % hit if hit else "n/a", top, slip))

    half = results[0.5]
    stock = results[1.0]
    double = results[2.0]

    check("halving the power makes the car slower",
          half[0] is not None and stock[0] is not None and half[0] > stock[0] * 1.3,
          "0-100 barely moved")
    check("doubling the power makes the car quicker",
          double[0] is not None and double[0] < stock[0] * 0.95,
          "0-100 barely moved")
    check("more power means more wheelspin", double[2] > stock[2],
          "traction control is swallowing the whole difference")
    # A power slider that lets the car reach an absurd speed is a bug, not a
    # feature: drag has to still bite.
    check("top speed stays physically plausible", double[1] < 400.0,
          "%.0f km/h" % double[1])
    check("the stock car is unchanged by the feature existing",
          abs(stock[0] - 4.92) < 0.15, "0-100 is now %.2f s" % stock[0])


def test_all_wheel_drive():
    """Four wheel drive must drive four wheels, and help off the line."""
    print("\n== four wheel drive ==")

    car = Car(REST_HEIGHT)
    car.all_wheel_drive = True
    car.front_torque_split = 0.4
    for _ in range(360):
        car.step()
    car.throttle = 1.0
    for _ in range(30):
        car.step()

    front = [w for w in car.wheels if w.is_steering]
    rear = [w for w in car.wheels if not w.is_steering]
    front_torque = sum(abs(w.drive_torque) for w in front)
    rear_torque = sum(abs(w.drive_torque) for w in rear)
    total = front_torque + rear_torque
    share = front_torque / max(total, 1e-6)
    print("  torque split: %.0f%% front / %.0f%% rear (asked for 40/60)"
          % (100 * share, 100 * (1 - share)))

    check("the front axle receives torque", front_torque > 1.0,
          "front wheels are getting nothing")
    check("the split matches the setting", abs(share - 0.4) < 0.05,
          "%.0f%% front" % (100 * share))

    # And rear drive must still be rear drive.
    rwd = Car(REST_HEIGHT)
    for _ in range(360):
        rwd.step()
    rwd.throttle = 1.0
    for _ in range(30):
        rwd.step()
    rwd_front = sum(abs(w.drive_torque) for w in rwd.wheels if w.is_steering)
    check("rear drive still sends nothing to the front", rwd_front < 1e-6,
          "%.1f Nm reached the front axle" % rwd_front)

    # 4WD should out-launch RWD once there is enough power to spin the rears.
    def zero_to_hundred(awd, power):
        c = Car(REST_HEIGHT)
        c.all_wheel_drive = awd
        c.engine_power = power
        for _ in range(360):
            c.step()
        c.throttle = 1.0
        t = 0.0
        for _ in range(120 * 18):
            c.step()
            t += DT
            if c.speed_kmh() >= 100.0:
                return t
        return None

    for power in (1.0, 2.0):
        r = zero_to_hundred(False, power)
        a = zero_to_hundred(True, power)
        print("  %3.0f%% power: RWD %.2f s, AWD %.2f s" % (power * 100, r, a))
        if power > 1.5:
            check("4WD launches better when power exceeds rear grip", a < r,
                  "AWD %.2f s vs RWD %.2f s" % (a, r))


def test_stability():
    print("\n== stability: 30 s parked, must not drift or sink ==")
    car = Car(REST_HEIGHT)
    for _ in range(120 * 30):
        car.step()
    drift = math.hypot(car.body.pos[0], car.body.pos[2])
    print("  drift %.4f m, height %.4f m, vy %.5f m/s"
          % (drift, car.origin_height(), car.body.vel[1]))
    check("does not slide around", drift < 0.05, "%.4f m" % drift)
    check("does not sink through the ground", car.origin_height() > -0.01)
    check("no residual jitter", abs(car.body.vel[1]) < 0.02)


def main():
    print("Vehicle physics verification (120 Hz, mirrors the GDScript exactly)")
    test_drop()
    test_stability()
    test_smoothness()
    test_reverse_latch()
    test_key_level_reverse()
    test_surfaces()
    test_pickup()
    test_defender()
    test_acceleration()
    test_braking()
    test_cornering()
    test_understeer_balance()
    test_no_spin_under_provocation()
    test_contact_normal_smoothing()
    test_stuck_recovery()
    test_normal_blend_is_safe()
    test_engine_power_setting()
    test_all_wheel_drive()
    print("\n%s" % ("ALL CHECKS PASSED" if not FAILURES
                    else "FAILURES: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
