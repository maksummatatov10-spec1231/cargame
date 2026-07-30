#!/usr/bin/env python3
"""
Convert the Okhey GHammer pickup FBX into a game-ready glTF.

Analysis of the source (tools/asset_report.py):

  * FBX 7400, 29 MB, 52 meshes, 775 983 verts, 490 468 polys
  * no animation, skinning, poses or takes - static geometry
  * node scale is -100 with a 90 deg X rotation, so the file is in centimetres
    and Z-up; the negative scale also mirrors it
  * the model lies along X: 5.50 m long, 2.23 m wide, 2.14 m tall
  * wheels are `Plano.001/003/005/007` (tyre) and `.002/004/006/008` (rim),
    radius 47 cm, centres at X +145 / -168 cm and Z +/-84 cm
  * wheelbase 3.13 m, track 1.68 m
  * the lowest point is 6.5 cm below the origin, so the whole model is lifted
    to put the tyre contact patch exactly on y = 0

At 490 k polys it is far too heavy to drive around, so the body is decimated by
grid clustering in the same way as the forest tree. The wheels are kept at full
detail because they are close to the camera and spin.

Output matches the layout the game expects: `body`, `wheel_lf/rf/lr/rr` and
`hub_*`, each with its pivot on the real hub centre.

Usage:
    python3 tools/pickup_to_gltf.py <pickup.fbx> <out_dir>
"""

import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbxparse as F  # noqa: E402

CM = 0.01

# Tyre and rim meshes for each corner, taken from the analysis above.
# FBX X is the length axis (+X is the front), Z is width (+Z is one side).
WHEEL_MESHES = {
    "rf": ["Plano.001", "Plano.002"],
    "lf": ["Plano.003", "Plano.004"],
    "lr": ["Plano.005", "Plano.006"],
    "rr": ["Plano.007", "Plano.008"],
}
# Suspension springs, kept with the hub so they move with the travel.
HUB_MESHES = {
    "rf": ["Cilindro.072"],
    "rr": ["Cilindro.222"],
    "lf": ["Cilindro.017"],
    "lr": ["Cilindro.140"],
}

# Vertex clustering cell in metres, per group. Larger = fewer triangles.
#
# The source is 490 k polys, which is roughly five times a modern game car and
# far too heavy to have four of on screen. The body tolerates a coarse cell
# because it is mostly large flat panels; the wheels need a finer one because
# they are round, close to the camera and spinning, so faceting shows.
BODY_CELL = 0.030
WHEEL_CELL = 0.012
HUB_CELL = 0.020


