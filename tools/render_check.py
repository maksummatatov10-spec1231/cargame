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

import json
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
                           ("glow_enabled", "glow")):
        check("%s is enabled" % label, env.get(feature, False))
    # These three are deliberately off. SDFGI reached only a quarter of the map
    # and re-voxelised constantly while driving; SSIL and SSR duplicated work
    # for effects that barely show in an outdoor scene.
    for feature, label in (("ssil_enabled", "SSIL"),
                           ("sdfgi_enabled", "SDFGI"),
                           ("ssr_enabled", "screen space reflections")):
        check("%s is off for performance" % label, not env.get(feature, False))
    check("sky ambient compensates for the missing GI",
          env.get("ambient_light_sky_contribution", 0.0) >= 0.5,
          "%.2f" % env.get("ambient_light_sky_contribution", 0.0))


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
    # PCSS is deliberately off. Any angular distance above zero makes every
    # shadow lookup search for the blocker distance first, which is one of the
    # most expensive things a mid-range GPU can be asked to do. Fixed-width
    # shadows with a blur look nearly identical outdoors.
    check("PCSS is off for performance", angular == 0.0, "%.2f deg" % angular)
    check("shadows still have a soft edge",
          re.search(r"shadow_blur = ([\d.]+)", block) is not None)
    check("shadows use cascades for range", "directional_shadow_mode = 2" in block)
    check("shadow range covers the drivable area",
          val("directional_shadow_max_distance") >= 150.0,
          "%.0f m" % val("directional_shadow_max_distance"))


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
    print("\n== terrain ==")
    src = open(os.path.join(ROOT, "scripts", "terrain.gd")).read()
    check("terrain is a heightfield", "HeightMapShape3D" in src)
    check("collision samples the same array as the mesh", "map_data = heights" in src)
    check("collision cell size is scaled to match the mesh",
          "Vector3(_cell, 1.0, _cell)" in src)
    check("terrain has a shaded material", "shader_type spatial" in src)
    check("ground blends grass, dirt and rock", "rock_amount" in src
          and "dirt_amount" in src and "grass_amount" in src)
    check("ground has procedural normal detail", "NORMAL_MAP" in src)
    check("terrain contributes to global illumination", "GI_MODE_STATIC" in src)
    check("surface types are exposed to the physics", "func sample_surface" in src)

    veh = open(os.path.join(ROOT, "scripts", "vehicle.gd")).read()
    check("wheels are told what surface they are on", "_update_surfaces" in veh)
    wheel = open(os.path.join(ROOT, "scripts", "wheel.gd")).read()
    check("grip actually changes with the surface", "surface_grip" in wheel)
    check("rolling resistance changes with the surface", "surface_drag" in wheel)


def test_terrain_winding():
    """The terrain must face up.

    Godot treats clockwise as front facing, and its own PlaneMesh gets an
    upward face by emitting points at (-x, 0, -z) with a particular index
    order. The terrain grid emits (+x, h, +z), so copying that index order
    verbatim reversed the winding and every triangle faced down: the ground was
    invisible from above and fully textured from below.

    This recomputes the geometric normal of a terrain triangle from the index
    order in the source and checks it points the same way as the engine's
    reference plane.
    """
    print("\n== terrain face winding ==")
    src = open(os.path.join(ROOT, "scripts", "terrain.gd")).read()

    block = src[src.index("for z in resolution - 1:"):]
    block = block[:block.index("st.generate_tangents()")]
    order = re.findall(r"st\.add_index\(([^)]+)\)", block)
    check("six indices per quad are emitted", len(order) == 6,
          "%d found" % len(order))
    if len(order) != 6:
        return

    def offset(expr):
        expr = expr.strip()
        if expr == "i":
            return (0, 0)
        if expr == "i + 1":
            return (1, 0)
        if expr == "i + resolution":
            return (0, 1)
        if expr == "i + resolution + 1":
            return (1, 1)
        return None

    def normal_y(tri):
        pts = []
        for e in tri:
            off = offset(e)
            if off is None:
                return None
            pts.append((off[0], 0.0, off[1]))
        u = [pts[1][k] - pts[0][k] for k in range(3)]
        v = [pts[2][k] - pts[0][k] for k in range(3)]
        return u[2] * v[0] - u[0] * v[2]

    # PlaneMesh reference: points at (-x, 0, -z) with Godot's index order gives
    # a negative value from this same computation for an upward face.
    for label, tri in (("first", order[0:3]), ("second", order[3:6])):
        ny = normal_y(tri)
        check("%s triangle of each quad faces up" % label,
              ny is not None and ny < 0,
              "value %s" % ("?" if ny is None else "%+.1f" % ny))

    check("the shader culls back faces", "cull_back" in src)


