#!/usr/bin/env python3
"""
Convert the Adventure Ready Defender 110 FBX into a game-ready glTF.

Analysis of the source (tools/asset_report.py plus the geometry work below):

  * FBX 7400, 45 MB, 590 nodes, 571 meshes, 857 242 verts, 860 352 polys
  * no animation, skinning, poses or takes - static geometry, like every other
    asset in this project
  * every node is called `desirefx.me_NNN`, so nothing can be identified by
    name; the parts were found geometrically instead
  * the model is in arbitrary units and yawed 2.90 deg in plan

Wheels were located by looking for round parts (two similar large extents, one
much smaller) that appear four times at mirrored positions:

      front axle  x = +13.11        rear axle  x = -14.70
      wheelbase   27.84 units       track      18.39 units
      tyre outer  8.65 units diameter, centre at y = 7.93

A real Defender 110 has a 2.79 m wheelbase, which fixes the scale at
2.79 / 27.84 = 0.10021. That gives a 0.867 m tyre (255/70R16 is 0.78 m) and a
4.71 x 2.30 x 2.47 m body against the real 4.76 x 1.97 x 1.97 - the right size
for a Defender with a roof rack and off-road tyres, which this one has.

Output matches the layout the game expects: `body`, `wheel_lf/rf/lr/rr`,
`hub_*`, each pivoted on its real hub centre, Y-up, facing -Z, sitting on y=0.

Usage:
    python3 tools/defender_to_gltf.py <defender.fbx> <out_dir>
"""

import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbxparse as F  # noqa: E402

# Derived above: real wheelbase / measured wheelbase.
SCALE = 2.79 / 27.84
# The model is yawed in plan; this straightens it before the axis swap.
YAW_FIX = math.radians(2.90)
# The model is not built around its own origin: the mean of the four hub
# centres sits at (-0.797, *, 3.770) in source units. Subtracting that puts the
# car on the origin, so the wheels come out symmetric about x = 0 and the
# centre of mass lands where the physics expects it.
ORIGIN_X = -0.797
ORIGIN_Z = 3.770

# Hub centres in source units, from the wheel search.
WHEEL_CENTRES = {
    "rf": (13.57, 7.93, 12.25),
    "lf": (12.64, 7.93, -6.12),
    "rr": (-14.23, 7.93, 13.66),
    "lr": (-15.17, 7.93, -4.71),
}
# Anything within this distance of a hub centre belongs to that corner. The
# tyre is 8.65 units across, so 4.6 captures the wheel and its brake without
# reaching into the arches and dragging body panels along with it.
WHEEL_RADIUS_UNITS = 4.6
# Parts inside the hub radius but small are brake and hub hardware, not tyre.
TYRE_MIN_EXTENT = 5.5
# Wheel parts must also be small overall: a body panel that happens to pass
# near the hub is much longer than the tyre.
WHEEL_MAX_EXTENT = 10.0

BODY_CELL = 0.028
WHEEL_CELL = 0.011


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
    return [[rot[i][j] * s[j] for j in range(3)] for i in range(3)], list(t)


def read_scene(path):
    nodes = F.parse(path)
    top = {n[0]: n for n in nodes}
    objs = top["Objects"]
    conns = top["Connections"]
    models = {m[1][0]: m for m in objs[2] if m[0] == "Model"}
    geos = {g[1][0]: g for g in objs[2] if g[0] == "Geometry"}
    geo_model = {}
    parent = {}
    for c in conns[2]:
        if c[1][0] != "OO":
            continue
        src, dst = c[1][1], c[1][2]
        if src in geos and dst in models:
            geo_model[src] = dst
        elif src in models:
            parent[src] = dst
    return models, geos, geo_model, parent


def world_matrix(mid, models, parent, cache):
    if mid in cache:
        return cache[mid]
    mat, trans = node_matrix(models[mid])
    p = parent.get(mid)
    if p in models:
        pm, pt = world_matrix(p, models, parent, cache)
        trans = [sum(pm[i][k] * trans[k] for k in range(3)) + pt[i] for i in range(3)]
        mat = [[sum(pm[i][k] * mat[k][j] for k in range(3)) for j in range(3)]
               for i in range(3)]
    cache[mid] = (mat, trans)
    return cache[mid]


