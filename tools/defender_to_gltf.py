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

# Paintwork colour for materials whose name says "paint" or "body" but whose
# exported diffuse is the usual Max black. A dark olive green is what an
# "Adventure Ready" Defender is normally finished in.
BODY_COLOUR = [0.16, 0.22, 0.17, 1.0]

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


def read_materials(objs):
    """{id: (name, diffuse rgb, shininess)} for every Material in the file.

    The Defender ships 70 of these and the converter used to throw all of
    them away, replacing the lot with one flat grey - which is why the truck
    rendered as a single blue-grey blob. Each of its 570 meshes uses exactly
    one material (LayerElementMaterial mapping is AllSame), so the mapping
    back is unambiguous.
    """
    out = {}
    for nm, props, kids in objs[2]:
        if nm != "Material" or len(props) < 2:
            continue
        colour = (0.6, 0.6, 0.6)
        shininess = 16.0
        for cn, _cp, ck in kids:
            if cn != "Properties70":
                continue
            for _pn, pp, _pk in ck:
                if not pp:
                    continue
                key = str(pp[0])
                if key in ("DiffuseColor", "Diffuse"):
                    colour = tuple(float(x) for x in pp[-3:])
                elif key in ("Shininess", "ShininessExponent"):
                    shininess = float(pp[-1])
        out[props[0]] = (str(props[1]).split("\x00")[0], colour, shininess)
    return out


def read_scene(path):
    nodes = F.parse(path)
    top = {n[0]: n for n in nodes}
    objs = top["Objects"]
    conns = top["Connections"]
    models = {m[1][0]: m for m in objs[2] if m[0] == "Model"}
    geos = {g[1][0]: g for g in objs[2] if g[0] == "Geometry"}
    materials = read_materials(objs)
    geo_model = {}
    model_mat = {}
    parent = {}
    for c in conns[2]:
        if c[1][0] != "OO":
            continue
        src, dst = c[1][1], c[1][2]
        if src in geos and dst in models:
            geo_model[src] = dst
        elif src in materials and dst in models:
            model_mat[dst] = src
        elif src in models:
            parent[src] = dst
    return models, geos, geo_model, parent, materials, model_mat


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


# How a material name maps to a believable PBR surface.
#
# The FBX diffuse colours are nearly all black (0.0-0.1) because these are
# 3ds Max scenes where the visible colour came from a shader network that
# does not survive FBX export. Using them directly gives a black truck.
# The material NAMES did survive, and they are descriptive, so they are what
# the classification keys off - falling back to the exported colour only
# when a name says nothing.
#
# token -> (base colour, metallic, roughness)
SURFACE_BY_NAME = [
    ("glass",     ([0.14, 0.17, 0.20, 0.45], 0.0, 0.05)),
    ("vidro",     ([0.14, 0.17, 0.20, 0.45], 0.0, 0.05)),
    ("chrome",    ([0.86, 0.88, 0.91, 1.0], 1.0, 0.10)),
    # "silver" on the pickup covers 34,781 triangles spanning the entire
    # 5.5 m body - that is the panelwork, not trim, and rendering the whole
    # truck as polished chrome looks wrong. Treated as a light metallic
    # paint: still clearly metal, but a body finish rather than a mirror.
    ("silver",    ([0.55, 0.57, 0.60, 1.0], 0.65, 0.38)),
    ("aro",       ([0.66, 0.68, 0.71, 1.0], 0.95, 0.28)),
    ("metal",     ([0.42, 0.44, 0.47, 1.0], 0.85, 0.40)),
    ("mtl",       ([0.42, 0.44, 0.47, 1.0], 0.85, 0.40)),
    ("steel",     ([0.48, 0.50, 0.53, 1.0], 0.90, 0.35)),
    ("light",     ([0.90, 0.88, 0.80, 1.0], 0.0, 0.15)),
    ("lamp",      ([0.90, 0.88, 0.80, 1.0], 0.0, 0.15)),
    ("faro",      ([0.90, 0.88, 0.80, 1.0], 0.0, 0.15)),
    ("tyre",      ([0.055, 0.055, 0.060, 1.0], 0.0, 0.95)),
    ("tire",      ([0.055, 0.055, 0.060, 1.0], 0.0, 0.95)),
    ("brc",       ([0.055, 0.055, 0.060, 1.0], 0.0, 0.95)),
    ("rubber",    ([0.06, 0.06, 0.065, 1.0], 0.0, 0.93)),
    ("spring",    ([0.20, 0.16, 0.14, 1.0], 0.7, 0.60)),
    ("plastic",   ([0.10, 0.10, 0.11, 1.0], 0.0, 0.65)),
    ("interior",  ([0.09, 0.09, 0.10, 1.0], 0.0, 0.80)),
    ("seat",      ([0.13, 0.11, 0.10, 1.0], 0.0, 0.85)),
    ("paint",     (None, 0.35, 0.32)),
    ("body",      (None, 0.35, 0.32)),
    ("base",      (None, 0.30, 0.42)),
]


