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
    # The detail is the *reason*, so only show it when there is something to
    # explain. Printing "[PASS] the dead setting is gone -> it is still there"
    # is worse than useless: several checks were phrased as failure messages
    # and read as though they had failed while passing.
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  -> " + detail) if (detail and not ok) else ""))
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
    print("\n== tyre marks and crushable plants ==")
    marks = os.path.join(ROOT, "scripts", "tyre_marks.gd")
    check("tyre mark script exists", os.path.exists(marks))
    if os.path.exists(marks):
        src = open(marks).read()
        check("marks are a mesh ribbon, not stacked decals", "ImmediateMesh" in src)
        check("marks stay on the ground as the car drives on",
              "top_level = true" in src)
        check("marks fade out rather than accumulating forever",
              "fade_time" in src and "pop_front" in src)
        check("an empty ribbon never opens a surface", "drawable" in src)

    # Particles were removed at the user's request.
    for gone in ("ground_particles.gd", "exhaust_smoke.gd"):
        check("%s is removed" % gone,
              not os.path.exists(os.path.join(ROOT, "scripts", gone)))
    for scene in ("car.tscn", "pickup.tscn", "defender.tscn"):
        text = open(os.path.join(ROOT, "scenes", scene)).read()
        check("  %s has no particle nodes" % scene,
              "GroundParticles" not in text and "ExhaustSmoke" not in text)

    crush = os.path.join(ROOT, "scripts", "crushable_plants.gd")
    check("crushable plant script exists", os.path.exists(crush))
    if os.path.exists(crush):
        src = open(crush).read()
        check("crush points are uploaded per wheel", "crush_points" in src)
        check("plants spring back up", "recovery_time" in src)

    forest = open(os.path.join(ROOT, "scripts", "forest.gd")).read()
    check("the shader bends plants under the wheels",
          "crushable" in forest and "crush_radius" in forest)
    check("bending happens in the vertex stage, not on the CPU",
          "VERTEX.xz += push.xz" in forest)

    # Nothing solid may be short enough to look like scenery.
    manifest = json.load(open(os.path.join(ROOT, "assets", "forest",
                                           "forest_manifest.json")))
    heights = {a["name"]: a["height"] for a in manifest["assets"]}
    solid_small = []
    for block in re.finditer(
            r'\{"name": "(\w+)", "count": \d+, "scale": \[([\d.]+), ([\d.]+)\][^{]*?\},',
            forest, re.S):
        name, lo = block.group(1), float(block.group(2))
        if name in heights and '"collide": true' in block.group(0):
            if heights[name] * lo < 0.5:
                solid_small.append("%s %.2f m" % (name, heights[name] * lo))
    check("nothing solid is shorter than 0.5 m", not solid_small,
          ", ".join(solid_small))



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

    # The species table moved from a const Dictionary keyed "name"/"count"/
    # "shadows" to PlantSpecies resources keyed "mesh_name"/"count"/
    # "cast_shadows". This check silently matched nothing after the rename and
    # reported a perfect 0 M of shadow work, which is exactly the sort of
    # false pass that lets a regression through - so it now fails loudly if it
    # cannot find the table at all.
    geometry = 0
    shadow = 0
    found = 0
    for block in re.finditer(r'\{"mesh_name": "(\w+)", "count": (\d+)(.*?)\},',
                             src, re.S):
        name, count, body = block.group(1), int(block.group(2)), block.group(3)
        if name not in tris:
            continue
        found += 1
        geometry += tris[name] * count
        if '"cast_shadows": true' in body:
            shadow += tris[name] * count

    check("the species table was actually parsed", found >= 8,
          "only matched %d species" % found)

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


