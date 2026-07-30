#!/usr/bin/env python3
"""
Reduce the triangle count of a glTF mesh by welding vertices on a grid.

Why this exists. untitled.fbx contains three species the map was missing -
two grasses and the only flower in any of the uploaded assets - but they are
authored as dense clumps:

    grass_wide   2,298 tris for a 0.22 m plant   (19x the existing tuft)
    grass_fine   1,467 tris
    daisy          448 tris

Scattered at the counts the other ground cover uses, that would add millions
of triangles for plants a few pixels tall. Godot's importer generates mesh
LODs, but LOD only helps at distance - the near band still pays full price,
and MultiMesh instances all share one mesh, so the base cost matters.

The approach is vertex clustering (Rossignac & Borrel, 1993): snap every
vertex to a grid, merge the ones that land in the same cell, and drop the
triangles that collapse to a line or a point. It is crude next to
quadric-error simplification, but for foliage - where the silhouette is
noise and nobody can see an individual blade - it is exactly right, and it
cannot produce the non-manifold artefacts that edge-collapse can.

Attributes are averaged per cell, and normals are renormalised afterwards so
the result stays lit correctly.

Usage:
    python3 tools/mesh_decimate.py in.gltf out.gltf <cell_size_metres>
"""

import json
import math
import os
import struct
import sys


COMPONENT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_accessor(gltf, blob, index):
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    fmt = COMPONENT[acc["componentType"]]
    n = NCOMP[acc["type"]]
    size = struct.calcsize(fmt) * n
    stride = view.get("byteStride") or size
    base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    out = []
    for i in range(acc["count"]):
        out.append(struct.unpack_from("<" + fmt * n, blob, base + i * stride))
    return out


def decimate(positions, normals, uvs, indices, cell):
    """Vertex clustering. Returns new (positions, normals, uvs, indices)."""
    # Assign every vertex to a grid cell.
    cluster_of = []
    cells = {}
    for p in positions:
        key = (int(math.floor(p[0] / cell)),
               int(math.floor(p[1] / cell)),
               int(math.floor(p[2] / cell)))
        if key not in cells:
            cells[key] = len(cells)
        cluster_of.append(cells[key])

    count = len(cells)
    acc_p = [[0.0, 0.0, 0.0] for _ in range(count)]
    acc_n = [[0.0, 0.0, 0.0] for _ in range(count)]
    acc_u = [[0.0, 0.0] for _ in range(count)]
    hits = [0] * count

    for i, p in enumerate(positions):
        c = cluster_of[i]
        hits[c] += 1
        for k in range(3):
            acc_p[c][k] += p[k]
        if normals:
            for k in range(3):
                acc_n[c][k] += normals[i][k]
        if uvs:
            for k in range(2):
                acc_u[c][k] += uvs[i][k]

    new_pos, new_nrm, new_uv = [], [], []
    for c in range(count):
        h = max(hits[c], 1)
        new_pos.append(tuple(v / h for v in acc_p[c]))
        if normals:
            n = acc_n[c]
            length = math.sqrt(sum(v * v for v in n))
            new_nrm.append(tuple(v / length for v in n) if length > 1e-9
                           else (0.0, 1.0, 0.0))
        if uvs:
            new_uv.append(tuple(v / h for v in acc_u[c]))

    # Keep only triangles whose three corners land in three different cells.
    new_idx = []
    for t in range(0, len(indices) - 2, 3):
        a = cluster_of[indices[t]]
        b = cluster_of[indices[t + 1]]
        c = cluster_of[indices[t + 2]]
        if a != b and b != c and a != c:
            new_idx.extend((a, b, c))

    return new_pos, new_nrm, new_uv, new_idx


def pack(values, fmt):
    out = bytearray()
    for v in values:
        out += struct.pack("<" + fmt * len(v), *v)
    return bytes(out)


def main():
    src, dst, cell = sys.argv[1], sys.argv[2], float(sys.argv[3])
    gltf = json.load(open(src))
    blob = open(os.path.splitext(src)[0] + ".bin", "rb").read()

    prim = gltf["meshes"][0]["primitives"][0]
    positions = read_accessor(gltf, blob, prim["attributes"]["POSITION"])
    normals = (read_accessor(gltf, blob, prim["attributes"]["NORMAL"])
               if "NORMAL" in prim["attributes"] else None)
    uvs = (read_accessor(gltf, blob, prim["attributes"]["TEXCOORD_0"])
           if "TEXCOORD_0" in prim["attributes"] else None)
    indices = [i[0] for i in read_accessor(gltf, blob, prim["indices"])]

    before = len(indices) // 3
    p, n, u, idx = decimate(positions, normals, uvs, indices, cell)
    after = len(idx) // 3
    if after == 0:
        print("  %s: cell %.3f collapsed everything, skipped" % (src, cell))
        return 1

    # Rebuild a minimal glTF around the new arrays.
    buf = bytearray()
    views, accessors = [], []

    def add(data, values, kind, comp, mn=None, mx=None, target=34962):
        nonlocal buf
        while len(buf) % 4:
            buf.append(0)
        offset = len(buf)
        buf += data
        views.append({"buffer": 0, "byteOffset": offset,
                      "byteLength": len(data), "target": target})
        acc = {"bufferView": len(views) - 1, "componentType": comp,
               "count": len(values), "type": kind}
        if mn:
            acc["min"], acc["max"] = mn, mx
        accessors.append(acc)
        return len(accessors) - 1

    mn = [min(v[i] for v in p) for i in range(3)]
    mx = [max(v[i] for v in p) for i in range(3)]
    a_pos = add(pack(p, "f"), p, "VEC3", 5126, mn, mx)
    attrs = {"POSITION": a_pos}
    if n:
        attrs["NORMAL"] = add(pack(n, "f"), n, "VEC3", 5126)
    if u:
        attrs["TEXCOORD_0"] = add(pack(u, "f"), u, "VEC2", 5126)
    a_idx = add(struct.pack("<%dI" % len(idx), *idx),
                [(i,) for i in idx], "SCALAR", 5125, target=34963)

    out = {
        "asset": {"version": "2.0", "generator": "mesh_decimate.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": gltf["nodes"][0].get("name", "mesh")}],
        "meshes": [{"name": gltf["meshes"][0].get("name", "mesh"),
                    "primitives": [{"attributes": attrs, "indices": a_idx,
                                    "material": 0}]}],
        "materials": gltf.get("materials", [{"name": "plant"}]),
        "bufferViews": views,
        "accessors": accessors,
        "buffers": [{"uri": os.path.basename(os.path.splitext(dst)[0]) + ".bin",
                     "byteLength": len(buf)}],
    }
    json.dump(out, open(dst, "w"), separators=(",", ":"))
    open(os.path.splitext(dst)[0] + ".bin", "wb").write(bytes(buf))

    print("  %-12s %6d -> %5d tris (%.0f%% off), %d -> %d verts, cell %.3f m"
          % (os.path.basename(dst).replace(".gltf", ""), before, after,
             100.0 * (before - after) / before, len(positions), len(p), cell))
    return 0


if __name__ == "__main__":
    sys.exit(main())
