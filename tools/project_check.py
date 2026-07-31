#!/usr/bin/env python3
"""
Static validation of the Godot project.

Godot is not always available on a build machine, so this walks the project the
way the engine would and checks the things that break silently at load time:

  * every ExtResource path in a .tscn exists on disk
  * every SubResource referenced by a node is declared in the same file
  * load_steps is large enough for the resources in the file
  * the glTF is valid: buffer lengths, accessor ranges, texture URIs
  * project.godot points at a scene that exists and defines every input action
    the scripts actually use
  * the scripts only reference node paths that exist in the scenes

Run:  python3 tools/project_check.py
"""

import json
import os
import re
import math
import struct
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


def res_path(p):
    return os.path.join(ROOT, p.replace("res://", ""))


# --------------------------------------------------------------------------- #

def check_scenes():
    print("\n== scene files ==")
    for name in sorted(os.listdir(os.path.join(ROOT, "scenes"))):
        if not name.endswith(".tscn"):
            continue
        path = os.path.join(ROOT, "scenes", name)
        text = open(path).read()
        print("  %s" % name)

        missing = []
        for m in re.finditer(r'\[ext_resource [^\]]*path="([^"]+)"', text):
            if not os.path.exists(res_path(m.group(1))):
                missing.append(m.group(1))
        check("    all ext_resource paths exist", not missing, ", ".join(missing))

        declared = set(re.findall(r'\[sub_resource [^\]]*id="([^"]+)"', text))
        used = set(re.findall(r'SubResource\("([^"]+)"\)', text))
        check("    all SubResource ids are declared", used <= declared,
              ", ".join(sorted(used - declared)))

        ext_ids = set(re.findall(r'\[ext_resource [^\]]*id="([^"]+)"', text))
        ext_used = set(re.findall(r'ExtResource\("([^"]+)"\)', text))
        check("    all ExtResource ids are declared", ext_used <= ext_ids,
              ", ".join(sorted(ext_used - ext_ids)))

        m = re.search(r"load_steps=(\d+)", text)
        needed = len(declared) + len(ext_ids) + 1
        if m:
            check("    load_steps is big enough", int(m.group(1)) >= needed,
                  "declared %s, needs >= %d" % (m.group(1), needed))

        # every node's parent must be a node that appears earlier in the file
        names = []
        ok_parents = True
        for m in re.finditer(r'\[node name="([^"]+)"(?: type="[^"]+")?'
                             r'(?: parent="([^"]+)")?', text):
            node, parent = m.group(1), m.group(2)
            if parent and parent != ".":
                if parent not in names:
                    ok_parents = False
            names.append((parent + "/" + node).lstrip("./") if parent and parent != "."
                         else node)
        check("    node parents resolve", ok_parents)


def check_gltf():
    print("\n== converted model ==")
    base = os.path.join(ROOT, "assets", "car")
    gltf = json.load(open(os.path.join(base, "bmw_1m.gltf")))
    buf = open(os.path.join(base, "bmw_1m.bin"), "rb").read()

    check("buffer length matches the declared size",
          gltf["buffers"][0]["byteLength"] == len(buf),
          "%d vs %d" % (gltf["buffers"][0]["byteLength"], len(buf)))

    bad = []
    for i, bv in enumerate(gltf["bufferViews"]):
        end = bv.get("byteOffset", 0) + bv["byteLength"]
        if end > len(buf):
            bad.append(i)
    check("every bufferView is inside the buffer", not bad, str(bad))

    comp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    size = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
    bad = []
    for i, a in enumerate(gltf["accessors"]):
        bv = gltf["bufferViews"][a["bufferView"]]
        need = a["count"] * comp[a["type"]] * size[a["componentType"]]
        if need > bv["byteLength"]:
            bad.append(i)
    check("every accessor fits its bufferView", not bad, str(bad))

    # indices must address real vertices
    bad = []
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            pos = gltf["accessors"][prim["attributes"]["POSITION"]]
            idx = gltf["accessors"][prim["indices"]]
            bv = gltf["bufferViews"][idx["bufferView"]]
            fmt = "H" if idx["componentType"] == 5123 else "I"
            vals = struct.unpack_from("<%d%s" % (idx["count"], fmt), buf,
                                      bv.get("byteOffset", 0))
            if max(vals) >= pos["count"]:
                bad.append(mesh["name"])
            if idx["count"] % 3 != 0:
                bad.append(mesh["name"] + " (not triangles)")
    check("all indices are in range and triangulated", not bad, str(set(bad)))

    missing = [im["uri"] for im in gltf.get("images", [])
               if not os.path.exists(os.path.join(base, im["uri"]))]
    check("every texture file is present", not missing, ", ".join(missing))

    mats = len(gltf["materials"])
    tris = sum(gltf["accessors"][p["indices"]]["count"] // 3
               for m in gltf["meshes"] for p in m["primitives"])
    verts = sum(gltf["accessors"][p["attributes"]["POSITION"]]["count"]
                for m in gltf["meshes"] for p in m["primitives"])
    print("  %d nodes, %d materials, %d textures, %d triangles, %d vertices"
          % (len(gltf["nodes"]), mats, len(gltf.get("textures", [])), tris, verts))
    check("triangle count is reasonable for a game car", 20000 < tris < 400000, str(tris))

    names = {n.get("name") for n in gltf["nodes"]}
    required = {"body", "steering"} | {p + c for p in ("wheel_", "hub_")
                                       for c in ("lf", "rf", "lr", "rr")}
    check("all the parts the game animates are present", required <= names,
          ", ".join(sorted(required - names)))


