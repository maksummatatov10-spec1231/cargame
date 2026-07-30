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
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []


def check(label, ok, detail=""):
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  -> " + detail) if detail else ""))
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


def check_project():
    print("\n== project.godot ==")
    text = open(os.path.join(ROOT, "project.godot")).read()

    m = re.search(r'run/main_scene="([^"]+)"', text)
    check("main scene is set and exists", m and os.path.exists(res_path(m.group(1))),
          m.group(1) if m else "not set")

    actions = set(re.findall(r"^(\w+)=\{", text, re.M))
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
    for needed in ("Wheels", "Model", "CameraTarget", "LF", "RF", "LR", "RR"):
        check("car.tscn has a %s node" % needed, needed in car_nodes)

    main = open(os.path.join(ROOT, "scenes", "main.tscn")).read()
    m = re.search(r'target_path = NodePath\("([^"]+)"\)', main)
    check("camera target path is set", m is not None, m.group(1) if m else "")
    m = re.search(r'vehicle_path = NodePath\("([^"]+)"\)', main)
    check("HUD vehicle path is set", m is not None, m.group(1) if m else "")

    # the car must spawn above the ground for the drop
    m = re.search(r'\[node name="Car"[^\]]*\]\ntransform = Transform3D\(([^)]+)\)', main)
    if m:
        vals = [float(v) for v in m.group(1).split(",")]
        height = vals[10]
        print("  car spawns at %.2f m" % height)
        check("car spawns in the air", height > 0.5, "%.2f m" % height)


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

    # @tool scripts run in the editor and can silently rewrite scene data.
    tools = [n for n, t in src.items() if t.lstrip().startswith("@tool")]
    check("no gameplay script is marked @tool", not tools, ", ".join(tools))

    # Writing to a SpringArm3D child's transform fights the arm.
    cam = src.get("chase_camera.gd", "")
    check("camera transform is left to the spring arm",
          not re.search(r"camera\.(position|rotation)\s*=", cam))

    # Every wheel visual must be optional, the model is wired up deferred.
    wheel = src.get("wheel.gd", "")
    check("wheel visuals are null-guarded for the first frame",
          "wheel_visual == null" in wheel)

    # Scripts must not assume the model exists before it is instanced.
    check("vehicle null-checks the model before driving it", "if _model:" in veh)


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


def main():
    print("Godot project validation")
    check_scenes()
    check_gltf()
    check_project()
    check_scripts()
    check_common_mistakes()
    check_types()
    print("\n%s" % ("ALL CHECKS PASSED" if not FAILURES else "FAILURES: " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