def to_game_space(x, y, z):
    """Source units -> metres, centred, yaw corrected, Y-up, facing -Z."""
    x -= ORIGIN_X
    z -= ORIGIN_Z
    c, s = math.cos(YAW_FIX), math.sin(YAW_FIX)
    xr = x * c - z * s
    zr = x * s + z * c
    # The model runs along X with the front at +X; Godot's forward is -Z.
    return (zr * SCALE, y * SCALE, -xr * SCALE)


def extract(geo, mat, trans):
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
        return [], None

    mirrored = (mat[0][0] * mat[1][1] * mat[2][2]) < 0.0

    def place(vi):
        a, b, c = verts[vi * 3], verts[vi * 3 + 1], verts[vi * 3 + 2]
        x = mat[0][0] * a + mat[0][1] * b + mat[0][2] * c + trans[0]
        y = mat[1][0] * a + mat[1][1] * b + mat[1][2] * c + trans[1]
        z = mat[2][0] * a + mat[2][1] * b + mat[2][2] * c + trans[2]
        return (x, y, z)

    def normal_at(ni):
        if not normals or (ni + 1) * 3 > len(normals):
            return None
        a, b, c = normals[ni * 3], normals[ni * 3 + 1], normals[ni * 3 + 2]
        x = mat[0][0] * a + mat[0][1] * b + mat[0][2] * c
        y = mat[1][0] * a + mat[1][1] * b + mat[1][2] * c
        z = mat[2][0] * a + mat[2][1] * b + mat[2][2] * c
        cs, sn = math.cos(YAW_FIX), math.sin(YAW_FIX)
        xr = x * cs - z * sn
        zr = x * sn + z * cs
        v = (zr, y, -xr)
        ln = math.sqrt(sum(q * q for q in v))
        return None if ln < 1e-9 else tuple(q / ln for q in v)

    tris = []
    raw_pts = []
    poly = []
    for k, raw in enumerate(idx):
        last = raw < 0
        vi = ~raw if last else raw
        poly.append((k, vi))
        if not last:
            continue
        corners = []
        for k2, vi2 in poly:
            src = place(vi2)
            raw_pts.append(src)
            p = to_game_space(*src)
            n = normal_at(k2 if nmap == "ByPolygonVertex" else vi2) or (0.0, 1.0, 0.0)
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
            tris.append(tri)
        poly = []

    xs = [p[0] for p in raw_pts]
    ys = [p[1] for p in raw_pts]
    zs = [p[2] for p in raw_pts]
    bbox = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    return tris, bbox


def classify(bbox):
    """Which group a part belongs to, from where it sits in source units."""
    cx = (bbox[0] + bbox[1]) * 0.5
    cy = (bbox[2] + bbox[3]) * 0.5
    cz = (bbox[4] + bbox[5]) * 0.5
    extent = max(bbox[1] - bbox[0], bbox[3] - bbox[2], bbox[5] - bbox[4])
    if extent > WHEEL_MAX_EXTENT:
        return "body"          # too big to be part of a wheel
    for corner, (wx, wy, wz) in WHEEL_CENTRES.items():
        if math.dist((cx, cy, cz), (wx, wy, wz)) < WHEEL_RADIUS_UNITS:
            # Big round things are the wheel; small ones are brake and hub
            # hardware that should follow the travel but not spin.
            return ("wheel_" + corner) if extent >= TYRE_MIN_EXTENT else ("hub_" + corner)
    return "body"


def cluster(tris, cell):
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
        ids = [order[(round(p[0] / cell), round(p[1] / cell), round(p[2] / cell))]
               for p, _n, _t in tri]
        if len(set(ids)) < 3:
            continue
        indices.extend(ids)
    return pos, nrm, uv, indices


def slab_hulls(points, slabs=9):
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
        if len(sel) >= 8:
            out.append(support_cloud(sel, 90))
    return out