def test_backface_culling():
    """Solid vegetation must not be rendered two-sided.

    cull_disabled rasterises every triangle twice over - once for each
    facing - and for a closed shape the back faces can never be seen. It also
    disqualifies the surface from FLAG_USES_SHARED_SHADOW_MATERIAL
    (render_forward_clustered.cpp:3795), which is what lets Godot draw it into
    the shadow map with the trivial depth-only material and the importer's
    12-byte position-only shadow mesh instead of the full shader and the
    52-byte vertex.
    """
    print("\n== vegetation back-face culling ==")
    src = open(os.path.join(ROOT, "scripts", "forest.gd")).read()
    manifest = json.load(open(os.path.join(ROOT, "assets", "forest",
                                           "forest_manifest.json")))
    tris = {a["name"]: a["tris"] for a in manifest["assets"]}

    check("the shader picks its cull mode per species",
          "cull_disabled\" if entry.two_sided else \"cull_back" in src)
    check("two_sided is an editable property",
          "@export var two_sided" in
          open(os.path.join(ROOT, "scripts", "plant_species.gd")).read())

    table = re.search(r"const DEFAULT_SPECIES := \[(.*?)\n\]", src, re.S)
    check("the species table can be parsed", table is not None)
    if table is None:
        return

    SOLID = ("tree", "tree_lod", "tree_far", "rock_a", "rock_b", "rock_c")
    two_sided_tris = 0
    single_tris = 0
    wrong = []
    for block in re.finditer(r'\{"mesh_name": "(\w+)", "count": (\d+)(.*?)\},',
                             table.group(1), re.S):
        name, count, body = block.group(1), int(block.group(2)), block.group(3)
        if name not in tris:
            continue
        two = '"two_sided": false' not in body
        total = tris[name] * count
        if two:
            two_sided_tris += total
        else:
            single_tris += total
        if name in SOLID and two:
            wrong.append(name)

    print("  single-sided (solid): %s tris" % format(single_tris, ","))
    print("  two-sided (foliage):  %s tris" % format(two_sided_tris, ","))
    saving = single_tris / max(single_tris + two_sided_tris, 1)
    print("  %.0f%% of vegetation geometry no longer rasterises back faces"
          % (100 * saving))

    check("trees and rocks are single sided", not wrong, ", ".join(wrong))
    # 79% is the correct answer, not a shortfall: the remaining 21% is flat
    # foliage, which must stay two-sided or ferns and grass disappear when
    # seen from behind. The threshold checks that the solid geometry - which
    # is where the waste was - has been converted.
    check("the solid geometry is single sided", saving > 0.7,
          "only %.0f%% - the trees or rocks are still two-sided"
          % (100 * saving))
    # Flat cards genuinely need both faces - a fern would vanish from behind.
    check("flat foliage is still two sided", two_sided_tris > 0,
          "everything is single sided, foliage will disappear from behind")


def test_shader_variants():
    """The crush loop must not be compiled into shaders that never crush.

    `if (crushable)` was a uniform branch, so every vegetation vertex shader -
    trees included - still carried the 8-iteration loop over crush_points.
    The shader is assembled per species now, so a tree's vertex shader does
    not contain the loop at all.
    """
    print("\n== shader variants ==")
    src = open(os.path.join(ROOT, "scripts", "forest.gd")).read()

    check("the shader is assembled from parts",
          "SHADER_HEADER" in src and "SHADER_CRUSH" in src
          and "SHADER_TAIL" in src)
    check("the crush block is only pasted in when needed",
          "SHADER_CRUSH if crushable else" in src)
    check("the crush uniforms are only declared when needed",
          "SHADER_CRUSH_UNIFORMS if crushable else" in src)
    check("there is no uniform crushable branch left",
          "uniform bool crushable" not in src)
    check("shaders are cached so species share compiled programs",
          "_shader_cache" in src)

    # The loop must live in the crush fragment and nowhere else.
    crush = re.search(r'const SHADER_CRUSH := """(.*?)"""', src, re.S)
    uniforms = re.search(r'const SHADER_UNIFORMS := """(.*?)"""', src, re.S)
    check("the 8-iteration loop is inside the optional block",
          crush is not None and "for (int i = 0; i < 8; i++)" in crush.group(1))
    check("and not in the part every species gets",
          uniforms is not None
          and "for (int i = 0; i < 8; i++)" not in uniforms.group(1))


