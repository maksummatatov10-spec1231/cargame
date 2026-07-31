#!/usr/bin/env python3
"""
Write per-texture .import files so Godot imports each one correctly first time.

Why this exists. The editor prints, on a fresh checkout:

    res://assets/car/textures/Dettaglio_Faro_NM.png: текстура используется
    как карта нормалей в 3D. Включено красно-зелёное сжатие...

That comes from resource_importer_texture.cpp:110-131, which fires exactly
when a texture is used as a normal map while its .import still says
`compress/normal_map = 0`, and again when it is used for roughness while
`roughness/mode = 0`. It then rewrites the .import itself and reimports.

The project's `[importer_defaults] texture={...}` block cannot fix this,
because those defaults apply to EVERY texture, and forcing normal_map = 1
project-wide would throw away the blue channel of the 26 albedo textures.
Verified in editor_file_system.cpp:2435-2459: importer_defaults are only
merged in when the file has no .import of its own, and a per-file .import
wins over them.

So the right answer is a .import per texture, with the correct settings for
that texture's actual role - which this script reads out of the glTF rather
than guessing from the file name.

Usage:
    python3 tools/make_import_files.py
"""

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# glTF slot -> what Godot needs to know about the texture.
#
#   normal   : compress/normal_map = 1 enables red-green (RGTC) compression.
#              The blue channel of a tangent-space normal is derivable from
#              the other two, so dropping it is lossless in practice and
#              halves the memory.
#   roughness: roughness/mode picks which channel carries roughness. Godot
#              numbers them from 2 (2 = red, 3 = green, 4 = blue, 5 = alpha,
#              6 = grey). glTF packs roughness in GREEN, so mode 3.
#   albedo   : sRGB colour, normal_map explicitly disabled so the importer
#              never decides to strip its blue channel.
ROLE_SETTINGS = {
    "normal": {"compress/normal_map": 1, "roughness/mode": 0},
    "roughness": {"compress/normal_map": 2, "roughness/mode": 3},
    "albedo": {"compress/normal_map": 2, "roughness/mode": 0},
}

TEMPLATE = """[remap]

importer="texture"
type="CompressedTexture2D"
uid="uid://{uid}"
path.s3tc="res://.godot/imported/{base}-{hash}.s3tc.ctex"
metadata={{
"imported_formats": ["s3tc_bptc"],
"vram_texture": true
}}

[deps]

source_file="res://{src}"
dest_files=["res://.godot/imported/{base}-{hash}.s3tc.ctex"]

[params]

compress/mode=2
compress/high_quality=false
compress/lossy_quality=0.7
compress/hdr_compression=1
compress/normal_map={normal_map}
compress/channel_pack=0
mipmaps/generate=true
mipmaps/limit=-1
roughness/mode={roughness_mode}
roughness/src_normal="{src_normal}"
process/fix_alpha_border=true
process/premult_alpha=false
process/normal_map_invert_y=false
process/hdr_as_srgb=false
process/hdr_clamp_exposure=false
process/size_limit=0
detect_3d/compress_to=0
"""


def roles_from_gltf(path):
    """{image file name: role} read from how the glTF actually uses it."""
    gltf = json.load(open(path))
    images = [i.get("uri", "") for i in gltf.get("images", [])]
    textures = [t.get("source") for t in gltf.get("textures", [])]
    out = {}

    def mark(index, role):
        src = textures[index]
        name = os.path.basename(images[src])
        # normal wins over roughness wins over albedo, because the importer
        # keys off the most specialised use.
        rank = {"albedo": 0, "roughness": 1, "normal": 2}
        if rank[role] >= rank.get(out.get(name, "albedo"), 0):
            out[name] = role

    for mat in gltf.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        if "baseColorTexture" in pbr:
            mark(pbr["baseColorTexture"]["index"], "albedo")
        if "metallicRoughnessTexture" in pbr:
            mark(pbr["metallicRoughnessTexture"]["index"], "roughness")
        if "normalTexture" in mat:
            mark(mat["normalTexture"]["index"], "normal")
        if "emissiveTexture" in mat:
            mark(mat["emissiveTexture"]["index"], "albedo")
    return out