def check_all_assets():
    """Every glTF in the project must be clean geometry.

    Godot logs "Ignoring face with non-finite normal in LOD generation" for
    zero-area faces and zero-length normals. Clustering during decimation can
    create both - a thin panel folding back on itself averages its normals to
    zero - so every asset is verified rather than just the one that was noticed.
    """
    print("\n== asset geometry ==")
    import glob
    problems = []
    checked = 0
    for gp in sorted(glob.glob(os.path.join(ROOT, "assets", "*", "*.gltf"))):
        gltf = json.load(open(gp))
        buf_path = os.path.join(os.path.dirname(gp), gltf["buffers"][0]["uri"])
        if not os.path.exists(buf_path):
            problems.append("%s: missing buffer" % os.path.basename(gp))
            continue
        buf = open(buf_path, "rb").read()
        checked += 1

        def acc(i):
            a = gltf["accessors"][i]
            bv = gltf["bufferViews"][a["bufferView"]]
            comp = {"VEC3": 3, "VEC2": 2, "SCALAR": 1}[a["type"]]
            fmt = {5126: "f", 5123: "H", 5125: "I"}[a["componentType"]]
            return struct.unpack_from("<%d%s" % (a["count"] * comp, fmt),
                                      buf, bv.get("byteOffset", 0))

        bad = degen = 0
        for mesh in gltf["meshes"]:
            for prim in mesh["primitives"]:
                nrm = acc(prim["attributes"]["NORMAL"])
                idx = acc(prim["indices"])
                for i in range(0, len(nrm), 3):
                    ln = math.sqrt(sum(nrm[i + j] ** 2 for j in range(3)))
                    if not math.isfinite(ln) or ln < 0.5:
                        bad += 1
                for k in range(0, len(idx), 3):
                    if len(set(idx[k:k + 3])) < 3:
                        degen += 1
        if bad or degen:
            problems.append("%s: %d bad normals, %d degenerate faces"
                            % (os.path.basename(gp), bad, degen))

        for image in gltf.get("images", []):
            uri = image.get("uri", "")
            if uri and not os.path.exists(os.path.join(os.path.dirname(gp), uri)):
                problems.append("%s: missing texture %s"
                                % (os.path.basename(gp), uri))

    print("  checked %d glTF files" % checked)
    check("no zero-length normals or degenerate faces", not problems,
          "; ".join(problems[:3]))


def check_project():
    print("\n== project.godot ==")
    text = open(os.path.join(ROOT, "project.godot")).read()

    m = re.search(r'run/main_scene="([^"]+)"', text)
    check("main scene is set and exists", m and os.path.exists(res_path(m.group(1))),
          m.group(1) if m else "not set")

    # Godot registers the ui_* actions itself; they are not in project.godot
    # unless overridden. Verified against the 4.3 source:
    # core/input/input_map.cpp:438 binds ui_cancel to Escape in
    # default_builtin_cache. Treating them as unmapped would be a false alarm.
    BUILTIN_ACTIONS = {
        "ui_accept", "ui_select", "ui_cancel", "ui_focus_next",
        "ui_focus_prev", "ui_left", "ui_right", "ui_up", "ui_down",
        "ui_page_up", "ui_page_down", "ui_home", "ui_end",
    }
    actions = set(re.findall(r"^(\w+)=\{", text, re.M)) | BUILTIN_ACTIONS
    used = set()
    for name in os.listdir(os.path.join(ROOT, "scripts")):
        if name.endswith(".gd"):
            src = open(os.path.join(ROOT, "scripts", name)).read()
            used |= set(re.findall(r'(?:is_action_pressed|is_action_just_pressed'
                                   r'|get_action_strength)\("([^"]+)"\)', src))
    check("every input action used by the scripts is mapped", used <= actions,
          ", ".join(sorted(used - actions)))
    print("  actions: %s" % ", ".join(sorted(used)))

    m = re.search(r"common/physics_ticks_per_second=(\d+)", text)
    rate = int(m.group(1)) if m else 60
    check("physics tick rate is high enough for a vehicle", rate >= 120, "%d Hz" % rate)


