#!/usr/bin/env python3
"""
Convert the Assetto Corsa style "bmw_1m final.fbx" asset into a clean glTF 2.0
scene that is ready for Godot 4.3.

What it does
------------
* parses the binary FBX (7700) without any external dependency
* bakes every node's world transform, converts centimetres -> metres and
  rotates the model 180 deg around Y so the car faces Godot's -Z ("forward")
* splits the model into logical groups that the vehicle controller animates:
      body, hub_<corner>, wheel_<corner> (incl. brake disc), steering
* merges all meshes of a group per material (40 materials -> ~40 draw calls
  instead of 210 nodes)
* writes PBR materials with base colour / normal maps pointing at the PNG
  textures shipped with the asset
* writes convex collision point clouds (slab decomposition of the body shell)
  to a JSON side car file used to build the Godot collision shapes

Usage:
    python3 tools/fbx_to_gltf.py <fbx> <texture_dir> <out_dir>
"""

import json
import math
import os
import shutil
import struct
import sys
import zlib

# --------------------------------------------------------------------------- #
#  minimal binary FBX reader
# --------------------------------------------------------------------------- #


def _read_prop(d, p):
    t = chr(d[p])
    p += 1
    if t == "Y":
        v = struct.unpack("<h", d[p:p + 2])[0]; p += 2
    elif t == "C":
        v = d[p]; p += 1
    elif t == "I":
        v = struct.unpack("<i", d[p:p + 4])[0]; p += 4
    elif t == "F":
        v = struct.unpack("<f", d[p:p + 4])[0]; p += 4
    elif t == "D":
        v = struct.unpack("<d", d[p:p + 8])[0]; p += 8
    elif t == "L":
        v = struct.unpack("<q", d[p:p + 8])[0]; p += 8
    elif t in "fdlbi":
        cnt, enc, cl = struct.unpack("<III", d[p:p + 12]); p += 12
        raw = d[p:p + cl]; p += cl
        if enc == 1:
            raw = zlib.decompress(raw)
        fmt = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "b"}[t]
        v = struct.unpack("<%d%s" % (cnt, fmt), raw)
    elif t in "SR":
        l = struct.unpack("<I", d[p:p + 4])[0]; p += 4
        v = d[p:p + l]; p += l
        if t == "S":
            v = v.decode("utf8", "ignore")
    else:
        raise ValueError("unknown FBX property type %r" % t)
    return v, p


def _read_node(d, p):
    end, nprop, _plen = struct.unpack("<QQQ", d[p:p + 24]); p += 24
    nlen = d[p]; p += 1
    name = d[p:p + nlen].decode("utf8", "ignore"); p += nlen
    if end == 0:
        return None, p
    props = []
    for _ in range(nprop):
        v, p = _read_prop(d, p)
        props.append(v)
    kids = []
    while p < end:
        c, p2 = _read_node(d, p)
        p = p2
        if c is None:
            break
        kids.append(c)
    return (name, props, kids), end


def parse_fbx(path):
    d = open(path, "rb").read()
    if d[:21] != b"Kaydara FBX Binary  \x00":
        raise ValueError("not a binary FBX file")
    pos, nodes = 27, []
    while pos < len(d) - 30:
        n, pos = _read_node(d, pos)
        if n is None:
            break
        nodes.append(n)
    return nodes


def props70(node):
    out = {}
    for c in node[2]:
        if c[0] == "Properties70":
            for p in c[2]:
                out[p[1][0]] = p[1][4:]
    return out


def child(node, name):
    for c in node[2]:
        if c[0] == name:
            return c
    return None


def obj_name(o):
    v = o[1][1]
    return v.split("\x00")[0] if isinstance(v, str) else str(v)


# --------------------------------------------------------------------------- #
#  math helpers (row major 4x4)
# --------------------------------------------------------------------------- #

def mat_ident():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]]


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]]


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def node_local_matrix(model):
    p = props70(model)
    t = p.get("Lcl Translation", [0.0, 0.0, 0.0])
    r = p.get("Lcl Rotation", [0.0, 0.0, 0.0])
    s = p.get("Lcl Scaling", [1.0, 1.0, 1.0])
    rx, ry, rz = (math.radians(v) for v in r)
    m = mat_mul(mat_mul(rot_z(rz), rot_y(ry)), rot_x(rx))
    m = mat_mul(m, [[s[0], 0, 0, 0], [0, s[1], 0, 0], [0, 0, s[2], 0], [0, 0, 0, 1]])
    m[0][3], m[1][3], m[2][3] = t[0], t[1], t[2]
    return m