def support_cloud(points, ndirs):
    found = {}
    ga = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(ndirs):
        y = 1.0 - (i / float(ndirs - 1)) * 2.0
        r = math.sqrt(max(0.0, 1.0 - y * y))
        th = ga * i
        d = (math.cos(th) * r, y, math.sin(th) * r)
        best, bv = None, -1e18
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
    base = [0.06, 0.06, 0.065, 1.0] if is_wheel else [0.55, 0.58, 0.54, 1.0]
    gltf = {
        "asset": {"version": "2.0", "generator": "cargame defender_to_gltf.py"},
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
                "metallicFactor": 0.0 if is_wheel else 0.35,
                "roughnessFactor": 0.92 if is_wheel else 0.45,
            },
            "doubleSided": False,
        }],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"uri": name + ".bin", "byteLength": len(buf)}],
    }
    open(os.path.join(out_dir, name + ".bin"), "wb").write(bytes(buf))
    json.dump(gltf, open(os.path.join(out_dir, name + ".gltf"), "w"), indent=1)


def main():
    src, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    models, geos, geo_model, parent = read_scene(src)
    cache = {}

    groups = {}
    lowest = 1e9
    for gid, geo in geos.items():
        mid = geo_model.get(gid)
        if mid is None:
            continue
        mat, trans = world_matrix(mid, models, parent, cache)
        tris, bbox = extract(geo, mat, trans)
        if not tris:
            continue
        group = classify(bbox)
        groups.setdefault(group, []).extend(tris)
        for tri in tris:
            for p, _n, _t in tri:
                lowest = min(lowest, p[1])

    lift = -lowest
    print("lifting by %.3f m so the tyres sit on y = 0" % lift)

    pivots = {}
    for group, tris in groups.items():
        if group == "body":
            pivots[group] = (0.0, 0.0, 0.0)
            continue
        xs = [p[0] for tri in tris for p, _n, _t in tri]
        ys = [p[1] + lift for tri in tris for p, _n, _t in tri]
        zs = [p[2] for tri in tris for p, _n, _t in tri]
        pivots[group] = ((min(xs) + max(xs)) * 0.5,
                         (min(ys) + max(ys)) * 0.5,
                         (min(zs) + max(zs)) * 0.5)
    for corner in ("lf", "rf", "lr", "rr"):
        w, h = "wheel_" + corner, "hub_" + corner
        if w in pivots and h in pivots:
            pivots[h] = pivots[w]

    counts = {}
    for group, tris in groups.items():
        px, py, pz = pivots[group]
        moved = [tuple(((p[0] - px, p[1] + lift - py, p[2] - pz), n, t)
                       for p, n, t in tri) for tri in tris]
        cell = BODY_CELL if group == "body" else WHEEL_CELL
        pos, nrm, uv, idx = cluster(moved, cell)
        if not idx:
            continue
        write_gltf(group, pos, nrm, uv, idx, out_dir)
        counts[group] = {"verts": len(pos) // 3, "tris": len(idx) // 3}
        print("%-12s %7d verts %7d tris  pivot (%.3f, %.3f, %.3f)"
              % (group, len(pos) // 3, len(idx) // 3, px, py, pz))

    body_pts = [(p[0], p[1] + lift, p[2])
                for tri in groups.get("body", []) for p, _n, _t in tri]
    hulls = slab_hulls(body_pts)
    meta = {
        "pivots": {k: list(v) for k, v in pivots.items()},
        "counts": counts,
        "body_shapes": hulls,
        "wheel_positions": {c: list(pivots["wheel_" + c])
                            for c in ("lf", "rf", "lr", "rr")
                            if "wheel_" + c in pivots},
        "body_aabb": {"min": [min(p[i] for p in body_pts) for i in range(3)],
                      "max": [max(p[i] for p in body_pts) for i in range(3)]},
    }
    json.dump(meta, open(os.path.join(out_dir, "defender_meta.json"), "w"), indent=1)
    print("collision: %d hulls, %d points" % (len(hulls), sum(len(h) for h in hulls)))
    print("total %d tris (source 860352)" % sum(c["tris"] for c in counts.values()))


if __name__ == "__main__":
    main()