def check_scripts():
    print("\n== scripts ==")
    car = open(os.path.join(ROOT, "scenes", "car.tscn")).read()
    car_nodes = set(re.findall(r'\[node name="([^"]+)"', car))
    for needed in ("Wheels", "Model", "CameraTarget", "LF", "RF", "LR", "RR",
                   "Smooth"):
        check("car.tscn has a %s node" % needed, needed in car_nodes)

    main = open(os.path.join(ROOT, "scenes", "main.tscn")).read()
    game = open(os.path.join(ROOT, "scripts", "game.gd")).read()

    # The vehicle is spawned at runtime now, because the ground is procedural
    # and a fixed height in the scene file would bury or float the car.
    check("a game controller spawns the vehicle", "func _spawn" in game)
    check("the spawn point follows the terrain", "sample_height" in game)
    check("the camera is retargeted when the vehicle changes",
          "set_target" in game)
    check("the HUD is retargeted too", "set_vehicle" in game)
    check("all three vehicles are registered",
          all(v in game for v in ("car.tscn", "pickup.tscn", "defender.tscn")))

    for scene in ("car.tscn", "pickup.tscn", "defender.tscn"):
        path = os.path.join(ROOT, "scenes", scene)
        check("%s exists" % scene, os.path.exists(path))
        if os.path.exists(path):
            text = open(path).read()
            check("  %s has a smoothing node" % scene, "smoothing.gd" in text)
            check("  %s has four wheels" % scene,
                  all(('name="%s"' % c.upper()) in text for c in
                      ("lf", "rf", "lr", "rr")))


def check_common_mistakes():
    """Guards against the classes of bug that actually broke this project."""
    print("\n== regression guards ==")
    src = {}
    for name in os.listdir(os.path.join(ROOT, "scripts")):
        if name.endswith(".gd"):
            raw = open(os.path.join(ROOT, "scripts", name)).read()
            # strip comments so documentation cannot trip the checks
            src[name] = "\n".join(line.split("#")[0] for line in raw.splitlines())

    # A RigidBody3D's transform is owned by the physics server; assigning it
    # directly is silently reverted on the next tick.
    veh = src.get("vehicle.gd", "")
    bad = re.search(r"^\s*global_transform\s*=", veh, re.M)
    check("vehicle does not assign global_transform directly",
          bad is None, "use PhysicsServer3D.body_set_state")

    # @tool scripts run in the editor. That is wanted for the world builders -
    # it is what makes the map visible and editable there - but not for
    # anything that touches physics state, because the editor would then write
    # runtime values into the saved scene. wheel.gd did exactly that once.
    # plant_species.gd is on the list because it is a plain Resource holding
    # numbers - it has no _process, no _physics_process and touches no node -
    # and a Resource has to be @tool for its exports to appear in the
    # inspector at all. The allowance is verified rather than trusted: a
    # script on this list that grows a physics callback still fails.
    EDITOR_ALLOWED = {"terrain.gd", "forest.gd", "plant_species.gd"}
    tools = [n for n, t in src.items()
             if t.lstrip().startswith("@tool") and n not in EDITOR_ALLOWED]
    check("no physics script is marked @tool", not tools, ", ".join(tools))

    physics_in_editor = [
        n for n in EDITOR_ALLOWED
        if re.search(r"func _physics_process|apply_force|apply_torque|"
                     r"linear_velocity|angular_velocity", src.get(n, ""))]
    check("the editor-allowed scripts really do not touch physics",
          not physics_in_editor, ", ".join(physics_in_editor))
    # The world builders must offer a rebuild button. plant_species.gd is a
    # data resource, not a builder - it has nothing to rebuild - so it is
    # checked for being valid inspector data instead.
    WORLD_BUILDERS = {"terrain.gd", "forest.gd"}
    for name in EDITOR_ALLOWED:
        if name not in src:
            continue
        check("%s builds in the editor" % name,
              src[name].lstrip().startswith("@tool"))
        if name in WORLD_BUILDERS:
            check("  and can be rebuilt from the inspector",
                  "rebuild" in src[name] and "func build" in src[name])
        else:
            check("  and exposes its fields to the inspector",
                  "extends Resource" in src[name]
                  and src[name].count("@export") >= 5)

    # Writing to a SpringArm3D child's transform fights the arm.
    #
    # This used to test only .position and .rotation, and missed
    # `camera.transform = Transform3D.IDENTITY` - which is precisely what
    # collapsed the chase camera onto the car's roof. SpringArm3D places its
    # child with set_global_transform() from the physics tick
    # (scene/3d/physics/spring_arm_3d.cpp); assigning the child's local
    # transform parks it on the pivot instead. `transform` is the one that
    # actually shipped broken, so it is now the one that is checked.
    cam_raw = src.get("chase_camera.gd", "")
    cam = "\n".join(l.split("#")[0] for l in cam_raw.splitlines())
    bad = re.findall(
        r"camera\.(position|rotation|transform|global_position|"
        r"global_transform|basis)\s*=", cam)
    check("camera transform is left to the spring arm", not bad,
          "script assigns camera.%s" % ", camera.".join(sorted(set(bad))))

    # Every wheel visual must be optional, the model is wired up deferred.
    wheel = src.get("wheel.gd", "")
    check("wheel visuals are null-guarded for the first frame",
          "wheel_visual == null" in wheel)

    # Scripts must not assume the model exists before it is instanced.
    check("vehicle null-checks the model before driving it",
          "_model != null" in veh and "has_method" in veh)
    check("the model is looked up, not assumed to be a direct child",
          "_find_model" in veh)