def node_matrix(model):
    p = F.props70(model)
    t = p.get("Lcl Translation", [0.0, 0.0, 0.0])
    r = p.get("Lcl Rotation", [0.0, 0.0, 0.0])
    s = p.get("Lcl Scaling", [1.0, 1.0, 1.0])
    rx, ry, rz = (math.radians(v) for v in r)

    def rot_x(a):
        return [[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]

    def rot_y(a):
        return [[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]]

    def rot_z(a):
        return [[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]

    rot = mul(mul(rot_z(rz), rot_y(ry)), rot_x(rx))
    mat = [[rot[i][j] * s[j] for j in range(3)] for i in range(3)]
    return mat, t


def read_scene(path):
    nodes = F.parse(path)
    top = {n[0]: n for n in nodes}
    objs = top["Objects"]
    conns = top["Connections"]

    models = {m[1][0]: m for m in objs[2] if m[0] == "Model"}
    geos = {g[1][0]: g for g in objs[2] if g[0] == "Geometry"}
    mats = {m[1][0]: m[1][1].split("\x00")[0] for m in objs[2] if m[0] == "Material"}

    geo_model = {}
    model_mats = {}
    for c in conns[2]:
        if c[1][0] != "OO":
            continue
        src, dst = c[1][1], c[1][2]
        if src in geos and dst in models:
            geo_model[src] = dst
        elif src in mats and dst in models:
            model_mats.setdefault(dst, []).append(mats[src])
    return models, geos, geo_model, model_mats


def extract_triangles(geo, mat, trans):
    """World-space triangles in Godot axes, in metres."""
    verts = idx = normals = uvs = uvidx = None
    nmap = umap = uref = None
    for ch in geo[2]:
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
    if not verts or not idx:
        return []

    # The node scale is negative, which mirrors the mesh and flips the winding.
    mirrored = (mat[0][0] * mat[1][1] * mat[2][2]) < 0.0

    def place(vi):
        a, b, c = verts[vi * 3], verts[vi * 3 + 1], verts[vi * 3 + 2]
        x = mat[0][0] * a + mat[0][1] * b + mat[0][2] * c + trans[0]
        y = mat[1][0] * a + mat[1][1] * b + mat[1][2] * c + trans[1]
        z = mat[2][0] * a + mat[2][1] * b + mat[2][2] * c + trans[2]
        # cm -> m, and the model faces +X while Godot faces -Z: rotate 90 deg
        # about Y so +X becomes -Z.
        return (-z * CM, y * CM, -x * CM)

    def normal_of(ni):
        if not normals or (ni + 1) * 3 > len(normals):
            return None
        a, b, c = normals[ni * 3], normals[ni * 3 + 1], normals[ni * 3 + 2]
        x = mat[0][0] * a + mat[0][1] * b + mat[0][2] * c
        y = mat[1][0] * a + mat[1][1] * b + mat[1][2] * c
        z = mat[2][0] * a + mat[2][1] * b + mat[2][2] * c
        v = (-z, y, -x)
        ln = math.sqrt(sum(q * q for q in v))
        if ln < 1e-9:
            return None
        return tuple(q / ln for q in v)

    out = []
    poly = []
    for k, raw in enumerate(idx):
        last = raw < 0
        vi = ~raw if last else raw
        poly.append((k, vi))
        if not last:
            continue
        corners = []
        for k2, vi2 in poly:
            p = place(vi2)
            n = normal_of(k2 if nmap == "ByPolygonVertex" else vi2) or (0.0, 1.0, 0.0)
            if uvs:
                ui = k2 if umap == "ByPolygonVertex" else vi2
                if uref == "IndexToDirect" and uvidx and ui < len(uvidx):
                    ui = uvidx[ui]
                if 0 <= ui and (ui + 1) * 2 <= len(uvs):
                    t = (uvs[ui * 2], 1.0 - uvs[ui * 2 + 1])
                else:
                    t = (0.0, 0.0)
            else:
                t = (0.0, 0.0)
            corners.append((p, n, t))
        for i in range(1, len(corners) - 1):
            tri = (corners[0], corners[i], corners[i + 1])
            if mirrored:
                tri = (tri[0], tri[2], tri[1])
            out.append(tri)
        poly = []
    return out


def cluster(tris, cell):
    """Grid-cluster vertex welding. Returns (pos, nrm, uv, indices)."""
    reps = {}
    for tri in tris:
        for p, n, t in tri:
            key = (round(p[0] / cell), round(p[1] / cell), round(p[2] / cell))
            e = reps.get(key)
            if e is None:
                reps[key] = [list(p), list(n), list(t), 1]
            else:
                for i in range(3):
                    e[0][i] += p[i]
                    e[1][i] += n[i]
                for i in range(2):
                    e[2][i] += t[i]
                e[3] += 1

    order = {}
    pos, nrm, uv = [], [], []
    for key, e in reps.items():
        order[key] = len(pos) // 3
        c = e[3]
        pos.extend(v / c for v in e[0])
        n = [v / c for v in e[1]]
        ln = math.sqrt(sum(v * v for v in n)) or 1.0
        nrm.extend(v / ln for v in n)
        uv.extend(v / c for v in e[2])

    indices = []
    for tri in tris:
        ids = []
        for p, _n, _t in tri:
            ids.append(order[(round(p[0] / cell), round(p[1] / cell),
                              round(p[2] / cell))])
        if len(set(ids)) < 3:
            continue
        # Clustering can collapse a triangle to zero area even when its three
        # vertices are distinct. Godot reports those as "face with non-finite
        # normal" during LOD generation, so they are dropped here.
        a = [pos[ids[0] * 3 + i] for i in range(3)]
        b = [pos[ids[1] * 3 + i] for i in range(3)]
        c = [pos[ids[2] * 3 + i] for i in range(3)]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        cr = (u[1] * v[2] - u[2] * v[1],
              u[2] * v[0] - u[0] * v[2],
              u[0] * v[1] - u[1] * v[0])
        if math.sqrt(sum(q * q for q in cr)) < 1e-12:
            continue
        indices.extend(ids)
    _repair_normals(pos, nrm, indices)
    return pos, nrm, uv, indices


def _repair_normals(pos, nrm, indices):
    """Replaces zero-length averaged normals with a real face normal.

    Averaging the normals inside a cluster can cancel them out where a thin
    panel folds back on itself, leaving a zero vector. Those render black and
    make Godot complain, so each one is rebuilt from a triangle that uses it.
    """
    broken = set()
    for i in range(len(nrm) // 3):
        n = nrm[i * 3:i * 3 + 3]
        if math.sqrt(sum(q * q for q in n)) < 1e-6:
            broken.add(i)
    if not broken:
        return
    for k in range(0, len(indices), 3):
        ids = indices[k:k + 3]
        if not broken.intersection(ids):
            continue
        a = [pos[ids[0] * 3 + i] for i in range(3)]
        b = [pos[ids[1] * 3 + i] for i in range(3)]
        c = [pos[ids[2] * 3 + i] for i in range(3)]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        cr = [u[1] * v[2] - u[2] * v[1],
              u[2] * v[0] - u[0] * v[2],
              u[0] * v[1] - u[1] * v[0]]
        ln = math.sqrt(sum(q * q for q in cr))
        if ln < 1e-12:
            continue
        for vid in ids:
            if vid in broken:
                for i in range(3):
                    nrm[vid * 3 + i] = cr[i] / ln
                broken.discard(vid)
        if not broken:
            break
    # Anything still broken has no usable neighbour; point it up.
    for vid in broken:
        nrm[vid * 3] = 0.0
        nrm[vid * 3 + 1] = 1.0
        nrm[vid * 3 + 2] = 0.0


def weld_exact(tris):
    """Weld identical vertices only - used where detail must be preserved."""
    seen = {}
    pos, nrm, uv, indices = [], [], [], []
    for tri in tris:
        for p, n, t in tri:
            key = (round(p[0], 5), round(p[1], 5), round(p[2], 5),
                   round(n[0], 3), round(n[1], 3), round(n[2], 3),
                   round(t[0], 4), round(t[1], 4))
            i = seen.get(key)
            if i is None:
                i = len(pos) // 3
                seen[key] = i
                pos.extend(p)
                nrm.extend(n)
                uv.extend(t)
            indices.append(i)
    return pos, nrm, uv, indices


def main():
    src, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    models, geos, geo_model, model_mats = read_scene(src)

    wheel_of = {}
    for corner, names in WHEEL_MESHES.items():
        for n in names:
            wheel_of[n] = "wheel_" + corner
    for corner, names in HUB_MESHES.items():
        for n in names:
            wheel_of[n] = "hub_" + corner

    groups = {}
    lowest = 1e9
    for gid, geo in geos.items():
        mid = geo_model.get(gid)
        if mid is None:
            continue
        name = models[mid][1][1].split("\x00")[0]
        mat, trans = node_matrix(models[mid])
        tris = extract_triangles(geo, mat, trans)
        if not tris:
            continue
        group = wheel_of.get(name, "body")
        groups.setdefault(group, []).extend(tris)
        for tri in tris:
            for p, _n, _t in tri:
                lowest = min(lowest, p[1])

    # Lift so the tyres sit exactly on y = 0.
    lift = -lowest
    print("lifting model by %.3f m so the tyres touch the ground" % lift)

    pivots = {}
    for group, tris in groups.items():
        xs = [p[0] for tri in tris for p, _n, _t in tri]
        ys = [p[1] + lift for tri in tris for p, _n, _t in tri]
        zs = [p[2] for tri in tris for p, _n, _t in tri]
        if group == "body":
            pivots[group] = (0.0, 0.0, 0.0)
        else:
            pivots[group] = ((min(xs) + max(xs)) * 0.5,
                             (min(ys) + max(ys)) * 0.5,
                             (min(zs) + max(zs)) * 0.5)

    # Hubs share the wheel pivot so both rotate about the same axle.
    for corner in ("lf", "rf", "lr", "rr"):
        w = "wheel_" + corner
        h = "hub_" + corner
        if w in pivots and h in pivots:
            pivots[h] = pivots[w]

    written = {}
    for group, tris in groups.items():
        px, py, pz = pivots[group]
        moved = []
        for tri in tris:
            moved.append(tuple(((p[0] - px, p[1] + lift - py, p[2] - pz), n, t)
                               for p, n, t in tri))
        if group == "body":
            pos, nrm, uv, idx = cluster(moved, BODY_CELL)
        elif group.startswith("wheel_"):
            pos, nrm, uv, idx = cluster(moved, WHEEL_CELL)
        else:
            pos, nrm, uv, idx = cluster(moved, HUB_CELL)
        if not idx:
            continue
        write_gltf(group, pos, nrm, uv, idx, out_dir)
        written[group] = (len(pos) // 3, len(idx) // 3)
        print("%-12s %7d verts %7d tris  pivot (%.3f, %.3f, %.3f)"
              % (group, len(pos) // 3, len(idx) // 3, px, py, pz))

    # Collision: slab decomposition of the body shell, same approach as the
    # BMW. The interior is excluded by taking only the outermost points in
    # each slab, so the hulls hug the bodywork.
    body_pts = []
    for tri in groups.get("body", []):
        for p, _n, _t in tri:
            body_pts.append((p[0], p[1] + lift, p[2]))
    hulls = slab_hulls(body_pts, 9)

    meta = {
        "pivots": {k: list(v) for k, v in pivots.items()},
        "counts": {k: {"verts": v[0], "tris": v[1]} for k, v in written.items()},
        "body_shapes": hulls,
        "wheel_positions": {c: list(pivots["wheel_" + c])
                            for c in ("lf", "rf", "lr", "rr")
                            if "wheel_" + c in pivots},
        "body_aabb": {
            "min": [min(p[i] for p in body_pts) for i in range(3)],
            "max": [max(p[i] for p in body_pts) for i in range(3)],
        },
    }
    print("collision: %d hulls, %d points"
          % (len(hulls), sum(len(h) for h in hulls)))
    json.dump(meta, open(os.path.join(out_dir, "pickup_meta.json"), "w"), indent=1)
    total = sum(v[1] for v in written.values())
    print("\ntotal %d tris (source was 490468)" % total)


def slab_hulls(points, slabs):
    """Convex decomposition: split along the length, take support points."""
    if not points:
        return []
    zmin = min(p[2] for p in points)
    zmax = max(p[2] for p in points)
    span = (zmax - zmin) / slabs
    out = []
    for i in range(slabs):
        lo = zmin + i * span
        hi = lo + span
        sel = [p for p in points if lo - 0.02 <= p[2] <= hi + 0.02]
        if len(sel) < 8:
            continue
        out.append(support_cloud(sel, 90))
    return out


def support_cloud(points, ndirs):
    """Extreme points along evenly spread directions - a convex hull sample."""
    found = {}
    ga = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(ndirs):
        y = 1.0 - (i / float(ndirs - 1)) * 2.0
        r = math.sqrt(max(0.0, 1.0 - y * y))
        th = ga * i
        d = (math.cos(th) * r, y, math.sin(th) * r)
        best = None
        bv = -1e18
        for p in points:
            v = p[0] * d[0] + p[1] * d[1] + p[2] * d[2]
            if v > bv:
                bv, best = v, p
        found[(round(best[0], 4), round(best[1], 4), round(best[2], 4))] = True
    return [list(k) for k in found]


def write_gltf(name, pos, nrm, uv, tris, out_dir):
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

    is_wheel = name.startswith("wheel_")
    base = [0.07, 0.07, 0.075, 1.0] if is_wheel else [0.42, 0.44, 0.48, 1.0]
    gltf = {
        "asset": {"version": "2.0", "generator": "cargame pickup_to_gltf.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [{"name": name, "primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
            "indices": 3, "material": 0}]}],
        "materials": [{
            "name": name + "_mat",
            "pbrMetallicRoughness": {
                "baseColorFactor": base,
                "metallicFactor": 0.0 if is_wheel else 0.45,
                "roughnessFactor": 0.9 if is_wheel else 0.35,
            },
            "doubleSided": False,
        }],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"uri": name + ".bin", "byteLength": len(buf)}],
    }
    open(os.path.join(out_dir, name + ".bin"), "wb").write(bytes(buf))
    json.dump(gltf, open(os.path.join(out_dir, name + ".gltf"), "w"), indent=1)


if __name__ == "__main__":
    main()
