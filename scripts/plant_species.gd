@tool
class_name PlantSpecies
extends Resource

## One kind of plant, rock or tree that the [Forest] scatters.
##
## This used to be a `const` Dictionary inside forest.gd, which meant the only
## way to change how many trees there were was to edit the script. It is a
## Resource now, so the whole list shows up in the inspector: you can change a
## count, a size range or a tint, tick Forest > rebuild, and see the result
## without touching any code. You can also add or remove entries entirely.
##
## Every field that used to be a magic number in the table is exported here.

@export_group("Identity")
## File name (without .gltf) inside Forest.asset_dir.
@export var mesh_name := "tree"
## How many to place. This is the number that matters for performance: each
## instance is a copy of the mesh, so the triangle count is count x mesh size.
@export_range(0, 4000, 1) var count := 100
## Skip this species entirely without deleting it from the list.
@export var enabled := true

@export_group("Size and placement")
## Random scale range. 1.0 is the mesh at its authored size.
@export var scale_min := 0.85
@export var scale_max := 1.5
## Steepest ground this will grow on. 0 = perfectly flat only, 1 = anywhere.
@export_range(0.0, 1.0) var max_slope := 0.34
## Only place further than this from the map centre (0 = no limit).
@export var min_distance := 0.0
## Only place closer than this to the map centre (0 = no limit).
@export var max_distance := 0.0
## Prefer slopes rather than flat ground. Rocks want this.
@export var prefers_slopes := false
## How far the prop leans to follow the ground normal. 0 = bolt upright.
@export_range(0.0, 1.0) var ground_lean := 0.5

@export_group("Look")
@export var tint := Color(0.30, 0.40, 0.20)
## Wind sway amplitude. 0 for rocks.
@export var wind := 0.05
## Vertices below this height (in the mesh's own space) do not sway, so trunks
## stay planted while canopies move.
@export var wind_anchor := 0.6
@export_range(0.0, 1.0) var roughness := 0.9
## Render both faces of every polygon.
##
## Needed for flat foliage cards - a fern is a single-sided plane and vanishes
## from behind without it. Wrong for closed shapes: a trunk or a rock has back
## faces that can never be seen, so drawing them is pure waste. Measured at
## 49% of all visible vegetation triangles.
##
## It also has a hidden cost. render_forward_clustered.cpp:3795 requires
## cull_mode == CULL_BACK before a surface can use the shared depth-only
## shadow material and the importer's position-only shadow mesh; two-sided
## surfaces run the full shader into the shadow map and re-fetch the whole
## 52-byte vertex instead of 12 bytes.
@export var two_sided := true

@export_group("Performance")
## Stop drawing past this distance in metres. 0 = always draw.
@export var cull_distance := 100.0
## Mesh LOD bias. Higher = drops to cheaper detail sooner.
@export var lod_bias := 1.0
## Casting shadows is by far the most expensive thing a prop can do: a shadow
## caster is redrawn once per shadow cascade and shadow rendering ignores the
## cull distance entirely. Leave this off for anything that is not a big,
## close tree.
@export var cast_shadows := false

@export_group("Collision")
## Solid props stop the car. Soft ones are bent out of the way by the shader.
@export var solid := false
## Radius of the collider in metres at scale 1.0. 0 = derive it from the mesh.
@export var collision_radius := 0.0
## Height of the mesh in metres at scale 1.0. Filled in from the asset when the
## forest is built; used to decide whether an instance is big enough to be
## worth colliding with.
@export var mesh_height := 0.0


## Convenience constructor used to build the default set.
static func make(values: Dictionary) -> PlantSpecies:
	var s := PlantSpecies.new()
	for key in values:
		s.set(String(key), values[key])
	return s


## Actual world height of an instance at the given scale factor.
func height_at(factor: float) -> float:
	return mesh_height * factor