def check_types():
    """Runs the call-signature type checker as part of the project gate."""
    print("\n== call signatures ==")
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "typecheck.py")],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    check("every call matches its function signature", ok,
          "" if ok else r.stdout.strip().splitlines()[-1])

    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "render_check.py")],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    check("rendering environment is sane", ok,
          "" if ok else r.stdout.strip().splitlines()[-1])


def check_editable_forest():
    """The vegetation must be editable from the inspector, not only in code."""
    print("\n== forest is editable ==")
    forest = open(os.path.join(ROOT, "scripts", "forest.gd")).read()
    species = open(os.path.join(ROOT, "scripts", "plant_species.gd")).read()

    check("plant species are a Resource, not a const Dictionary",
          "extends Resource" in species and "class_name PlantSpecies" in species)
    check("the species list is exported",
          re.search(r"@export var species\s*:\s*Array\[PlantSpecies\]", forest)
          is not None)
    check("PlantSpecies is a @tool script so it works in the editor",
          species.lstrip().startswith("@tool"))

    # Every field a player would reasonably want to change must be exported.
    for field in ("count", "enabled", "scale_min", "scale_max", "tint",
                  "cull_distance", "cast_shadows", "solid", "max_slope"):
        check("  %s is editable" % field,
              re.search(r"@export[^\n]*\bvar %s\b" % field, species) is not None,
              "not exported")

    check("there is a global density control",
          "var density" in forest and "@export" in forest)
    check("the list can be restored after a bad edit",
          "reset_species" in forest)
    check("rebuilding from the inspector still works",
          "@export var rebuild" in forest)
    # The old hard-coded table must be gone as the live source of truth.
    check("nothing reads the old const table at runtime",
          "for species in SPECIES" not in forest)


def check_no_tiny_colliders():
    """No solid prop may be small enough to be an invisible obstacle.

    The complaint was crashing into small trees. Two separate causes:
    colliders sized from a typed-in number rather than the mesh, and species
    whose scale range let them generate knee-high instances that still got a
    car-stopping collider.
    """
    print("\n== no small solid props ==")
    forest = open(os.path.join(ROOT, "scripts", "forest.gd")).read()

    m = re.search(r"@export var min_solid_height\s*:=\s*([\d.]+)", forest)
    check("there is a minimum height for solid props", m is not None)
    if not m:
        return
    floor_h = float(m.group(1))
    print("  minimum solid height: %.2f m" % floor_h)
    check("the minimum is above knee height", floor_h >= 1.0,
          "%.2f m" % floor_h)

    check("instances below it are skipped",
          "if world_height < min_solid_height" in forest)
    check("solid species cannot be scaled below it",
          "floor_scale" in forest and "min_solid_height / entry.mesh_height" in forest)
    check("collider size comes from the mesh, not a typed-in number",
          "mesh.get_aabb()" in forest and "entry.mesh_height = aabb.size.y" in forest)

    # Work out, for every default species, the smallest instance it can make.
    entries = re.findall(
        r'\{"mesh_name": "(\w+)".*?\}', forest, re.S)
    table = re.search(r"const DEFAULT_SPECIES := \[(.*?)\n\]", forest, re.S)
    if table is None:
        check("the default table can be parsed", False)
        return
    bad = []
    for block in re.finditer(r'\{"mesh_name": "(\w+)"(.*?)\},', table.group(1), re.S):
        name, body = block.group(1), block.group(2)
        solid = '"solid": true' in body
        if not solid:
            continue
        sm = re.search(r'"scale_min": ([\d.]+)', body)
        hm = re.search(r'"mesh_height": ([\d.]+)', body)
        if not sm or not hm:
            continue
        smallest = float(sm.group(1)) * float(hm.group(1))
        raised = max(float(sm.group(1)), floor_h / float(hm.group(1))) * float(hm.group(1))
        print("  %-9s smallest was %.2f m -> now %.2f m" % (name, smallest, raised))
        if raised < floor_h - 1e-6:
            bad.append("%s %.2f m" % (name, raised))
    check("no solid species can produce a sub-threshold instance",
          not bad, ", ".join(bad))


