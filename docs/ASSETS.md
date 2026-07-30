# Analysis of the uploaded forest assets

Every file uploaded to `main` was extracted and parsed. This is what is in
them. Reproduce with:

```bash
python3 tools/asset_report.py <extracted asset dir>
```

## The short answer on animation

**No file contains animation.** Not one has an `AnimationCurve`,
`AnimationLayer`, `AnimationStack`, `Deformer` (skinning), `Pose`, or a
non-empty `Takes` block. They are all static geometry.

That is why the vegetation sways via a **vertex shader** rather than an
`AnimationPlayer`: there is no rig to drive. The shader is in
`scripts/forest.gd` (`WIND_SHADER`) and moves vertices by their height above
an anchor point, so trunks stay planted while canopies move.

## File by file

| File | Format | Models | Meshes | Verts | Polys | Animation |
| --- | --- | --- | --- | --- | --- | --- |
| `fbx game export.fbx` | FBX 7700 | 18 | 18 | 26 893 | 19 662 | none |
| `untitled.fbx` | FBX 7400 | 5 005 | 4 | 9 428 | 7 231 | none |
| `tree+asset.fbx` | FBX 7400 | 1 107 | 5 | 3 805 | 3 783 | none |
| `grass.fbx` | FBX 7400 | 5 | 3 | 12 651 | 7 464 | none |
| `Extra lowpoly tree set.fbx` | FBX 7400 | 6 | 5 | 72 | 26 | none |

### `fbx game export.fbx` — the useful one

18 separate meshes: one detailed tree (23 935 verts, 6.87 m tall), four fern
and plant variants, a grass tuft, and several small bushes. Textures reference
`woodbark`, `BarkAI_Spec` and a set of `GrassUE5` maps.

`UnitScaleFactor` is 0.1 but the models carry a scale of 1000, which works out
to metres 1:1. Geometry is **Z-up** despite the header claiming Y-up — the
usual Blender FBX export convention — so the converter rotates it.

Most of the forest comes from this file.

### `tree+asset.fbx` — rocks

1 101 of the 1 107 models are copies of a single flat "Tree Branch" card, which
is not much use. The three `Cube` meshes, however, are 1 500–1 800 vertex
boulders, and those became `rock_a`, `rock_b` and `rock_c`.

### `grass.fbx` — dense grass clumps

Two "Wild Grass Patch" meshes, 5 600 and 7 000 verts. Very dense for something
scattered thousands of times, so only one is used and sparingly.

### `untitled.fbx` — pre-scattered grass

4 unique meshes (grass blades and a daisy) already duplicated into 5 000 dupli
instances, plus a 2 m ground plane. The instancing is redundant here since the
game scatters its own with `MultiMesh`, and the unique meshes overlap what
`fbx game export.fbx` already provides, so this file is not used.

### `Extra lowpoly tree set.fbx` — billboard cards

Five flat quads (10–18 verts each) meant as branch billboards, with five PNG
textures. Too low-detail to stand in for real geometry at the distances the car
drives, so unused; the decimated tree LOD covers that job better.

### Archives

| Archive | Format | Contents |
| --- | --- | --- |
| `Extra+lowpoly+tree+fbx.rar` | RAR4 | the branch card FBX + 5 branch PNGs |
| `Textures.rar` | RAR5 | 2 bark JPEGs + a 6.4 MB branch atlas PNG |
| `fbx+game+export.rar` | RAR4 | `fbx game export.fbx` |

No `unrar` binary exists in the build environment, so `tools/rar_list.py` was
written to read both RAR4 and RAR5 directory structures directly. For the
entries that are actually compressed rather than stored, extraction uses the
`unrar2-cffi` package.

## What was taken into the game

`tools/forest_to_gltf.py` converts the meshes worth using, rotating Z-up to
Godot's Y-up and scaling to metres:

| Asset | Source | Tris | Height |
| --- | --- | --- | --- |
| `tree` | fbx game export | 28 327 | 6.87 m |
| `tree_lod` | decimated from `tree` | 3 367 | 6.87 m |
| `fern_a`, `fern_b` | fbx game export | ~550 | 1.73 m |
| `bush_a`, `bush_b` | fbx game export | ~155 | 1.18 m |
| `plant` | fbx game export | 185 | 1.27 m |
| `grass_tuft` | fbx game export | 123 | 0.56 m |
| `rock_a/b/c` | tree+asset | 890–3 504 | 2.6–4.2 m |
| `grass_patch` | grass.fbx | 8 256 | 0.90 m |

### Why there is a decimated tree

At 28 327 triangles, the 420 trees the forest wants would be **11.9 M
triangles** on their own. A grid-clustering pass produces `tree_lod` at 3 367
triangles — an 88% reduction that is hard to tell apart beyond about 40 m.
Trees within 110 m of the spawn use the full mesh, the rest use the LOD, which
brings the whole scene from 13.8 M to **6.0 M triangles**.

## Scene budget

| | Count | Triangles |
| --- | --- | --- |
| Trees (full detail) | 110 | 3.12 M |
| Trees (LOD) | 340 | 1.14 M |
| Ferns, bushes, plants | 2 320 | 0.73 M |
| Grass tufts | 1 500 | 0.18 M |
| Rocks | 270 | 0.63 M |
| Terrain (257²) | 1 | 0.13 M |
| Car | 1 | 0.10 M |
| **Total** | | **≈ 6.0 M** |

Vegetation is drawn with one `MultiMeshInstance3D` per species, so all 4 540
plants and rocks cost **11 draw calls**, not 4 540.