def surface_by_shape(extent, centre_y, tris, body_extent, rank):
    """Classify a submesh from its geometry when its name says nothing.

    The Defender's 70 materials are called "Material #1958" and so on - the
    names carry no meaning, so name matching alone leaves the whole truck a
    uniform grey. Shape and size do carry meaning. Measured on the actual
    submeshes:

        29,433 tris  2.04 x 1.26 x 3.93 m   the body shell
        12,487 tris  1.65 x 1.66 x 4.12 m   arches, sills, underbody
         8,804 tris  1.51 x 0.46 x 2.56 m   roof rack
         2,474 tris  1.36 x 1.32 x 0.29 m   a thin upright pane - glazing

    `rank` is the index of this submesh when sorted by triangle count, which
    is what distinguishes "the shell" from "everything else large".
    """
    width, height, length = extent
    body_w, _body_h, body_l = body_extent
    spans_body = width > body_w * 0.55 and length > body_l * 0.55

    # A pane: thin in one axis, broad in the others, and sitting high.
    thin = min(extent) < 0.35
    if thin and centre_y > 1.0 and tris < 6000 and max(extent) > 0.8:
        return [0.13, 0.16, 0.19, 0.42], 0.0, 0.05

    # The shell is simply the biggest thing that wraps the vehicle.
    if rank == 0 or (spans_body and tris > 10000):
        return None, 0.35, 0.34

    # Painted structure: large, spans the body, sits at body height.
    if spans_body and centre_y > 0.7:
        return None, 0.30, 0.45

    # Low-slung parts are chassis, axles and guards.
    if centre_y < 0.8:
        return [0.075, 0.075, 0.08, 1.0], 0.55, 0.72

    # Fittings: racks, mirrors, lamp guards, hinges.
    return [0.13, 0.135, 0.14, 1.0], 0.60, 0.45


def surface_for(name, colour, body_colour):
    """Turn an FBX material into a glTF PBR material."""
    lowered = name.lower()
    for token, (base, metallic, rough) in SURFACE_BY_NAME:
        if token in lowered:
            if base is None:
                return list(body_colour), metallic, rough
            return list(base), metallic, rough

    # No hint in the name: use the exported diffuse, but lift it out of the
    # near-black range so the part is visible at all. Anything below 0.12 is
    # treated as "unset" rather than as a deliberate black.
    level = max(colour)
    if level < 0.12:
        grey = 0.16 + level * 2.0
        return [grey, grey, grey * 1.02, 1.0], 0.25, 0.55
    return [colour[0], colour[1], colour[2], 1.0], 0.25, 0.50


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