def check_fps_counter():
    """The fps counter must exist and must not be able to go missing.

    It previously lived in main.tscn and was fetched with `@onready var _fps:
    Label = $Fps`. That combination produced

        Node not found: "Fps" (relative to "/root/Main/HUD")

    followed by an "Invalid assignment of property 'text' ... on a base object
    of type 'null instance'" on every single frame afterwards, because $Node
    does not fail loudly - it stores null and lets every later use explode.

    The widgets are built in code now, so the check is that no HUD widget is
    fetched with the unguarded $ syntax at all.
    """
    print("\n== fps counter ==")
    hud_raw = open(os.path.join(ROOT, "scripts", "hud.gd")).read()
    # Strip comments before pattern matching. The comment explaining this very
    # bug contains the string "$Fps", and matching it would fail the check
    # that the bug is fixed - a false alarm caused by the fix's own
    # documentation.
    hud = "\n".join(l.split("#")[0] for l in hud_raw.splitlines())

    check("the fps label is created by the script, not fetched from a scene",
          'made.name = label_name' in hud and '_need_label' in hud)
    check("every widget lookup is guarded",
          "get_node_or_null" in hud)

    # This is the real regression guard: not one @onready $Path in the HUD.
    unguarded = re.findall(r"@onready\s+var\s+\w+\s*:[^=]+=\s*\$([\w/]+)", hud)
    check("no HUD widget is fetched with the unguarded $ syntax",
          not unguarded, "still uses $%s" % ", $".join(unguarded))

    # And nothing may write .text to something that was never checked.
    for widget in ("_fps", "_speed", "_gear", "_hint", "_debug"):
        declared = re.search(r"var %s\s*:\s*(Label|ProgressBar)\s*$"
                             % widget, hud, re.M)
        check("  %s is declared without a scene lookup" % widget,
              declared is not None, "%s is still bound to a scene node" % widget)

    check("it is updated every frame", "_update_fps(delta)" in hud)
    check("the reading is averaged, not a single frame",
          "FPS_WINDOW" in hud and "_frames" in hud)
    check("the worst frame in the window is reported too",
          "_worst_frame" in hud)
    check("draw calls and triangles are available for diagnosis",
          "RENDER_TOTAL_DRAW_CALLS_IN_FRAME" in hud
          and "RENDER_TOTAL_PRIMITIVES_IN_FRAME" in hud)
    check("the counter can be switched off in the settings",
          "GameSettings.show_fps" in hud)


def check_frame_rate_control():
    """The project must actually be able to exceed 60 fps.

    Two independent things stopped it, both confirmed against the engine
    source rather than guessed:

      * project.godot set `debug/settings/fps/force_fps=0`. That is the
        Godot 3 name. Grepping the whole 4.3 tree for "force_fps" returns
        nothing; main.cpp:2377 reads `application/run/max_fps`. The old line
        was inert.
      * `display/window/vsync/vsync_mode=1` is VSYNC_ENABLED
        (main.cpp:2369), which pins presentation to the display refresh rate
        no matter what max_fps says.
    """
    print("\n== frame rate control ==")
    raw = open(os.path.join(ROOT, "project.godot")).read()
    # Same reason as above: the comment recording why force_fps was removed
    # mentions it by name.
    text = "\n".join(l for l in raw.splitlines() if not l.startswith(";"))

    check("the dead Godot 3 fps setting is gone",
          "force_fps" not in text, "force_fps is still there and does nothing")

    m = re.search(r"^run/max_fps=(\d+)", text, re.M)
    check("the setting the engine actually reads is present", m is not None,
          "" if m else "application/run/max_fps is missing")
    if m:
        print("  run/max_fps = %s (%s)"
              % (m.group(1), "unlimited" if m.group(1) == "0" else "capped"))
        check("no frame cap by default", m.group(1) == "0", m.group(1))

    m = re.search(r"^window/vsync/vsync_mode=(\d+)", text, re.M)
    check("vsync is declared explicitly", m is not None)
    if m:
        modes = {"0": "disabled", "1": "enabled", "2": "adaptive",
                 "3": "mailbox"}
        print("  vsync_mode = %s (%s)" % (m.group(1),
                                          modes.get(m.group(1), "?")))
        check("vsync does not cap the frame rate by default",
              m.group(1) in ("0", "3"),
              "" if m.group(1) in ("0", "3")
              else "mode %s pins fps to the refresh rate" % m.group(1))

    # physics_hz / max_physics_steps_per_frame is a single number with TWO
    # opposite meanings, and I had a check demanding each of them:
    #
    #   below it, simulated time runs slow (main.cpp:4033 discards the
    #     leftover time), so a LOW value is wanted;
    #   at it, the frame rate can lock, because catch-up ticks make the next
    #     frame long too and it saturates, so a HIGH value is wanted.
    #
    # v2.8 set 8 steps to push the dilation floor down to 15 fps. That gave
    # the user a game that would suddenly seize at exactly 120/8 = 15.0 fps.
    #
    # The lock-up is far worse than the dilation: at 39 fps the clock slows a
    # few percent and the car still drives, whereas locking at 15 fps makes
    # it unplayable and it cannot recover on its own. So the value is chosen
    # against the lock-up, and this check now guards the band rather than one
    # end of it.
    hz = int(re.search(r"common/physics_ticks_per_second=(\d+)",
                       text).group(1))
    steps = int(re.search(r"common/max_physics_steps_per_frame=(\d+)",
                          text).group(1))
    floor_fps = hz / steps
    print("  physics %d Hz, max %d steps/frame -> floor at %.1f fps"
          % (hz, steps, floor_fps))
    print("    below it the clock slows; at it the frame rate can lock")
    check("the game cannot lock itself at an unplayable frame rate",
          floor_fps > 25.0,
          "" if floor_fps > 25.0 else "can lock at %.1f fps" % floor_fps)
    check("time still runs true at playable frame rates", floor_fps <= 60.0,
          "" if floor_fps <= 60.0 else "clock slows below %.1f fps" % floor_fps)

    # And the settings singleton must be loaded before any scene.
    check("the settings autoload is registered",
          re.search(r'GameSettings="\*res://scripts/game_settings\.gd"', text)
          is not None)