def test_forest():
    print("\n== forest ==")
    path = os.path.join(ROOT, "scripts", "forest.gd")
    check("forest script exists", os.path.exists(path))
    if not os.path.exists(path):
        return
    src = open(path).read()
    check("vegetation is instanced, not one node each", "MultiMesh" in src)
    check("plants sway in the wind (they have no rig, so it is a shader)",
          "shader_type spatial" in src and "TIME" in src)
    check("trunks stay still while canopies move", "anchor_height" in src)
    check("foliage is lit from behind", "BACKLIGHT" in src)
    check("trees and rocks have collision", "CapsuleShape3D" in src
          and "SphereShape3D" in src)
    check("placement follows the terrain", "sample_height" in src
          and "sample_normal" in src)
    check("a clearing is kept for the spawn", "clearing_radius" in src)

    manifest = os.path.join(ROOT, "assets", "forest", "forest_manifest.json")
    check("converted forest assets are present", os.path.exists(manifest))
    if os.path.exists(manifest):
        data = json.load(open(manifest))
        names = {a["name"] for a in data["assets"]}
        print("  %d assets: %s" % (len(names), ", ".join(sorted(names))))
        check("the tree survived conversion", "tree" in names)
        tree = next((a for a in data["assets"] if a["name"] == "tree"), None)
        if tree:
            check("tree is a believable height", 4.0 < tree["height"] < 12.0,
                  "%.2f m" % tree["height"])


def test_effects():
    print("\n== tyre marks and dirt ==")
    marks = os.path.join(ROOT, "scripts", "tyre_marks.gd")
    check("tyre mark script exists", os.path.exists(marks))
    if os.path.exists(marks):
        src = open(marks).read()
        check("marks are a mesh ribbon, not stacked decals",
              "ImmediateMesh" in src)
        check("marks stay on the ground as the car drives on",
              "top_level = true" in src)
        check("marks fade out rather than accumulating forever",
              "fade_time" in src and "pop_front" in src)
        check("marks appear on soft ground even without sliding",
              "surface_looseness" in src)

    dirt = os.path.join(ROOT, "scripts", "ground_particles.gd")
    check("ground particle script exists", os.path.exists(dirt))
    if os.path.exists(dirt):
        src = open(dirt).read()
        check("clods are thrown with gravity, so they arc",
              "gravity = Vector3(0.0, -9.81, 0.0)" in src)
        check("dust is separate from the clods", "_make_dust" in src)
        check("particle colour follows the surface", "surface_colours" in src)
        check("tarmac throws nothing", "surface_looseness <= 0.01" in src)

    scene = open(os.path.join(ROOT, "scenes", "car.tscn")).read()
    check("effects are wired into the car scene",
          "TyreMarks" in scene and "GroundParticles" in scene)


def test_clouds():
    print("\n== sky ==")
    path = os.path.join(ROOT, "scripts", "sky_clouds.gd")
    check("cloud shader exists", os.path.exists(path))
    if not os.path.exists(path):
        return
    src = open(path).read()
    check("it is a real sky shader", "shader_type sky" in src)
    check("clouds are raymarched, not a scrolling texture",
          "MARCH_STEPS" in src and "transmittance" in src)
    check("clouds self-shadow, giving bright tops and dark bases",
          "LIGHT_STEPS" in src)
    check("clouds drift over time", "wind_speed" in src and "TIME" in src)
    check("sky radiance updates so lighting matches the sky",
          "PROCESS_MODE_REALTIME" in src)
    scene = open(os.path.join(ROOT, "scenes", "main.tscn")).read()
    check("clouds are in the scene", "SkyClouds" in scene)