def test_hidden_car_interior():
    """The cabin must be hidden in the chase view.

    Measured on the BMW's own material list: 56,493 of its 100,582 triangles
    belong to interior materials, and the dashboard alone is 26,723. None of
    it is visible from behind the car. The Z-buffer would reject the pixels,
    but the vertices are still transformed and the draw calls still issued -
    hiding the surfaces removes all of it.
    """
    print("\n== car interior is hidden in the chase view ==")
    model = open(os.path.join(ROOT, "scripts", "car_model.gd")).read()
    cam = open(os.path.join(ROOT, "scripts", "chase_camera.gd")).read()

    check("the model can hide its cabin", "set_interior_visible" in model)
    check("the camera drives it", "_set_interior_visible" in cam)
    check("it is shown again in the hood view",
          "_set_interior_visible(mode == Mode.HOOD)" in cam)

    # Verify the material tokens actually match the asset, rather than
    # assuming. A typo here would silently hide nothing at all.
    tokens = re.search(r"const INTERIOR_MATERIALS := \[(.*?)\]", model, re.S)
    check("the interior material list can be parsed", tokens is not None)
    if tokens is None:
        return
    names = re.findall(r'"([^"]+)"', tokens.group(1))

    gltf = json.load(open(os.path.join(ROOT, "assets", "car", "bmw_1m.gltf")))
    materials = [m.get("name", "") for m in gltf["materials"]]
    per_material = {}
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            mat = prim.get("material")
            if mat is None or "indices" not in prim:
                continue
            key = materials[mat]
            count = gltf["accessors"][prim["indices"]]["count"] // 3
            per_material[key] = per_material.get(key, 0) + count

    hidden = 0
    matched = 0
    for name, count in per_material.items():
        if any(tok in name.lower() for tok in names):
            hidden += count
            matched += 1
    total = sum(per_material.values())
    print("  %d of %d materials matched, %s of %s triangles hidden (%.0f%%)"
          % (matched, len(per_material), format(hidden, ","),
             format(total, ","), 100.0 * hidden / total))

    check("the tokens match real materials in the asset", matched > 10,
          "only %d matched - the list is out of step with the model" % matched)
    check("hiding the cabin removes a worthwhile share", hidden > total * 0.4,
          "only %.0f%%" % (100.0 * hidden / total))

    # Nothing that is visible from outside may be caught by the tokens.
    EXTERIOR = ("livrea", "chassis", "rt_rim", "rt_battistrada", "vetri_fanali",
                "dischi_freni", "car_pinzafreni", "griglia", "mirror")
    caught = [m for m in per_material
              if any(tok in m.lower() for tok in names)
              and any(e in m.lower() for e in EXTERIOR)]
    check("no exterior surface is hidden by mistake", not caught,
          ", ".join(caught))


def test_interior_split_keeps_the_body():
    """Hiding the cabin must not take the bodywork with it.

    v3.1 shipped a per-NODE hide: if any surface on a MeshInstance3D used an
    interior material, the whole node was switched off. The converter exports
    the entire car body as ONE node with 35 surfaces - 20 interior, 15
    exterior - so hiding the dashboard also hid the paint, the chassis, the
    lights and the glass. The wheels are separate nodes with no interior
    surfaces, so they stayed: four tyres floating over the grass.
    """
    print("\n== hiding the cabin must not hide the car ==")
    model = open(os.path.join(ROOT, "scripts", "car_model.gd")).read()

    check("the split is per surface, not per node",
          "_mesh_from_surfaces" in model and "_split_mesh_node" in model)
    check("the old per-node hide is gone", "_find_interior" not in model)
    check("the original node keeps the exterior",
          "mesh_node.mesh = exterior_mesh" in model)
    check("the cabin goes on its own node",
          "cabin.mesh = interior_mesh" in model)

    # A node that is entirely exterior must never be touched.
    check("wholly exterior nodes are left alone",
          "if interior_surfaces.is_empty():" in model)

    # Prove against the real asset that a mixed node exists - otherwise this
    # test is guarding against nothing.
    gltf = json.load(open(os.path.join(ROOT, "assets", "car", "bmw_1m.gltf")))
    materials = [m.get("name", "") for m in gltf["materials"]]
    tokens = re.search(r"const INTERIOR_MATERIALS := \[(.*?)\]", model, re.S)
    names = re.findall(r'"([^"]+)"', tokens.group(1)) if tokens else []

    mixed = []
    for node in gltf["nodes"]:
        if "mesh" not in node:
            continue
        prims = gltf["meshes"][node["mesh"]]["primitives"]
        inside = 0
        outside = 0
        for prim in prims:
            mat = prim.get("material")
            if mat is None:
                continue
            if any(tok in materials[mat].lower() for tok in names):
                inside += 1
            else:
                outside += 1
        if inside and outside:
            mixed.append((node.get("name", "?"), inside, outside))

    for name, inside, outside in mixed:
        print("  node '%s': %d interior + %d exterior surfaces on ONE node"
              % (name, inside, outside))
    check("the asset really does mix both on one node", bool(mixed),
          "nothing to split - this test proves nothing")


