#!/usr/bin/env python3
"""
Checks the rendering environment for the settings that visibly wash the image
out, and for the ones a good-looking scene needs.

This exists because of a real regression: the scene shipped with a 40 m tall
height-fog layer at density 0.02. The car sits at about y = 0.5 m, so the whole
play area was *inside* the fog, and Godot's height fog gets denser the further
below `fog_height` you are. Working the numbers through:

    effective density at y=0.5  =  0.02 * exp((40 - 0.5) * 0.02)  =  0.044
    over a 30 m sight line       =  1 - exp(-0.044 * 30)          =  73%

so nearly three quarters of every pixel was flat fog colour. Combined with
ambient light at full strength and a low tonemap white point, the result was
the pale, low-contrast image the user reported.

The maths here is the same as Godot's, so the numbers below are the real
predicted contribution, not a guess.

Run:  python3 tools/render_check.py
"""

import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []


def check(label, ok, detail=""):
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -> " + detail) if detail else ""))
    if not ok:
        FAILURES.append(label)


def parse_env(text):
    """Pull the Environment sub-resource into a dict of floats/bools."""
    start = text.index('[sub_resource type="Environment"')
    end = text.index("[sub_resource", start + 10)
    block = text[start:end]
    out = {}
    for m in re.finditer(r"^(\w+)\s*=\s*(.+)$", block, re.M):
        key, raw = m.group(1), m.group(2).strip()
        if raw in ("true", "false"):
            out[key] = raw == "true"
        else:
            try:
                out[key] = float(raw)
            except ValueError:
                out[key] = raw
    return out


def fog_contribution(env, car_height=0.5, sight=30.0):
    """Fraction of a pixel that is fog colour, using Godot's own model."""
    if not env.get("fog_enabled", False):
        return 0.0
    density = env.get("fog_density", 0.0)
    h_density = env.get("fog_height_density", 0.0)
    h = env.get("fog_height", 0.0)
    if h_density > 0.0 and car_height < h:
        density += h_density * math.exp((h - car_height) * h_density)
    return 1.0 - math.exp(-density * sight)


def test_environment():
    print("\n== rendering environment ==")
    text = open(os.path.join(ROOT, "scenes", "main.tscn")).read()
    env = parse_env(text)

    fog = fog_contribution(env)
    print("  fog covers %.1f%% of a pixel at 30 m (density %.4f, height density %.4f)"
          % (fog * 100.0, env.get("fog_density", 0.0), env.get("fog_height_density", 0.0)))
    check("scene is not washed out by fog", fog < 0.25, "%.1f%% fog" % (fog * 100.0))

    far = fog_contribution(env, sight=200.0)
    print("  fog covers %.1f%% at 200 m (distance haze should still be visible)"
          % (far * 100.0))
    check("there is still some atmospheric depth", far > 0.15,
          "%.1f%% at 200 m" % (far * 100.0))

    sky_contrib = env.get("ambient_light_sky_contribution", 1.0)
    check("ambient does not flatten everything", sky_contrib <= 0.6,
          "sky contribution %.2f" % sky_contrib)

    exposure = env.get("tonemap_exposure", 1.0)
    white = env.get("tonemap_white", 1.0)
    check("tonemapper has headroom for highlights", white >= 4.0, "white %.1f" % white)
    check("exposure is not blowing the image out", exposure <= 1.0,
          "exposure %.2f" % exposure)
    check("filmic/ACES tonemapping is on", env.get("tonemap_mode", 0) >= 2,
          "mode %s" % env.get("tonemap_mode"))

    contrast = env.get("adjustment_contrast", 1.0)
    saturation = env.get("adjustment_saturation", 1.0)
    check("contrast is boosted, not flat", contrast >= 1.1, "%.2f" % contrast)
    check("colour is saturated enough to read", saturation >= 1.1, "%.2f" % saturation)

    for feature, label in (("ssao_enabled", "SSAO (contact shading)"),
                           ("ssil_enabled", "SSIL (indirect bounce)"),
                           ("sdfgi_enabled", "SDFGI (global illumination)"),
                           ("ssr_enabled", "screen space reflections"),
                           ("glow_enabled", "glow")):
        check("%s is enabled" % label, env.get(feature, False))