def xform_point(m, x, y, z):
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
            m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
            m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3])


def xform_dir(m, x, y, z):
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z)


# --------------------------------------------------------------------------- #
#  layer element access
# --------------------------------------------------------------------------- #

def layer_values(geo, elem_name, data_name, index_name):
    e = child(geo, elem_name)
    if e is None:
        return None, None, None, None
    mapping = child(e, "MappingInformationType")[1][0]
    ref = child(e, "ReferenceInformationType")[1][0]
    data = child(e, data_name)
    data = data[1][0] if data else None
    idx = child(e, index_name) if index_name else None
    idx = idx[1][0] if idx else None
    return mapping, ref, data, idx


# --------------------------------------------------------------------------- #
#  conversion
# --------------------------------------------------------------------------- #

SCALE = 0.01           # centimetres -> metres
CORNERS = ("lf", "rf", "lr", "rr")


class Converter:
    def __init__(self, fbx_path, tex_dir, out_dir):
        self.out_dir = out_dir
        self.tex_dir = tex_dir
        self.nodes = parse_fbx(fbx_path)
        objs = [n for n in self.nodes if n[0] == "Objects"][0]
        conns = [n for n in self.nodes if n[0] == "Connections"][0]

        self.models = {o[1][0]: o for o in objs[2] if o[0] == "Model"}
        self.geos = {o[1][0]: o for o in objs[2] if o[0] == "Geometry"}
        self.mats = {o[1][0]: o for o in objs[2] if o[0] == "Material"}
        self.texs = {o[1][0]: o for o in objs[2] if o[0] == "Texture"}

        self.parent = {}
        self.model_geos = {}
        self.model_mats = {}
        self.mat_tex = {}
        for c in conns[2]:
            kind = c[1][0]
            if kind == "OO":
                src, dst = c[1][1], c[1][2]
                if src in self.geos and dst in self.models:
                    self.model_geos.setdefault(dst, []).append(src)
                elif src in self.mats and dst in self.models:
                    self.model_mats.setdefault(dst, []).append(src)
                elif src in self.models:
                    self.parent[src] = dst
            elif kind == "OP":
                src, dst, prop = c[1][1], c[1][2], c[1][3]
                if src in self.texs and dst in self.mats:
                    self.mat_tex.setdefault(dst, {})[prop] = self._tex_file(self.texs[src])

        self._wm_cache = {}
        # convert FBX (Y up, +Z front, +X left) to Godot/glTF (Y up, -Z front, +X right)
        self.basis = [[-SCALE, 0, 0, 0], [0, SCALE, 0, 0], [0, 0, -SCALE, 0], [0, 0, 0, 1]]

    # -- naming / hierarchy ------------------------------------------------ #

    def _tex_file(self, tex):
        c = child(tex, "RelativeFilename") or child(tex, "FileName")
        if not c:
            return None
        v = c[1][0]
        if isinstance(v, bytes):
            v = v.decode("utf8", "ignore")
        return os.path.basename(v.replace("\\", "/"))

    def world_matrix(self, mid):
        if mid in self._wm_cache:
            return self._wm_cache[mid]
        m = node_local_matrix(self.models[mid])
        p = self.parent.get(mid)
        if p in self.models:
            m = mat_mul(self.world_matrix(p), m)
        self._wm_cache[mid] = m
        return m

    def ancestry(self, mid):
        out = []
        while mid in self.models:
            out.append(obj_name(self.models[mid]))
            mid = self.parent.get(mid)
        return out

    def group_of(self, mid):
        """Return (group_name, pivot_model_id) for a mesh node."""
        chain_ids = []
        cur = mid
        while cur in self.models:
            chain_ids.append(cur)
            cur = self.parent.get(cur)
        for cid in chain_ids:
            name = obj_name(self.models[cid])
            up = name.upper()
            if up.startswith("WHEEL_") and len(up) == 8:
                return "wheel_" + up[6:].lower(), cid
            if up.startswith("DISC_"):
                return "wheel_" + up[5:7].lower(), self.pivot_for_corner(up[5:7].lower())
            if up.startswith("SUSP_"):
                return "hub_" + up[5:7].lower(), self.pivot_for_corner(up[5:7].lower())
            if up == "STEER_HR":
                return "steering", cid
        return "body", None

    def pivot_for_corner(self, corner):
        return self.corner_pivot[corner]

    # -- geometry ---------------------------------------------------------- #

    def triangulate(self, geo, world, mat_ids):
        """Yield (material_id, [(pos, nrm, uv) x3]) triangles in glTF space."""
        verts = child(geo, "Vertices")[1][0]
        pvi = child(geo, "PolygonVertexIndex")[1][0]

        nmap, nref, ndata, nidx = layer_values(geo, "LayerElementNormal", "Normals", "NormalsIndex")
        umap, uref, udata, uidx = layer_values(geo, "LayerElementUV", "UV", "UVIndex")
        mmap, mref, mdata, _ = layer_values(geo, "LayerElementMaterial", "Materials", None)

        nrm_m = mat_ident()
        for i in range(3):
            for j in range(3):
                nrm_m[i][j] = world[i][j]

        poly = []
        poly_index = 0
        out = []
        for k, raw in enumerate(pvi):
            last = raw < 0
            vi = ~raw if last else raw
            poly.append((k, vi))
            if not last:
                continue

            # material for this polygon
            mid = None
            if mat_ids:
                if mmap == "AllSame" or mdata is None:
                    mid = mat_ids[0]
                else:
                    m_i = mdata[poly_index] if poly_index < len(mdata) else 0
                    mid = mat_ids[m_i] if m_i < len(mat_ids) else mat_ids[0]

            corners = []
            for k2, vi2 in poly:
                px, py, pz = verts[vi2 * 3], verts[vi2 * 3 + 1], verts[vi2 * 3 + 2]
                wx, wy, wz = xform_point(world, px, py, pz)
                gx, gy, gz = xform_point(self.basis, wx, wy, wz)

                if ndata is not None:
                    ni = k2 if nmap == "ByPolygonVertex" else vi2
                    if nref == "IndexToDirect" and nidx:
                        ni = nidx[ni]
                    nx, ny, nz = ndata[ni * 3], ndata[ni * 3 + 1], ndata[ni * 3 + 2]
                    nx, ny, nz = xform_dir(nrm_m, nx, ny, nz)
                    nx, ny, nz = -nx, ny, -nz
                    ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                    nrm = (nx / ln, ny / ln, nz / ln)
                else:
                    nrm = (0.0, 1.0, 0.0)

                if udata is not None:
                    ui = k2 if umap == "ByPolygonVertex" else vi2
                    if uref == "IndexToDirect" and uidx:
                        ui = uidx[ui]
                    if ui < 0:
                        uv = (0.0, 0.0)
                    else:
                        uv = (udata[ui * 2], 1.0 - udata[ui * 2 + 1])
                else:
                    uv = (0.0, 0.0)
                corners.append(((gx, gy, gz), nrm, uv))

            for i in range(1, len(corners) - 1):
                out.append((mid, (corners[0], corners[i], corners[i + 1])))
            poly = []
            poly_index += 1
        return out

    # -- main -------------------------------------------------------------- #

    def run(self):
        # locate the pivot node of every corner (WHEEL_xx defines the hub centre)
        self.corner_pivot = {}
        for mid, m in self.models.items():
            n = obj_name(m).upper()
            if n.startswith("WHEEL_") and len(n) == 8:
                self.corner_pivot[n[6:].lower()] = mid
        assert len(self.corner_pivot) == 4, self.corner_pivot

        groups = {}         # group -> {mat_id: [tri, ...]}
        pivots = {}         # group -> pivot position (glTF space)
        pivot_rot = {}      # group -> pivot 3x3 rotation (glTF space)
        hull_pts = []       # exterior shell points for collision

        for mid, geo_ids in self.model_geos.items():
            group, pivot_mid = self.group_of(mid)
            world = self.world_matrix(mid)
            mat_ids = self.model_mats.get(mid, [])
            if not mat_ids:  # inherit from parent null
                p = self.parent.get(mid)
                while p in self.models and not mat_ids:
                    mat_ids = self.model_mats.get(p, [])
                    p = self.parent.get(p)

            if group not in pivots:
                if pivot_mid is None:
                    pivots[group] = (0.0, 0.0, 0.0)
                    pivot_rot[group] = None
                else:
                    pw = self.world_matrix(pivot_mid)
                    pivots[group] = xform_point(self.basis, pw[0][3], pw[1][3], pw[2][3])
                    # The steering column is tilted; bake its orientation into
                    # the node so the game can just spin the mesh around its own
                    # Z axis instead of reconstructing the rake at runtime.
                    if group == "steering":
                        pivot_rot[group] = self.basis_rotation(pw)
                    else:
                        pivot_rot[group] = None

            bucket = groups.setdefault(group, {})
            chain = [c.upper() for c in self.ancestry(mid)]
            interior = any(c.startswith(("COCKPIT", "CINTURE", "STEER", "SHIFT", "IN_REAR")) for c in chain)

            for gid in geo_ids:
                for mat_id, tri in self.triangulate(self.geos[gid], world, mat_ids):
                    bucket.setdefault(mat_id, []).append(tri)
                    if group == "body" and not interior:
                        for c in tri:
                            hull_pts.append(c[0])

        # move each group into its own pivot space: subtract the pivot origin
        # and, where a pivot orientation was captured, rotate into its frame
        for group, buckets in groups.items():
            px, py, pz = pivots[group]
            rot = pivot_rot.get(group)
            if px == py == pz == 0.0 and rot is None:
                continue
            inv = transpose3(rot) if rot else None
            for mat_id, tris in buckets.items():
                new_tris = []
                for tri in tris:
                    corners = []
                    for c in tri:
                        p = (c[0][0] - px, c[0][1] - py, c[0][2] - pz)
                        n = c[1]
                        if inv:
                            p = mul3(inv, p)
                            n = mul3(inv, n)
                        corners.append((p, n, c[2]))
                    new_tris.append(tuple(corners))
                buckets[mat_id] = new_tris

        self.write_gltf(groups, pivots, pivot_rot)
        self.write_collision(hull_pts, pivots)

    # -- glTF writing ------------------------------------------------------ #

    def material_def(self, mat_id, tex_index):
        mat = self.mats[mat_id]
        name = obj_name(mat)
        p = props70(mat)
        diff = p.get("DiffuseColor", p.get("Diffuse", [0.8, 0.8, 0.8]))
        shin = (p.get("ShininessExponent") or [32.0])[0]
        # Blinn-Phong exponent -> roughness (Burley approximation)
        rough = max(0.04, min(1.0, math.sqrt(2.0 / (max(shin, 1.0) + 2.0))))
        metal = 0.0
        alpha = (p.get("Opacity") or [1.0])[0]
        base = [max(diff[0], 0.02), max(diff[1], 0.02), max(diff[2], 0.02), alpha]

        up = name.upper()
        if up.startswith("VETRI") or "VETRO" in up:          # glass
            base = [0.06, 0.07, 0.08, 0.34]
            rough, metal = 0.05, 0.0
        elif up == "CHASSIS" or up == "LIVREA":              # car paint
            base = [0.62, 0.05, 0.06, 1.0]
            rough, metal = 0.22, 0.35
        elif up == "CHASSIS_METAL" or up == "MIRROR":
            base = [0.85, 0.86, 0.88, 1.0]
            rough, metal = 0.14, 1.0
        elif up.startswith("RT_RIM") or up.startswith("LOGHI_RIM"):
            base = [0.34, 0.35, 0.37, 1.0]
            rough, metal = 0.28, 0.9
        elif up == "DISCHI_FRENI":
            base = [0.42, 0.42, 0.44, 1.0]
            rough, metal = 0.42, 1.0
        elif up == "CAR_PINZAFRENI":
            base = [0.45, 0.05, 0.05, 1.0]
            rough, metal = 0.35, 0.4
        elif up.startswith("RT_BATTISTRADA") or up == "INT_GOMMA":   # tyre rubber
            base = [0.055, 0.055, 0.06, 1.0]
            rough, metal = 0.88, 0.0
        elif up.startswith("FANALI") or up.startswith("FARI"):
            rough = 0.12
        elif up.startswith("PLASTICA") or up.startswith("INT_PLAASTICA"):
            base = [0.05, 0.05, 0.055, 1.0]
            rough = 0.45

        out = {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": base,
                "metallicFactor": metal,
                "roughnessFactor": rough,
            },
            "doubleSided": False,
        }
        tex = self.mat_tex.get(mat_id, {})
        base_tex = tex.get("DiffuseColor")
        nrm_tex = tex.get("NormalMap") or tex.get("Bump")
        if base_tex and base_tex in tex_index:
            out["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_index[base_tex]}
            out["pbrMetallicRoughness"]["baseColorFactor"] = [1.0, 1.0, 1.0, base[3]]
        if nrm_tex and nrm_tex in tex_index:
            out["normalTexture"] = {"index": tex_index[nrm_tex]}
        if base[3] < 0.999:
            out["alphaMode"] = "BLEND"
        return out

    def resolve_textures(self, used_mat_ids):
        """copy the PNG version of every referenced texture next to the glTF"""
        avail = {f.lower(): f for f in os.listdir(self.tex_dir)}
        dst_dir = os.path.join(self.out_dir, "textures")
        os.makedirs(dst_dir, exist_ok=True)
        index, images, samplers, textures = {}, [], [{"magFilter": 9729, "minFilter": 9987,
                                                      "wrapS": 10497, "wrapT": 10497}], []
        for mat_id in used_mat_ids:
            for _prop, fname in self.mat_tex.get(mat_id, {}).items():
                if not fname or fname in index:
                    continue
                stem = os.path.splitext(fname)[0].lower()
                real = None
                for ext in (".png", ".jpg", ".jpeg"):
                    if stem + ext in avail:
                        real = avail[stem + ext]
                        break
                if real is None:
                    continue
                shutil.copyfile(os.path.join(self.tex_dir, real), os.path.join(dst_dir, real))
                images.append({"uri": "textures/" + real})
                textures.append({"source": len(images) - 1, "sampler": 0})
                index[fname] = len(textures) - 1
        return index, images, samplers, textures

    def basis_rotation(self, world):
        """Orthonormal 3x3 of a world matrix, expressed in glTF axes."""
        cols = []
        for j in range(3):
            v = (world[0][j], world[1][j], world[2][j])
            v = (-v[0], v[1], -v[2])          # FBX -> glTF axis flip
            l = math.sqrt(sum(c * c for c in v)) or 1.0
            cols.append([c / l for c in v])
        return [[cols[j][i] for j in range(3)] for i in range(3)]

    def write_gltf(self, groups, pivots, pivot_rot=None):
        os.makedirs(self.out_dir, exist_ok=True)
        used_mats = []
        for buckets in groups.values():
            for mid in buckets:
                if mid is not None and mid not in used_mats:
                    used_mats.append(mid)
        tex_index, images, samplers, textures = self.resolve_textures(used_mats)
        materials = [self.material_def(mid, tex_index) for mid in used_mats]
        mat_slot = {mid: i for i, mid in enumerate(used_mats)}

        buf = bytearray()
        accessors, buffer_views, meshes, nodes = [], [], [], []

        def add_view(data, target):
            while len(buf) % 4:
                buf.append(0)
            off = len(buf)
            buf.extend(data)
            buffer_views.append({"buffer": 0, "byteOffset": off,
                                 "byteLength": len(data), "target": target})
            return len(buffer_views) - 1

        order = ["body", "steering"] + ["hub_" + c for c in CORNERS] + ["wheel_" + c for c in CORNERS]
        order = [g for g in order if g in groups] + [g for g in groups if g not in order]

        stats = {}
        for group in order:
            buckets = groups[group]
            prims = []
            gv = 0
            for mat_id, tris in sorted(buckets.items(), key=lambda kv: mat_slot.get(kv[0], -1)):
                weld, pos, nrm, uv, idx = {}, [], [], [], []
                for tri in tris:
                    for c in tri:
                        key = (round(c[0][0], 6), round(c[0][1], 6), round(c[0][2], 6),
                               round(c[1][0], 4), round(c[1][1], 4), round(c[1][2], 4),
                               round(c[2][0], 5), round(c[2][1], 5))
                        i = weld.get(key)
                        if i is None:
                            i = len(pos) // 3
                            weld[key] = i
                            pos.extend(c[0]); nrm.extend(c[1]); uv.extend(c[2])
                        idx.append(i)
                if not idx:
                    continue
                gv += len(pos) // 3
                nverts = len(pos) // 3
                pmin = [min(pos[i::3]) for i in range(3)]
                pmax = [max(pos[i::3]) for i in range(3)]

                v_pos = add_view(struct.pack("<%df" % len(pos), *pos), 34962)
                accessors.append({"bufferView": v_pos, "componentType": 5126, "count": nverts,
                                  "type": "VEC3", "min": pmin, "max": pmax})
                a_pos = len(accessors) - 1
                v_nrm = add_view(struct.pack("<%df" % len(nrm), *nrm), 34962)
                accessors.append({"bufferView": v_nrm, "componentType": 5126,
                                  "count": nverts, "type": "VEC3"})
                a_nrm = len(accessors) - 1
                v_uv = add_view(struct.pack("<%df" % len(uv), *uv), 34962)
                accessors.append({"bufferView": v_uv, "componentType": 5126,
                                  "count": nverts, "type": "VEC2"})
                a_uv = len(accessors) - 1

                if nverts <= 65535:
                    v_idx = add_view(struct.pack("<%dH" % len(idx), *idx), 34963)
                    ctype = 5123
                else:
                    v_idx = add_view(struct.pack("<%dI" % len(idx), *idx), 34963)
                    ctype = 5125
                accessors.append({"bufferView": v_idx, "componentType": ctype,
                                  "count": len(idx), "type": "SCALAR"})
                a_idx = len(accessors) - 1

                prim = {"attributes": {"POSITION": a_pos, "NORMAL": a_nrm, "TEXCOORD_0": a_uv},
                        "indices": a_idx}
                if mat_id in mat_slot:
                    prim["material"] = mat_slot[mat_id]
                prims.append(prim)

            meshes.append({"name": group + "_mesh", "primitives": prims})
            px, py, pz = pivots[group]
            node = {"name": group, "mesh": len(meshes) - 1,
                    "translation": [px, py, pz]}
            rot = (pivot_rot or {}).get(group)
            if rot:
                node["rotation"] = quat_from_matrix(rot)
            nodes.append(node)
            stats[group] = gv

        root_children = list(range(len(nodes)))
        nodes.append({"name": "bmw_1m", "children": root_children})

        gltf = {
            "asset": {"version": "2.0", "generator": "cargame fbx_to_gltf.py"},
            "scene": 0,
            "scenes": [{"nodes": [len(nodes) - 1]}],
            "nodes": nodes,
            "meshes": meshes,
            "materials": materials,
            "accessors": accessors,
            "bufferViews": buffer_views,
            "buffers": [{"uri": "bmw_1m.bin", "byteLength": len(buf)}],
        }
        if images:
            gltf["images"] = images
            gltf["samplers"] = samplers
            gltf["textures"] = textures

        open(os.path.join(self.out_dir, "bmw_1m.bin"), "wb").write(bytes(buf))
        json.dump(gltf, open(os.path.join(self.out_dir, "bmw_1m.gltf"), "w"), indent=1)
        print("groups (vertices):", json.dumps(stats, indent=1))
        print("materials:", len(materials), "textures:", len(textures),
              "buffer:", round(len(buf) / 1048576.0, 2), "MB")

    # -- collision --------------------------------------------------------- #

    def write_collision(self, pts, pivots, slabs=9):
        """Slab based convex decomposition of the outer shell."""
        if not pts:
            return
        zmin = min(p[2] for p in pts)
        zmax = max(p[2] for p in pts)
        span = (zmax - zmin) / slabs
        shapes = []
        for s in range(slabs):
            lo = zmin + s * span
            hi = lo + span
            sel = [p for p in pts if lo - 0.01 <= p[2] <= hi + 0.01]
            if len(sel) < 8:
                continue
            shapes.append(support_cloud(sel, 96))
        info = {
            "body_shapes": shapes,
            "body_aabb": {
                "min": [min(p[i] for p in pts) for i in range(3)],
                "max": [max(p[i] for p in pts) for i in range(3)],
            },
            "wheel_positions": {c: list(pivots["wheel_" + c]) for c in CORNERS},
        }
        json.dump(info, open(os.path.join(self.out_dir, "bmw_1m_collision.json"), "w"), indent=1)
        print("collision slabs:", len(shapes),
              "points:", sum(len(s) for s in shapes))


def transpose3(m):
    return [[m[j][i] for j in range(3)] for i in range(3)]


def mul3(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def quat_from_matrix(m):
    """glTF stores rotations as xyzw quaternions."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    return [x / n, y / n, z / n, w / n]


def support_cloud(points, ndirs):
    """Extreme points of a cloud along evenly distributed directions."""
    out = {}
    ga = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(ndirs):
        y = 1.0 - (i / float(ndirs - 1)) * 2.0
        r = math.sqrt(max(0.0, 1.0 - y * y))
        th = ga * i
        d = (math.cos(th) * r, y, math.sin(th) * r)
        best, bi = -1e18, 0
        for j, p in enumerate(points):
            v = p[0] * d[0] + p[1] * d[1] + p[2] * d[2]
            if v > best:
                best, bi = v, j
        p = points[bi]
        out[(round(p[0], 4), round(p[1], 4), round(p[2], 4))] = True
    return [list(k) for k in out]


if __name__ == "__main__":
    fbx, texdir, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    Converter(fbx, texdir, outdir).run()