def check_texture_imports():
    """Every texture must ship a .import that matches how it is used.

    Without one, the editor prints two lines per texture on a fresh checkout:

        <file>: текстура используется как карта нормалей в 3D...
        <file>: текстура используется как карта шероховатости в 3D...

    resource_importer_texture.cpp:110-131 emits those exactly when a texture
    is used as a normal map while its .import still says
    compress/normal_map = 0, or used for roughness while roughness/mode = 0.
    The project's old [importer_defaults] block set both of those for ALL
    textures, so the setting meant to silence the messages was producing
    them - 18 pairs of them.

    Raising normal_map project-wide is not the fix either: red-green
    compression discards the blue channel, which would wreck the albedo maps.
    editor_file_system.cpp:2435-2459 shows importer_defaults are only merged
    in when a file has no .import of its own, so per-file .import wins.
    """
    print("\n== texture import settings ==")
    text = open(os.path.join(ROOT, "project.godot")).read()
    live = "\n".join(l for l in text.splitlines() if not l.startswith(";"))
    check("the blanket importer_defaults block is gone",
          "[importer_defaults]" not in live,
          "it forces the same settings onto every texture")

    gltf = json.load(open(os.path.join(ROOT, "assets", "car", "bmw_1m.gltf")))
    images = [i.get("uri", "") for i in gltf.get("images", [])]
    textures = [t.get("source") for t in gltf.get("textures", [])]
    normals = set()
    for mat in gltf.get("materials", []):
        if "normalTexture" in mat:
            normals.add(os.path.basename(
                images[textures[mat["normalTexture"]["index"]]]))

    tex_dir = os.path.join(ROOT, "assets", "car", "textures")
    files = [f for f in sorted(os.listdir(tex_dir))
             if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    missing = [f for f in files
               if not os.path.exists(os.path.join(tex_dir, f + ".import"))]
    print("  %d textures, %d used as normal maps in the glTF"
          % (len(files), len(normals)))
    check("every texture has a .import", not missing,
          "%d without one: %s" % (len(missing), ", ".join(missing[:4])))

    wrong = []
    for name in files:
        path = os.path.join(tex_dir, name + ".import")
        if not os.path.exists(path):
            continue
        body = open(path).read()
        m = re.search(r"compress/normal_map=(\d+)", body)
        setting = int(m.group(1)) if m else -1
        want = 1 if name in normals else 2
        if setting != want:
            wrong.append("%s=%d(want %d)" % (name, setting, want))
    check("each .import matches the texture's role in the glTF", not wrong,
          ", ".join(wrong[:4]))

    # Role must come from the glTF, not from the file name: RIM_OS.png and
    # Fanali_Anteriori_OS.png are normal maps despite the _OS suffix.
    tool = open(os.path.join(ROOT, "tools", "make_import_files.py")).read()
    check("roles are read from the glTF, not guessed from names",
          "roles_from_gltf" in tool and "normalTexture" in tool)


def check_uids():
    """Every .import UID must be one Godot can round-trip, and be unique.

    This is the bug that produced 44 copies of each of these in the log:

        core/io/resource_uid.cpp:132 - Condition "!unique_ids.has(p_id)" is
        true. Returning: String()
        Can't find file 'uid://chxql2rtxgf8b'.

    resource_uid.cpp:38-41 defines the alphabet with an off-by-one the Godot
    authors documented but cannot fix for compatibility:

        static constexpr uint32_t char_count = ('z' - 'a');       // 25
        static constexpr uint32_t base = char_count + ('9' - '0');  // 34

    So the digits are 'a'..'y' plus '0'..'8' - 'z' and '9' are never emitted
    by id_to_text() and are misread by text_to_id(). make_import_files.py used
    to write uuid4().hex, which is hex, so 29 of 44 UIDs contained a '9'.
    Those ids could not be re-encoded to the same string, the editor's lookup
    missed, and every texture failed to resolve.

    The check is a round trip through Godot's own two functions, transcribed
    from the C++: text -> id -> text must give back the original.
    """
    print("\n== resource UIDs ==")

    char_count, base = 25, 34

    def text_to_id(text):
        # resource_uid.cpp:66-83
        value = 0
        for ch in text:
            value *= base
            if "a" <= ch <= "z":
                value += ord(ch) - ord("a")
            elif ch.isdigit():
                value += ord(ch) - ord("0") + char_count
            else:
                return None
            value &= 0xFFFFFFFFFFFFFFFF
        return value & 0x7FFFFFFFFFFFFFFF

    def id_to_text(value):
        # resource_uid.cpp:46-63
        out = ""
        while value:
            c = value % base
            out = (chr(ord("a") + c) if c < char_count
                   else chr(ord("0") + (c - char_count))) + out
            value //= base
        return out

    imports = []
    for folder, _, names in os.walk(os.path.join(ROOT, "assets")):
        for name in names:
            if name.endswith(".import"):
                imports.append(os.path.join(folder, name))
    imports.sort()

    seen = {}
    broken = []
    duplicates = []
    for path in imports:
        rel = os.path.relpath(path, ROOT)
        m = re.search(r'uid="uid://([^"]+)"', open(path).read())
        if not m:
            broken.append("%s: no uid" % rel)
            continue
        text = m.group(1)
        value = text_to_id(text)
        if value is None or id_to_text(value) != text:
            broken.append("%s: %s -> %s" % (rel, text, id_to_text(value or 0)))
        if value in seen:
            duplicates.append("%s and %s" % (rel, seen[value]))
        seen[value] = rel

    print("  %d .import files" % len(imports))
    check("every UID survives text -> id -> text", not broken,
          "%d broken, e.g. %s" % (len(broken), "; ".join(broken[:3])))
    check("no UID contains 'z' or '9'",
          not [t for t in seen.values() if False] and
          all(("z" not in re.search(r'uid="uid://([^"]+)"',
                                    open(os.path.join(ROOT, p)).read()).group(1)
               and "9" not in re.search(r'uid="uid://([^"]+)"',
                                        open(os.path.join(ROOT, p)).read()).group(1))
              for p in seen.values()),
          "those two characters are outside Godot's 34-symbol alphabet")
    check("no two resources share a UID", not duplicates,
          "; ".join(duplicates[:3]))

    # Derived from the path, so re-running the generator is a no-op rather
    # than a project-wide reimport.
    # Look at the code, not the prose: the docstring explains the old uuid4
    # mistake on purpose, and a plain substring search matched that comment
    # and failed a correct file.
    tool = open(os.path.join(ROOT, "tools", "make_import_files.py")).read()
    code = "\n".join(l for l in tool.splitlines()
                     if not l.lstrip().startswith("#"))
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    check("UIDs are derived from the path, not random",
          "uuid" not in code and "def uid_for" in code,
          "random UIDs change on every run and force a full reimport")


def check_lod_warning_is_not_ours():
    """The 'non-finite normal in LOD generation' notice is not an asset fault.

    importer_mesh.cpp:513-521 walks `new_indices` - the OUTPUT of
    meshoptimizer's simplifier - and skips any triangle whose area came out
    zero. Simplification collapses edges, so it can create such a triangle
    from a perfectly good input. It is WARN_PRINT_ONCE, so it appears once
    per run however many faces are skipped.

    This check proves the input is clean, which is the part the project can
    actually be responsible for.
    """
    print("\n== LOD warning: assets must be clean ==")
    import struct as _struct
    import glob as _glob

    fmt = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}
    ncomp = {"VEC3": 3, "VEC2": 2, "VEC4": 4, "SCALAR": 1}
    total = degenerate = 0

    for path in sorted(_glob.glob(os.path.join(ROOT, "assets", "*", "*.gltf"))):
        bin_path = path[:-5] + ".bin"
        if not os.path.exists(bin_path):
            continue
        gltf = json.load(open(path))
        blob = open(bin_path, "rb").read()

        def read(index):
            acc = gltf["accessors"][index]
            view = gltf["bufferViews"][acc["bufferView"]]
            f = fmt[acc["componentType"]]
            n = ncomp[acc["type"]]
            off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
            stride = view.get("byteStride") or _struct.calcsize(f) * n
            return [_struct.unpack_from("<" + f * n, blob, off + i * stride)
                    for i in range(acc["count"])]

        for mesh in gltf["meshes"]:
            for prim in mesh["primitives"]:
                if "indices" not in prim:
                    continue
                pos = read(prim["attributes"]["POSITION"])
                idx = [i[0] for i in read(prim["indices"])]
                for t in range(0, len(idx) - 2, 3):
                    a, b, c = pos[idx[t]], pos[idx[t + 1]], pos[idx[t + 2]]
                    u = [b[k] - a[k] for k in range(3)]
                    w = [c[k] - a[k] for k in range(3)]
                    cross = (u[1] * w[2] - u[2] * w[1],
                             u[2] * w[0] - u[0] * w[2],
                             u[0] * w[1] - u[1] * w[0])
                    total += 1
                    if sum(x * x for x in cross) == 0.0:
                        degenerate += 1

    print("  %s triangles across every glTF, %d with zero area"
          % (format(total, ","), degenerate))
    check("no shipped mesh contains a degenerate face", degenerate == 0,
          "%d degenerate faces" % degenerate)