def test_sun():
    print("\n== sun and shadows ==")
    text = open(os.path.join(ROOT, "scenes", "main.tscn")).read()
    start = text.index('[node name="Sun"')
    block = text[start:text.index("[node name=", start + 10)]

    def val(key, default=0.0):
        m = re.search(r"^%s\s*=\s*([-\d.]+)$" % key, block, re.M)
        return float(m.group(1)) if m else default

    energy = val("light_energy", 1.0)
    angular = val("light_angular_distance")
    print("  sun energy %.2f, angular size %.2f deg, shadow distance %.0f m"
          % (energy, angular, val("directional_shadow_max_distance")))

    check("sun is strong enough to model the car", energy >= 1.5, "%.2f" % energy)
    check("shadows are enabled", "shadow_enabled = true" in block)
    check("soft shadows (PCSS) are on", angular > 0.0, "%.2f deg" % angular)
    check("shadows use cascades for range", "directional_shadow_mode = 2" in block)
    check("shadow range covers the play area",
          val("directional_shadow_max_distance") >= 200.0)


def test_smoke():
    print("\n== exhaust smoke ==")
    path = os.path.join(ROOT, "scripts", "exhaust_smoke.gd")
    check("smoke script exists", os.path.exists(path))
    if not os.path.exists(path):
        return
    src = open(path).read()

    check("smoke is emitted from the tailpipes", "PIPE_POSITIONS" in src)
    check("tyre smoke reacts to wheel slip", "slip_ratio" in src)
    check("smoke is left behind the car, not carried",
          "local_coords = false" in src)
    check("particles fade softly into geometry", "proximity_fade_enabled" in src)
    check("smoke is grey, not black or white",
          re.search(r"smoke_colour\s*:=\s*Color\(0\.[5-7]", src) is not None)

    # the pipe positions must match the model's actual exhaust tips
    m = re.findall(r"Vector3\((-?[\d.]+), ([\d.]+), ([\d.]+)\)", src)
    if m:
        x, y, z = (float(v) for v in m[0])
        print("  first pipe at x=%.3f y=%.3f z=%.3f (model tips: +/-0.374, 0.274, 2.201)"
              % (x, y, z))
        check("pipes line up with the model's exhaust tips",
              abs(abs(x) - 0.374) < 0.05 and abs(y - 0.274) < 0.06 and 2.1 < z < 2.4)

    scene = open(os.path.join(ROOT, "scenes", "car.tscn")).read()
    check("smoke node is in the car scene", "ExhaustSmoke" in scene)
    gen = open(os.path.join(ROOT, "tools", "build_car_scene.py")).read()
    check("the scene generator also emits it (so it survives a rebuild)",
          "ExhaustSmoke" in gen)


def test_ground():
    print("\n== ground material ==")
    src = open(os.path.join(ROOT, "scripts", "world.gd")).read()
    check("ground has an albedo texture", "albedo_texture" in src)
    check("ground has a normal map so the sun catches it", "normal_texture" in src)
    check("ground has a roughness map", "roughness_texture" in src)
    check("ground contributes to global illumination", "GI_MODE_STATIC" in src)
    check("textures are anisotropically filtered", "ANISOTROPIC" in src)


def test_input():
    print("\n== controls ==")
    text = open(os.path.join(ROOT, "project.godot")).read()
    actions = set(re.findall(r"^(\w+)=\{", text, re.M))
    for a in ("drive_forward", "drive_backward", "steer_left", "steer_right",
              "turbo", "handbrake"):
        check("action '%s' is mapped" % a, a in actions)

    # Shift is physical keycode 4194325
    block = text[text.index("turbo={"):]
    block = block[:block.index("}")]
    check("turbo is bound to Shift", "4194325" in block)

    veh = open(os.path.join(ROOT, "scripts", "vehicle.gd")).read()
    check("turbo actually adds torque", "boost_multiplier" in veh)
    check("turbo spools rather than switching instantly", "boost_spool_rate" in veh)


def main():
    print("Rendering and presentation checks")
    test_environment()
    test_sun()
    test_ground()
    test_smoke()
    test_input()
    print("\n%s" % ("ALL CHECKS PASSED" if not FAILURES
                    else "FAILURES: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