def write_gltf(name, parts, out_dir, body_colour, body_extent=(2.0, 2.0, 4.5)):
    """Write one glTF with one primitive per material.

    `parts` is [(material name, diffuse, shininess, pos, nrm, uv, idx), ...].
    Splitting by material is the whole point: the old version merged every
    surface into a single primitive with one flat colour, which is why the
    Defender and the pickup rendered as featureless blue-grey blocks.
    """
    buf = bytearray()
    views = []
    accessors = []

    def add(data, target):
        while len(buf) % 4:
            buf.append(0)
        off = len(buf)
        buf.extend(data)
        views.append({"buffer": 0, "byteOffset": off,
                      "byteLength": len(data), "target": target})
        return len(views) - 1

    primitives = []
    materials = []
    # parts arrive sorted by triangle count, biggest first.
    for rank, (mat_name, diffuse, _shine, pos, nrm, uv, idx) in enumerate(parts):
        if not idx:
            continue
        n = len(pos) // 3
        v = add(struct.pack("<%df" % len(pos), *pos), 34962)
        a_pos = len(accessors)
        accessors.append({
            "bufferView": v, "componentType": 5126, "count": n, "type": "VEC3",
            "min": [min(pos[i::3]) for i in range(3)],
            "max": [max(pos[i::3]) for i in range(3)]})
        v = add(struct.pack("<%df" % len(nrm), *nrm), 34962)
        a_nrm = len(accessors)
        accessors.append({"bufferView": v, "componentType": 5126,
                          "count": n, "type": "VEC3"})
        v = add(struct.pack("<%df" % len(uv), *uv), 34962)
        a_uv = len(accessors)
        accessors.append({"bufferView": v, "componentType": 5126,
                          "count": n, "type": "VEC2"})
        if n <= 65535:
            v = add(struct.pack("<%dH" % len(idx), *idx), 34963)
            ctype = 5123
        else:
            v = add(struct.pack("<%dI" % len(idx), *idx), 34963)
            ctype = 5125
        a_idx = len(accessors)
        accessors.append({"bufferView": v, "componentType": ctype,
                          "count": len(idx), "type": "SCALAR"})

        # Prefer the name when it means something, fall back to shape.
        base, metallic, rough = surface_for(mat_name, diffuse, body_colour)
        if not any(tok in mat_name.lower() for tok, _ in SURFACE_BY_NAME):
            extent = [max(pos[i::3]) - min(pos[i::3]) for i in range(3)]
            centre_y = (max(pos[1::3]) + min(pos[1::3])) * 0.5
            shaped = surface_by_shape(extent, centre_y, len(idx) // 3,
                                      body_extent, rank)
            if shaped is not None:
                base, metallic, rough = shaped
                if base is None:
                    base = list(body_colour)
        primitives.append({
            "attributes": {"POSITION": a_pos, "NORMAL": a_nrm,
                           "TEXCOORD_0": a_uv},
            "indices": a_idx, "material": len(materials)})
        mat = {
            "name": mat_name or (name + "_mat"),
            "pbrMetallicRoughness": {
                "baseColorFactor": base,
                "metallicFactor": metallic,
                "roughnessFactor": rough,
            },
            "doubleSided": False,
        }
        if base[3] < 0.999:
            mat["alphaMode"] = "BLEND"
        materials.append(mat)

    if not primitives:
        return 0

    gltf = {
        "asset": {"version": "2.0", "generator": "cargame defender_to_gltf.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"uri": name + ".bin", "byteLength": len(buf)}],
    }
    open(os.path.join(out_dir, name + ".bin"), "wb").write(bytes(buf))
    json.dump(gltf, open(os.path.join(out_dir, name + ".gltf"), "w"), indent=1)
    return len(primitives)


def main():
    src, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    models, geos, geo_model, parent, materials, model_mat = read_scene(src)
    cache = {}

    # Triangles are now bucketed by (part, material) rather than by part
    # alone, so each material can become its own primitive with its own
    # colour further down.
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
        mat_id = model_mat.get(mid)
        mat_name, diffuse, shine = materials.get(
            mat_id, ("", (0.6, 0.6, 0.6), 16.0))
        groups.setdefault(group, {}).setdefault(
            (mat_name, diffuse, shine), []).extend(tris)
        for tri in tris:
            for p, _n, _t in tri:
                lowest = min(lowest, p[1])

    lift = -lowest
    print("lifting by %.3f m so the tyres sit on y = 0" % lift)

    pivots = {}
    for group, buckets in groups.items():
        if group == "body":
            pivots[group] = (0.0, 0.0, 0.0)
            continue
        tris = [t for bucket in buckets.values() for t in bucket]
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
    for group, buckets in groups.items():
        px, py, pz = pivots[group]
        cell = BODY_CELL if group == "body" else WHEEL_CELL
        parts = []
        total_v = total_t = 0
        for (mat_name, diffuse, shine), tris in sorted(
                buckets.items(), key=lambda kv: -len(kv[1])):
            moved = [tuple(((p[0] - px, p[1] + lift - py, p[2] - pz), n, t)
                           for p, n, t in tri) for tri in tris]
            pos, nrm, uv, idx = cluster(moved, cell)
            if not idx:
                continue
            parts.append((mat_name, diffuse, shine, pos, nrm, uv, idx))
            total_v += len(pos) // 3
            total_t += len(idx) // 3
        if not parts:
            continue
        all_pos = [v for p in parts for v in p[3]]
        extent = tuple(max(all_pos[i::3]) - min(all_pos[i::3])
                       for i in range(3)) if all_pos else (2.0, 2.0, 4.5)
        n_prims = write_gltf(group, parts, out_dir, BODY_COLOUR, extent)
        counts[group] = {"verts": total_v, "tris": total_t}
        print("%-12s %7d verts %7d tris  %2d materials  pivot (%.3f, %.3f, %.3f)"
              % (group, total_v, total_t, n_prims, px, py, pz))

    # groups["body"] is now {(material, diffuse, shininess): [tri, ...]}, so
    # the collision hull has to walk the buckets. The hull covers the whole
    # body regardless of material, exactly as before.
    body_pts = [(p[0], p[1] + lift, p[2])
                for bucket in groups.get("body", {}).values()
                for tri in bucket for p, _n, _t in tri]
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