def check_menus():
    print("\n== menus ==")
    def source(name):
        """Script text with comments stripped.

        Necessary because the doc comment on pause_menu.gd explains why
        PROCESS_MODE_ALWAYS is needed, and a plain substring search found the
        explanation rather than the code - the check passed even after the
        real assignment was removed. Verified by deleting it.
        """
        raw = open(os.path.join(ROOT, "scripts", name)).read()
        return "\n".join(l.split("#")[0] for l in raw.splitlines())

    menu = source("main_menu.gd")
    pause = source("pause_menu.gd")
    settings = source("settings_menu.gd")
    game = source("game.gd")
    project = open(os.path.join(ROOT, "project.godot")).read()

    check("the game boots into the main menu",
          'run/main_scene="res://scenes/main_menu.tscn"' in project)
    check("the main menu scene exists",
          os.path.exists(os.path.join(ROOT, "scenes", "main_menu.tscn")))

    for caption in ("Играть", "Настройки", "Выход"):
        check("  main menu has a %s button" % caption, caption in menu)
    check("Играть loads the driving scene",
          "change_scene_to_file(GAME_SCENE)" in menu)

    check("Esc opens the pause menu", 'ui_cancel' in game and "_pause.toggle()" in game)
    for caption in ("Продолжить", "В главное меню", "Выйти из игры"):
        check("  pause menu has a %s button" % caption, caption in pause)
    check("pausing actually pauses the tree", "get_tree().paused = true" in pause)
    check("leaving the pause menu unpauses", "get_tree().paused = false" in pause)

    # The classic pause-menu bug: the menu is paused along with the game and
    # stops responding, so nothing can be clicked.
    # Must be an actual assignment, not a mention in a comment.
    assigned = r"process_mode\s*=\s*Node\.PROCESS_MODE_ALWAYS"
    check("the pause menu keeps running while paused",
          re.search(assigned, pause) is not None,
          "process_mode is never set to ALWAYS")
    check("the settings screen keeps running while paused",
          re.search(assigned, settings) is not None,
          "process_mode is never set to ALWAYS")
    check("the settings singleton keeps running while paused",
          re.search(assigned, source("game_settings.gd")) is not None,
          "process_mode is never set to ALWAYS")
    check("unpausing before a scene change",
          "get_tree().paused = false" in pause
          and "change_scene_to_file(MAIN_MENU)" in pause)

    # Settings content.
    gs = source("game_settings.gd")
    check("fps limit is adjustable", "FPS_OPTIONS" in gs)
    check("the unlimited option exists", "Без ограничения" in gs)
    check("vsync is adjustable", "set_vsync" in settings)
    check("settings survive a restart", "ConfigFile" in gs)
    check("the limit is pushed into the engine", "Engine.max_fps = max_fps" in gs)
    check("vsync is pushed into the engine",
          "DisplayServer.window_set_vsync_mode" in gs)
    # The interaction that makes "I set 240 and still see 75" baffling.
    check("the menu explains that vsync overrides the fps limit",
          "синхронизация ограничивает" in settings)


