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

---

# The GHammer pickup

`Okhey+GHammer+-+pickup+(FBX+File).7z` — a single 29 MB FBX inside a 7-Zip
archive.

## Animation

**None.** No `AnimationCurve`, `AnimationLayer`, `AnimationStack`, `Deformer`,
`Pose` or non-empty `Takes` block — the same result as every other uploaded
asset. It is static geometry, so the wheels are animated by the physics exactly
as the BMW's are: spin integrated from drive, brake and contact-patch torque,
steering applied to the raycast the wheel hangs off.

## What is in it

| | |
| --- | --- |
| Format | FBX 7400, 29.4 MB |
| Meshes | 52 |
| Vertices | 775 983 |
| Polygons | 490 468 |
| Materials | 40+ (`Wheel_BRC`, `Paint_base`, `Spring`, `LGT_*`, …) |

Node names are generic (`Plane.*`, `Plano.*`, `Cilindro.*`), so the parts were
identified geometrically rather than by name:

* node scale is **−100** with a 90° X rotation, so the file is in centimetres,
  Z-up, and mirrored — the negative scale also flips triangle winding, which
  has to be undone or the whole model renders inside out
* the model lies along **X**, so it is rotated 90° about Y to face Godot's −Z
* `Plano.001/003/005/007` are the tyres, `.002/004/006/008` the rims,
  radius 47 cm
* `Cilindro.017/072/140/222` are the suspension springs, kept with the hubs
* the lowest point is 6.5 cm below the origin, so everything is lifted to put
  the contact patch exactly on y = 0

## Measured dimensions

| | |
| --- | --- |
| Length | 5.50 m |
| Width | 2.23 m |
| Height | 2.14 m |
| Wheelbase | 3.130 m |
| Track | 1.681 m |
| Tyre radius | 0.470 m |

Realistic for a full-size pickup (an F-150 is 5.3 × 2.0 × 1.9 m).

## Decimation

490 k polygons is roughly five times a modern game car. Grid clustering brings
it down without a visible change at driving distances:

| Part | Cell | Tris |
| --- | --- | --- |
| body | 30 mm | 91 824 |
| wheel ×4 | 12 mm | 16 501 each |
| hub ×4 | 20 mm | ~8 200 each |
| **total** | | **190 644** (from 490 468) |

The wheels get a finer cell than the body because they are round, spinning and
close to the camera, where faceting shows immediately.

## Physics

Derived from the measured geometry rather than guessed:

* 2 450 kg, 56% on the front axle — and the centre of mass is placed to
  *match* that split (z = −0.074, which is 0.44 of the wheelbase behind the
  front axle). Getting this wrong made the springs carry 50/50 while the rest
  of the model assumed 56/44.
* springs sized for 1.35 Hz front / 1.44 Hz rear, which is pickup territory
  and much softer than the BMW's 1.85 Hz
* 240 mm travel, static sag 137 mm
* all-wheel drive with a 40/60 front/rear split and a 0.75 locking diff
* 620 Nm at 2 100 rpm, 5 200 rpm redline, shorter gearing
* off-road tread: peak grip 1.30 versus the BMW's 1.58, but a wider slip angle
  before it lets go

---

# The Defender 110

`Adventure+Ready+Defender+110.fbx` — 45 MB, uploaded to `main`.

## Animation

**None.** Same as every other asset: no curves, skinning, poses or takes. The
wheels are animated by the physics.

## What is in it

| | |
| --- | --- |
| Format | FBX 7400, 45.2 MB |
| Nodes | 590 |
| Meshes | 571 |
| Vertices | 857 242 |
| Polygons | 860 352 |

Every node is named `desirefx.me_NNN`, so nothing could be identified by name.
The parts were found geometrically instead: round meshes (two similar large
extents, one much smaller) that repeat at four mirrored positions.

```
front axle  x = +13.11        rear axle  x = -14.70
wheelbase   27.84 units       track      18.39 units
tyre outer  8.65 units diameter, hub centre at y = 7.93
```

## Establishing scale

The file is in arbitrary units. A real Defender 110 has a **2.79 m wheelbase**,
which fixes the scale at `2.79 / 27.84 = 0.10021`. Everything else then falls
out at the right size, which is the check that the assumption was correct:

| | Converted | Real 110 |
| --- | --- | --- |
| Wheelbase | 2.790 m | 2.79 m |
| Track | 1.837 m | 1.49 m (this one has wide off-road wheels) |
| Tyre diameter | 0.867 m | 0.78 m (255/70R16; this model is on larger AT tyres) |
| Length | 4.70 m | 4.76 m |
| Width | 2.20 m | 1.97 m (mirrors and arch flares) |
| Height | 1.97 m | 1.97 m |

Two further corrections were needed:

* the model is **yawed 2.90°** in plan — the axle centres are not aligned with
  the X axis — so it is straightened before the axis swap
* it is **not built around its own origin**: the mean hub centre sits at
  `(-0.797, *, 3.770)`, so the car rendered offset sideways until that was
  subtracted. With it removed the wheels come out symmetric at x = ±0.92.

## Decimation

860 k polygons is about nine times a normal game car:

| Part | Cell | Tris |
| --- | --- | --- |
| body | 28 mm | 134 663 |
| wheel ×4 | 11 mm | 4 738 each |
| hub ×4 | 11 mm | ~21 000 each |
| **total** | | **240 197** (from 860 352) |

## Physics

| | |
| --- | --- |
| Mass | 2 550 kg (kerb plus the rack, tent, spare and winch it is wearing) |
| Weight split | 51% front, with the centre of mass placed to match |
| Suspension | 260 mm travel, 1.31 Hz front / 1.29 Hz rear, sag 145 mm |
| Anti-roll | deliberately soft — articulation matters more than roll control |
| Drive | permanent 4WD, 50/50, 0.85 locking diff |
| Engine | 550 Nm at 1 600 rpm, 4 400 rpm redline |
| Tyres | all-terrain: peak grip 1.22, but 11.5° of slip angle before letting go |
| Aero | 1.62 m² drag area — it is a brick with a roof rack |

It sits high and leans, which is the point of a Defender, so the stability and
traction assists are turned up relative to the other two vehicles.