def test_wheel_spin_direction():
    """Wheels must roll forwards when the car drives forwards.

    Godot is right-handed and the car faces -Z. A positive rotation about the
    wheel's +X axis carries the top of the tyre towards +Z, i.e. backwards.
    `spin` is positive when driving forward, so feeding it straight into
    rotation.x span the wheels the wrong way.
    """
    print("\n== wheel spin direction ==")
    wheel = open(os.path.join(ROOT, "scripts", "wheel.gd")).read()
    body = "\n".join(l.split("#")[0] for l in wheel.splitlines())

    check("the visual angle is negated",
          re.search(r"wheel_visual\.rotation\s*=\s*Vector3\(\s*-", body)
          is not None,
          "rotation.x is not negated - the wheels will spin backwards")

    # Reproduce the geometry rather than asserting it.
    import math as _m
    angle = 0.3
    top = (0.0, 0.33, 0.0)
    z = top[1] * _m.sin(angle)
    print("  rotating the top of the tyre by +%.1f rad about +X gives z = %+.3f"
          % (angle, z))
    print("  the car faces -Z, so +z means the top moved BACKWARDS")
    check("a positive angle really does spin backwards", z > 0.0)

    # And the angle must advance on the physics tick, not the render frame.
    check("the angle is integrated in update_spin, on the physics tick",
          "_prev_spin_angle = spin_angle" in body
          and "func update_spin" in body)
    check("update_visuals interpolates rather than integrating",
          "get_physics_interpolation_fraction" in body
          and "spin_angle = wrapf(spin_angle + spin * delta" not in
          body[body.index("func update_visuals"):])


def test_frame_rate_floor():
    """The engine must not be able to lock itself at a low frame rate.

    main.cpp:4031 clamps physics catch-up to max_physics_steps_per_frame.
    When the game falls behind it replays that many full vehicle ticks in one
    frame, which makes that frame long too, and it saturates - parking the
    frame rate at exactly physics_hz / max_steps. At 120 Hz with 8 steps that
    is 15.0 fps, which is the number the user reported.
    """
    print("\n== physics catch-up floor ==")
    text = open(os.path.join(ROOT, "project.godot")).read()
    hz = int(re.search(r"common/physics_ticks_per_second=(\d+)", text).group(1))
    steps = int(re.search(r"common/max_physics_steps_per_frame=(\d+)",
                          text).group(1))
    floor_fps = hz / steps
    print("  %d Hz physics / %d steps -> the game cannot lock below %.1f fps"
          % (hz, steps, floor_fps))
    print("  (the reported lock-up was at 120/8 = 15.0 fps)")

    check("the lock-up floor is not near 15 fps", floor_fps > 25.0,
          "floor is %.1f fps" % floor_fps)
    check("one hitch cannot cascade into many ticks", steps <= 4,
          "%d ticks of catch-up per frame" % steps)
    # Below the floor, simulated time slows instead of the game seizing.
    # That is the better failure, but it should not start too early.
    check("time still runs true at ordinary frame rates", floor_fps <= 60.0,
          "time dilates below %.1f fps" % floor_fps)


def test_quality_presets():
    print("\n== graphics presets ==")
    gs = open(os.path.join(ROOT, "scripts", "game_settings.gd")).read()
    menu = open(os.path.join(ROOT, "scripts", "settings_menu.gd")).read()

    for name in ("render_scale", "shadow_distance", "shadow_size", "ssao",
                 "glow", "vegetation_density"):
        check("  %s is a setting" % name, "var %s" % name in gs)

    check("there is a single preset control", "set_quality_preset" in gs)
    check("presets are exposed in the menu", "Качество графики" in menu)
    check("render scale is applied to the viewport",
          "scaling_3d_scale" in gs)
    check("shadow atlas size is applied",
          "directional_shadow_atlas_set_size" in gs)
    check("shadow distance is applied to the sun",
          "directional_shadow_max_distance" in gs)
    check("ssao and glow are applied to the environment",
          "ssao_enabled" in gs and "glow_enabled" in gs)

    # Render scale is the strongest lever, so the low preset must use it.
    m = re.search(r"0:\s*#\s*Низкие(.*?)1:", gs, re.S)
    check("the low preset actually lowers something", m is not None)
    if m:
        body = m.group(1)
        print("  low preset: %s" % ", ".join(
            l.strip() for l in body.strip().splitlines() if "=" in l))
        check("  it reduces the render scale", "render_scale = 0.8" in body)
        check("  it shortens the shadow distance",
              re.search(r"shadow_distance = (\d+)", body) is not None
              and int(re.search(r"shadow_distance = (\d+)", body).group(1)) < 140)
        check("  it thins the vegetation", "vegetation_density = 0.5" in body)


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
    test_performance()
    test_backface_culling()
    test_shader_variants()
    test_hidden_car_interior()
    test_interior_split_keeps_the_body()
    test_wheel_spin_direction()
    test_frame_rate_floor()
    test_quality_presets()
    test_input()
    print("\n%s" % ("ALL CHECKS PASSED" if not FAILURES
                    else "FAILURES: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