def stable_hash(path):
    """Godot names its cache files after an md5 of the resource path."""
    return hashlib.md5(("res://" + path).encode()).hexdigest()


# Godot's UID alphabet, from core/io/resource_uid.cpp:40-41:
#
#     static constexpr uint32_t char_count = ('z' - 'a');      // 25, not 26
#     static constexpr uint32_t base = char_count + ('9' - '0'); // 34, not 36
#
# The comment right above it admits the constants are off by one, so 'z' and
# '9' are never produced and are NOT accepted on the way back in: text_to_id()
# maps 'z' to 25, which id_to_text() would re-encode as the digit '0'. Any id
# text containing 'z' or '9' therefore does not survive a round trip.
UID_CHAR_COUNT = 25
UID_BASE = 34


def id_to_text(value):
    """Exactly ResourceUID::id_to_text - the only spelling Godot round-trips."""
    out = ""
    while value:
        c = value % UID_BASE
        if c < UID_CHAR_COUNT:
            out = chr(ord("a") + c) + out
        else:
            out = chr(ord("0") + (c - UID_CHAR_COUNT)) + out
        value //= UID_BASE
    return out


def uid_for(rel_path):
    """A stable, canonical UID derived from the resource path.

    WHY NOT uuid4().hex - the bug this replaces
    -------------------------------------------
    The first version of this script wrote `uuid.uuid4().hex[:13]`. That is a
    HEXADECIMAL string, and it is wrong twice over:

      1. Hex uses the digits 0-9 and letters a-f, so 29 of the 44 files got a
         '9' in them. text_to_id() in resource_uid.cpp:66 maps '9' to the value
         34 - out of range for base 34 - so the id it produced could never be
         re-encoded to the same text. The editor read the .import, converted
         the text to an id, failed to find that id in its cache, and printed:

             core/io/resource_uid.cpp:132 - Condition "!unique_ids.has(p_id)"
             is true. Returning: String()
             Can't find file 'uid://chxql2rtxgf8b'.

         once per texture - 44 of each, which is exactly what the user saw.
         The names in the errors are the CANONICAL spellings, which is why
         none of them matched the strings written in the files.

      2. It was random, so every run of this script invalidated every UID and
         forced a full reimport.

    Deriving the id from the path fixes both: same file, same id, forever, and
    the id is generated by Godot's own encoder so it round-trips by
    construction.
    """
    digest = hashlib.sha256(("res://" + rel_path).encode()).digest()
    value = int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
    return id_to_text(value)


def write_import(rel_path, role):
    settings = ROLE_SETTINGS[role]
    base = os.path.basename(rel_path)
    out = TEMPLATE.format(
        uid=uid_for(rel_path),
        base=base,
        hash=stable_hash(rel_path),
        src=rel_path,
        normal_map=settings["compress/normal_map"],
        roughness_mode=settings["roughness/mode"],
        src_normal="",
    )
    open(os.path.join(ROOT, rel_path + ".import"), "w").write(out)


def main():
    written = {"normal": 0, "roughness": 0, "albedo": 0}

    # The BMW is the only asset with textures declared in its glTF.
    gltf = os.path.join(ROOT, "assets", "car", "bmw_1m.gltf")
    roles = roles_from_gltf(gltf)
    tex_dir = os.path.join("assets", "car", "textures")
    for name in sorted(os.listdir(os.path.join(ROOT, tex_dir))):
        if not name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        role = roles.get(name, "albedo")
        write_import(os.path.join(tex_dir, name), role)
        written[role] += 1

    # The bark textures are sampled as plain colour by the forest shader.
    bark_dir = os.path.join("assets", "forest", "textures")
    full = os.path.join(ROOT, bark_dir)
    if os.path.isdir(full):
        for name in sorted(os.listdir(full)):
            if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            write_import(os.path.join(bark_dir, name), "albedo")
            written["albedo"] += 1

    print("wrote .import files: %d normal, %d roughness, %d albedo"
          % (written["normal"], written["roughness"], written["albedo"]))
    print("roles were read from the glTF, not guessed from file names")
    return 0


if __name__ == "__main__":
    sys.exit(main())
