#!/usr/bin/env python3
"""
Full analysis of every uploaded asset: structure, geometry, textures and -
the question that was actually asked - whether any of them contain animation.

This is the tool that produced docs/ASSETS.md. It is kept in the repository so
the claims in that document can be re-checked rather than taken on trust.

Usage:
    python3 tools/asset_report.py <dir with the extracted assets>
"""

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbxparse as F  # noqa: E402


def analyse(path):
    info = {"file": os.path.basename(path), "bytes": os.path.getsize(path)}
    try:
        info["version"] = F.version(path)
        nodes = F.parse(path)
    except Exception as exc:                       # noqa: BLE001
        info["error"] = str(exc)
        return info

    top = {n[0]: n for n in nodes}
    gs = top.get("GlobalSettings")
    if gs:
        pr = F.props70(gs)
        info["unit_scale"] = (pr.get("UnitScaleFactor") or [None])[0]
        info["up_axis"] = (pr.get("UpAxis") or [None])[0]

    objs = top.get("Objects")
    if objs is None:
        info["error"] = "no Objects block"
        return info

    kinds = Counter(x[0] for x in objs[2])
    info["objects"] = dict(kinds)

    verts = polys = 0
    meshes = []
    for g in objs[2]:
        if g[0] != "Geometry":
            continue
        v = p = 0
        for ch in g[2]:
            if ch[0] == "Vertices":
                v = len(ch[1][0]) // 3
            elif ch[0] == "PolygonVertexIndex":
                p = sum(1 for i in ch[1][0] if i < 0)
        verts += v
        polys += p
        meshes.append((v, p))
    info["verts"] = verts
    info["polys"] = polys
    info["meshes"] = len(meshes)

    models = [m for m in objs[2] if m[0] == "Model"]
    info["models"] = len(models)
    names = [m[1][1].split("\x00")[0] for m in models if isinstance(m[1][1], str)]
    groups = Counter(n.split(".")[0].split("|")[0].rstrip("0123456789_ ") for n in names)
    info["name_groups"] = dict(groups.most_common(8))

    # --- the animation question ------------------------------------------- #
    anim_kinds = [k for k in kinds
                  if "Anim" in k or "Deformer" in k or k in ("Pose",)]
    takes = top.get("Takes")
    take_names = []
    if takes:
        take_names = [c[1][0] for c in takes[2] if c[0] == "Take"]
    info["animation"] = {
        "objects": {k: kinds[k] for k in anim_kinds},
        "takes": take_names,
        "has_animation": bool(anim_kinds) or bool(take_names),
    }

    info["materials"] = [m[1][1].split("\x00")[0]
                         for m in objs[2] if m[0] == "Material"][:10]
    texs = []
    for t in objs[2]:
        if t[0] != "Texture":
            continue
        for ch in t[2]:
            if ch[0] == "RelativeFilename":
                v = ch[1][0]
                v = v.decode("utf8", "ignore") if isinstance(v, bytes) else v
                texs.append(v.replace("\\", "/").split("/")[-1])
    info["textures"] = texs[:10]
    return info


def main():
    src = sys.argv[1]
    files = sorted(f for f in os.listdir(src) if f.lower().endswith(".fbx"))
    animated = []
    print("Asset analysis\n" + "=" * 74)
    for f in files:
        info = analyse(os.path.join(src, f))
        print("\n%s  (%.1f MB, FBX %s)"
              % (info["file"], info["bytes"] / 1e6, info.get("version", "?")))
        if "error" in info:
            print("   ERROR: %s" % info["error"])
            continue
        print("   units: scale=%s up_axis=%s"
              % (info.get("unit_scale"), info.get("up_axis")))
        print("   %d models, %d meshes, %d verts, %d polys"
              % (info["models"], info["meshes"], info["verts"], info["polys"]))
        print("   groups: %s" % info["name_groups"])
        anim = info["animation"]
        if anim["has_animation"]:
            print("   ANIMATION: %s takes=%s" % (anim["objects"], anim["takes"]))
            animated.append(info["file"])
        else:
            print("   ANIMATION: none - static geometry only")
        if info["materials"]:
            print("   materials: %s" % info["materials"])
        if info["textures"]:
            print("   textures: %s" % info["textures"])

    print("\n" + "=" * 74)
    if animated:
        print("Files containing animation: %s" % ", ".join(animated))
    else:
        print("No file contains animation, skinning, poses or takes.")
        print("Any movement (wind, sway) therefore has to come from a shader.")


if __name__ == "__main__":
    main()