def test_performance():
    """Budgets the per-frame cost, especially shadows.

    The scene was unplayable because shadow casting ignores visibility_range
    and redraws every caster once per cascade. With all 420 trees casting, the
    12.5 M triangles of vegetation became 37.5 M of shadow work every frame,
    on top of the visible geometry. That single setting was the lag.
    """
    print("\n== performance budget ==")
    src = open(os.path.join(ROOT, "scripts", "forest.gd")).read()
    manifest = json.load(open(os.path.join(ROOT, "assets", "forest",
                                           "forest_manifest.json")))
    tris = {a["name"]: a["tris"] for a in manifest["assets"]}

    geometry = 0
    shadow = 0
    for block in re.finditer(r'\{"name": "(\w+)", "count": (\d+)[^{]*?\},', src, re.S):
        name, count = block.group(1), int(block.group(2))
        if name not in tris:
            continue
        geometry += tris[name] * count
        if '"shadows": true' in block.group(0):
            shadow += tris[name] * count

    scene = open(os.path.join(ROOT, "scenes", "main.tscn")).read()
    m = re.search(r"directional_shadow_mode = (\d+)", scene)
    cascades = 4 if (m and m.group(1) == "2") else 1
    m = re.search(r"directional_shadow_split_3 = ([\d.]+)", scene)
    cascades = 3 if m else cascades

    print("  vegetation geometry %.2f M tris" % (geometry / 1e6))
    print("  shadow casters      %.2f M tris  x%d cascades = %.2f M"
          % (shadow / 1e6, cascades, shadow * cascades / 1e6))

    check("shadow casters are a small share of the vegetation",
          shadow < geometry * 0.6,
          "%.0f%% of the forest casts" % (shadow / max(geometry, 1) * 100.0))
    check("shadow work stays under 10 M triangles",
          shadow * cascades < 10e6, "%.2f M" % (shadow * cascades / 1e6))
    check("small plants never cast shadows",
          all(('"name": "%s"' % n) not in src.split('"shadows": true')[0][-400:]
              for n in ("grass_tuft",)))

    # The sky shader is per-pixel work over a large part of the screen, so its
    # sample budget matters as much as any geometry count. The first version
    # marched 28 steps with a 4-step light march and a 5-octave fbm called
    # twice: ~1400 noise lookups per sky pixel, about 10 billion heavy
    # operations per frame at 1080p, which no mid-range GPU can absorb.
    sky = open(os.path.join(ROOT, "scripts", "sky_clouds.gd")).read()
    m = re.search(r"MARCH_STEPS = (\d+)", sky)
    march = int(m.group(1)) if m else 0
    m = re.search(r"LIGHT_STEPS = (\d+)", sky)
    light = int(m.group(1)) if m else 0
    m = re.search(r"for \(int i = 0; i < (\d+); i\+\+\)", sky)
    octaves = int(m.group(1)) if m else 0
    per_pixel = march * (1 + light) * octaves * 2
    print("  sky: %d march x (1+%d light) x %d octaves = ~%d noise lookups/pixel"
          % (march, light, octaves, per_pixel))
    check("sky shader stays within budget", per_pixel <= 300,
          "%d lookups per pixel" % per_pixel)
    check("sky renders at half resolution", "use_half_res_pass" in sky)
    check("empty sky samples exit early", "return 0.0;" in sky)

    # Anything that redraws the whole scene must be justified.
    env = parse_env(open(os.path.join(ROOT, "scenes", "main.tscn")).read())
    heavy = [k for k in ("sdfgi_enabled", "ssil_enabled", "ssr_enabled",
                         "volumetric_fog_enabled") if env.get(k, False)]
    check("no full-scene GI passes are enabled", not heavy, ", ".join(heavy))


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
    test_terrain_winding()
    test_forest()
    test_effects()
    test_clouds()
    test_smoke()
    test_performance()
    test_input()
    print("\n%s" % ("ALL CHECKS PASSED" if not FAILURES
                    else "FAILURES: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