def check_camera_smoothing():
    """The camera and the car model must be on the same clock.

    The model hangs off a TransformSmoothing node and moves at the display
    rate. If the camera moves in 120 Hz steps, the difference - which is what
    is on screen - is not smooth, however smooth the simulation is.
    """
    print("\n== camera and model share a clock ==")
    cam = open(os.path.join(ROOT, "scripts", "chase_camera.gd")).read()

    check("the camera does its work in _process, not _physics_process",
          re.search(r"func _process\(delta: float\) -> void:", cam) is not None)
    check("_physics_process only samples the target",
          "_curr_target = _target.global_transform" in cam)
    check("it interpolates between physics ticks",
          "Engine.get_physics_interpolation_fraction()" in cam)
    check("velocity is interpolated too, so the fov and pitch do not step",
          "_prev_velocity.lerp(_curr_velocity" in cam)
    check("a teleport snaps instead of flying across the map",
          "> 8.0" in cam)

    # Nothing may still read the raw body transform during _process.
    body = cam[cam.index("func _process(delta: float)"):]
    body = body[:body.index("## Rolls") if "## Rolls" in body else len(body)]
    raw = re.findall(r"_target\.global_(?:position|transform|rotation)", body)
    check("_process never reads the un-interpolated body transform",
          not raw, "%d raw reads" % len(raw))


def main():
    print("Godot project validation")
    check_scenes()
    check_gltf()
    check_project()
    check_scripts()
    check_all_assets()
    check_common_mistakes()
    check_types()
    check_editable_forest()
    check_no_tiny_colliders()
    check_fps_counter()
    check_frame_rate_control()
    check_texture_imports()
    check_uids()
    check_lod_warning_is_not_ours()
    check_menus()
    check_camera_smoothing()
    print("\n%s" % ("ALL CHECKS PASSED" if not FAILURES else "FAILURES: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
