#!/usr/bin/env python3
"""
Convert the forest FBX assets into clean glTF files for Godot.

Findings from analysing the uploaded assets (see docs/ASSETS.md):

  fbx game export.fbx   18 meshes, 19 662 polys - one good 6.9 m tree plus
                        grass, ferns and small plants. Z-up, model scale 1000,
                        UnitScaleFactor 0.1, which works out to metres 1:1.
  grass.fbx             two dense "Wild Grass Patch" clumps, 12 651 verts
  tree+asset.fbx        a branch card and three rock-like Cube meshes
  Extra lowpoly tree.fbx  five flat branch cards for billboards
  untitled.fbx          grass and daisy cards, already duplicated 5 000 times

None of them contain animation, skinning, poses or takes - they are static
meshes, so any movement (wind, sway) has to come from a shader.

This tool pulls out the meshes worth using, converts Z-up to Godot's Y-up,
scales to metres, and writes one glTF per asset with its textures.

Usage:
    python3 tools/forest_to_gltf.py <asset_dir> <out_dir>
"""

import json
import math
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbxparse as F  # noqa: E402


# Which meshes to take from which file, and what to call them.
# name -> (source file, geometry name, scale to metres, texture hints)
WANTED = {
    "tree": ("fbx game export.fbx", "Tree.001", 1.0),
    "fern_a": ("fbx game export.fbx", "GPlant1.001", 1.0),
    "fern_b": ("fbx game export.fbx", "GPlant3.001", 1.0),
    "plant": ("fbx game export.fbx", "Plant1.001", 1.0),
    "grass_tuft": ("fbx game export.fbx", "Grass.001", 1.0),
    "bush_a": ("fbx game export.fbx", "FPlantR.001", 1.0),
    "bush_b": ("fbx game export.fbx", "FPlantR.003", 1.0),
    "rock_a": ("tree+asset.fbx", "Cube", 0.35),
    "rock_b": ("tree+asset.fbx", "Cube.002", 0.30),
    "rock_c": ("tree+asset.fbx", "Cube.001", 0.22),
    "grass_patch": ("grass.fbx", "Wild Grass Patch 2", 1.0),
    # From untitled.fbx, which had never been used. It carries three species
    # that the map was missing entirely - notably the only flower in any of
    # the uploaded assets. The file also bakes 5,000 scattered copies of them
    # as Model nodes; those are ignored, because Forest scatters its own with
    # a MultiMesh and importing pre-placed duplicates would be 5,000 separate
    # nodes for no benefit.
    # collect() names a geometry after the Model it is connected to, and in
    # this file every geometry is wired to the FIRST of its 5,000 duplicates,
    # so the names come out as "Plane|<species>|Dupli|<n>".
    "grass_wide": ("untitled.fbx",
                   "Plane|Grass_Basic_A_spring-summer|Dupli|4396", 1.0),
    "grass_fine": ("untitled.fbx",
                   "Plane|Grass_Basic_D_spring-summer|Dupli|4399", 1.0),
    "daisy": ("untitled.fbx",
              "Plane|Flower_Daisy_A_spring-summer|Dupli|1099.1", 1.0),
}


def collect(path):
    """Return {geometry name: (vertices, polygon indices, normals, uvs)}."""
    nodes = F.parse(path)
    top = {n[0]: n for n in nodes}
    objs = top["Objects"]
    conns = top.get("Connections")

    models = {}
    for m in objs[2]:
        if m[0] == "Model":
            nm = m[1][1].split("\x00")[0] if isinstance(m[1][1], str) else "?"
            models[m[1][0]] = nm

    geo_to_model = {}
    if conns:
        for c in conns[2]:
            if c[1][0] == "OO" and c[1][2] in models:
                geo_to_model[c[1][1]] = models[c[1][2]]

    out = {}
    for g in objs[2]:
        if g[0] != "Geometry":
            continue
        name = geo_to_model.get(g[1][0])
        if name is None:
            continue
        verts = idx = normals = uvs = uvidx = None
        nmap = umap = uref = None
        for ch in g[2]:
            if ch[0] == "Vertices":
                verts = ch[1][0]
            elif ch[0] == "PolygonVertexIndex":
                idx = ch[1][0]
            elif ch[0] == "LayerElementNormal":
                for c2 in ch[2]:
                    if c2[0] == "Normals":
                        normals = c2[1][0]
                    elif c2[0] == "MappingInformationType":
                        nmap = c2[1][0]
            elif ch[0] == "LayerElementUV":
                for c2 in ch[2]:
                    if c2[0] == "UV":
                        uvs = c2[1][0]
                    elif c2[0] == "UVIndex":
                        uvidx = c2[1][0]
                    elif c2[0] == "MappingInformationType":
                        umap = c2[1][0]
                    elif c2[0] == "ReferenceInformationType":
                        uref = c2[1][0]
        if verts and idx:
            out[name] = (verts, idx, normals, nmap, uvs, uvidx, umap, uref)
    return out


def build_mesh(data, scale):
    """Triangulate and convert Z-up centimetre-ish data to Y-up metres."""
    verts, idx, normals, nmap, uvs, uvidx, umap, uref = data
    pos, nrm, uv, tris = [], [], [], []
    weld = {}
    poly = []

    for k, raw in enumerate(idx):
        last = raw < 0
        vi = ~raw if last else raw
        poly.append((k, vi))
        if not last:
            continue

        corners = []
        for k2, vi2 in poly:
            x = verts[vi2 * 3] * scale
            y = verts[vi2 * 3 + 1] * scale
            z = verts[vi2 * 3 + 2] * scale
            # Z-up -> Y-up: (x, y, z) becomes (x, z, -y)
            p = (x, z, -y)

            n = (0.0, 1.0, 0.0)
            if normals:
                ni = k2 if nmap == "ByPolygonVertex" else vi2
                # Some exporters write fewer normals than the mapping implies;
                # fall back rather than reading off the end.
                if (ni + 1) * 3 <= len(normals):
                    nx, ny, nz = (normals[ni * 3], normals[ni * 3 + 1],
                                  normals[ni * 3 + 2])
                    cand = (nx, nz, -ny)
                    ln = math.sqrt(sum(c * c for c in cand))
                    if ln > 1e-9:
                        n = tuple(c / ln for c in cand)

            if uvs:
                ui = k2 if umap == "ByPolygonVertex" else vi2
                if uref == "IndexToDirect" and uvidx:
                    ui = uvidx[ui]
                if ui >= 0 and (ui + 1) * 2 <= len(uvs):
                    t = (uvs[ui * 2], 1.0 - uvs[ui * 2 + 1])
                else:
                    t = (0.0, 0.0)
            else:
                t = (0.0, 0.0)
            corners.append((p, n, t))

        for i in range(1, len(corners) - 1):
            for c in (corners[0], corners[i], corners[i + 1]):
                key = (round(c[0][0], 5), round(c[0][1], 5), round(c[0][2], 5),
                       round(c[1][0], 3), round(c[1][1], 3), round(c[1][2], 3),
                       round(c[2][0], 4), round(c[2][1], 4))
                j = weld.get(key)
                if j is None:
                    j = len(pos) // 3
                    weld[key] = j
                    pos.extend(c[0])
                    nrm.extend(c[1])
                    uv.extend(c[2])
                tris.append(j)
        poly = []
    return pos, nrm, uv, tris


def write_gltf(name, pos, nrm, uv, tris, out_dir, texture=None, alpha_cut=False):
    buf = bytearray()
    views, accessors = [], []

    def add(data, target):
        while len(buf) % 4:
            buf.append(0)
        off = len(buf)
        buf.extend(data)
        views.append({"buffer": 0, "byteOffset": off,
                      "byteLength": len(data), "target": target})
        return len(views) - 1

    n = len(pos) // 3
    v = add(struct.pack("<%df" % len(pos), *pos), 34962)
    accessors.append({"bufferView": v, "componentType": 5126, "count": n,
                      "type": "VEC3",
                      "min": [min(pos[i::3]) for i in range(3)],
                      "max": [max(pos[i::3]) for i in range(3)]})
    v = add(struct.pack("<%df" % len(nrm), *nrm), 34962)
    accessors.append({"bufferView": v, "componentType": 5126, "count": n, "type": "VEC3"})
    v = add(struct.pack("<%df" % len(uv), *uv), 34962)
    accessors.append({"bufferView": v, "componentType": 5126, "count": n, "type": "VEC2"})
    if n <= 65535:
        v = add(struct.pack("<%dH" % len(tris), *tris), 34963)
        ctype = 5123
    else:
        v = add(struct.pack("<%dI" % len(tris), *tris), 34963)
        ctype = 5125
    accessors.append({"bufferView": v, "componentType": ctype,
                      "count": len(tris), "type": "SCALAR"})

    mat = {
        "name": name + "_mat",
        "pbrMetallicRoughness": {
            "baseColorFactor": [1, 1, 1, 1],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.85,
        },
        "doubleSided": True,
    }
    gltf = {
        "asset": {"version": "2.0", "generator": "cargame forest_to_gltf.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [{"name": name, "primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
            "indices": 3, "material": 0}]}],
        "materials": [mat],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"uri": name + ".bin", "byteLength": len(buf)}],
    }
    if texture:
        gltf["images"] = [{"uri": "textures/" + texture}]
        gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987,
                             "wrapS": 10497, "wrapT": 10497}]
        gltf["textures"] = [{"source": 0, "sampler": 0}]
        mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
        if alpha_cut:
            mat["alphaMode"] = "MASK"
            mat["alphaCutoff"] = 0.5

    open(os.path.join(out_dir, name + ".bin"), "wb").write(bytes(buf))
    json.dump(gltf, open(os.path.join(out_dir, name + ".gltf"), "w"), indent=1)
    return n, len(tris) // 3


def main():
    src, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    tex_dir = os.path.join(out, "textures")
    os.makedirs(tex_dir, exist_ok=True)

    cache = {}
    summary = []
    for name, (fbx, geo, scale) in WANTED.items():
        path = os.path.join(src, fbx)
        if not os.path.exists(path):
            print("missing", fbx)
            continue
        if fbx not in cache:
            cache[fbx] = collect(path)
        meshes = cache[fbx]
        if geo not in meshes:
            print("!! %s not found in %s (have %s)" % (geo, fbx, list(meshes)[:6]))
            continue
        pos, nrm, uv, tris = build_mesh(meshes[geo], scale)
        if not tris:
            print("!! %s produced no triangles" % name)
            continue
        height = max(pos[1::3]) - min(pos[1::3])
        v, t = write_gltf(name, pos, nrm, uv, tris, out)
        summary.append((name, v, t, height))
        print("%-14s %6d verts %6d tris   height %.2f m" % (name, v, t, height))

    json.dump({"assets": [{"name": n, "verts": v, "tris": t, "height": h}
                          for n, v, t, h in summary]},
              open(os.path.join(out, "forest_manifest.json"), "w"), indent=1)
    print("\n%d assets written to %s" % (len(summary), out))


if __name__ == "__main__":
    main()
