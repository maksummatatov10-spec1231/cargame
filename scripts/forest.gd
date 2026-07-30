@tool
class_name Forest
extends Node3D

## Scatters the imported vegetation over the terrain.
##
## The uploaded assets are static meshes with no animation, skinning or takes
## (verified in tools/asset_report.py), so:
##   * they are drawn with [MultiMeshInstance3D] - one draw call per species
##     however many there are, which is what makes thousands of plants viable
##   * the wind is a vertex shader, since there is no rig to animate
##
## Placement follows the ground: trees avoid steep slopes and the spawn
## clearing, rocks prefer slopes, grass fills the gentle ground. Everything is
## seated on the terrain height and tilted to its normal.
##
## Only the trees and rocks get collision, and only within a radius of the
## start, because a StaticBody per grass tuft would cost far more than it is
## worth and the car cannot reach the far corners quickly anyway.

# The vegetation shader is assembled per species rather than shared verbatim,
# because two render_mode choices have a large, measurable cost and the right
# choice differs between a tree trunk and a fern card.
#
# CULLING. The old shader was cull_disabled for everything. For a closed shape
# - a trunk, a branch, a rock - the back faces are never visible, so drawing
# them is pure waste: measured at 419,135 of the 859,479 visible vegetation
# triangles, i.e. 49%. Worse, render_forward_clustered.cpp:3795 requires
# cull_mode == CULL_BACK before a surface may use FLAG_USES_SHARED_SHADOW_MATERIAL.
# Without that flag the shadow pass runs this whole shader instead of the
# trivial depth-only default, and mesh_get_shadow_mesh() is never used, so the
# shadow pass re-fetches the full 52-byte vertex instead of the 12-byte
# position-only one. Flat foliage genuinely needs two-sided rendering; solid
# shapes do not.
#
# CRUSHING. `if (crushable)` is a uniform branch, so the GPU still carries the
# 8-iteration loop in every vegetation vertex shader, trees included. Species
# that can never be crushed now get a shader compiled without it at all.
const SHADER_HEADER := """
shader_type spatial;
// depth_prepass_alpha runs an alpha-discard prepass. This shader is fully
// opaque and never writes ALPHA, so the prepass had nothing to keep and the
// plants rendered invisible while their collision still worked - you could
// drive through trees you could not see. Plain opaque rendering is correct
// here, and cheaper.
render_mode %s, diffuse_burley;
"""

const SHADER_UNIFORMS := """
uniform vec3 tint : source_color = vec3(1.0);
uniform float wind_strength = 0.06;
uniform float wind_speed = 1.1;
uniform float wind_scale = 0.18;
// Vertices below this height (object space) do not move, so trunks stay put
// while canopies sway.
uniform float anchor_height = 0.6;
uniform float roughness_value = 0.85;

varying float sway_amount;

void vertex() {
	// The assets have no bones, so the wind has to be geometric. Two offset
	// sine waves in world space keep neighbouring plants out of sync.
	vec3 world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	float h = max(VERTEX.y - anchor_height, 0.0);
	float phase = world.x * wind_scale + world.z * wind_scale * 0.7;
	float t = TIME * wind_speed;
	float sway = sin(t + phase) * 0.7 + sin(t * 1.7 + phase * 2.3) * 0.3;
	sway_amount = sway;
	VERTEX.x += sway * h * wind_strength;
	VERTEX.z += cos(t * 0.9 + phase) * h * wind_strength * 0.6;
"""

## Pasted into the vertex shader only for species that can be crushed.
const SHADER_CRUSH := """
	// Find the wheel pressing hardest on this plant. Only the horizontal
	// distance matters: a wheel directly above should flatten it whatever
	// the ride height.
	float best = 0.0;
	vec3 push = vec3(0.0);
	for (int i = 0; i < 8; i++) {
		float strength = crush_points[i].w;
		if (strength <= 0.001) {
			continue;
		}
		vec2 delta = world.xz - crush_points[i].xz;
		float d = length(delta);
		if (d > crush_radius) {
			continue;
		}
		// Full effect under the wheel, easing off towards the edge.
		float amount = (1.0 - smoothstep(0.0, crush_radius, d)) * strength;
		if (amount > best) {
			best = amount;
			// Lay the plant away from the wheel centre, so it folds in the
			// direction the tyre rolled over it rather than collapsing
			// straight down.
			vec2 dir = d > 0.001 ? delta / d : vec2(1.0, 0.0);
			push = vec3(dir.x, 0.0, dir.y);
		}
	}
	if (best > 0.0) {
		// Bend from the base: the top travels furthest, the roots stay put.
		float bend = best * h;
		VERTEX.xz += push.xz * bend * 1.35;
		VERTEX.y -= bend * 0.75;
	}
"""

## Uniforms only the crushable variant declares.
const SHADER_CRUSH_UNIFORMS := """
// Crush points, one per wheel touching the ground: xyz is the world position
// of the contact patch, w is how strongly it is pressing (1 under the wheel,
// fading to 0 as the plant recovers). Small plants have no collision, so this
// is what makes driving over them look like anything at all.
uniform vec4 crush_points[8];
uniform float crush_radius = 1.4;
"""

const SHADER_TAIL := """
}

void fragment() {
	ALBEDO = tint * (0.92 + 0.08 * sway_amount);
	ROUGHNESS = roughness_value;
	SPECULAR = 0.15;
	// Leaves catch a little light from behind, which is most of what makes
	// foliage read as foliage rather than cardboard.
	BACKLIGHT = vec3(0.22, 0.28, 0.14);
}
"""

## The default set of species, used when the `species` list below is empty.
##
## These are only defaults now. The live list is an exported array of
## [PlantSpecies] resources, so every count, size, colour and cull distance is
## editable in the inspector without touching this file.
##
## The three tree entries are distance bands of the same tree: only the near
## band uses the full-detail mesh and only the near band casts shadows. A
## shadow caster is redrawn once per shadow cascade and shadow rendering
## ignores the cull distance entirely, so letting all 470 trees cast turned
## 12.5 M triangles into 37.5 M of shadow work every frame.
const DEFAULT_SPECIES := [
	{"mesh_name": "tree", "count": 70, "scale_min": 0.85, "scale_max": 1.5,
		"max_slope": 0.34, "tint": Color(0.30, 0.40, 0.20), "solid": true,
		"collision_radius": 0.55, "wind_anchor": 1.2, "wind": 0.035,
		"max_distance": 80.0, "cull_distance": 170.0, "cast_shadows": true,
		"mesh_height": 6.87, "two_sided": false},
	{"mesh_name": "tree_lod", "count": 180, "scale_min": 0.85, "scale_max": 1.5,
		"max_slope": 0.34, "tint": Color(0.29, 0.39, 0.19), "solid": true,
		"collision_radius": 0.55, "wind_anchor": 1.2, "wind": 0.035,
		"min_distance": 80.0, "max_distance": 170.0, "cull_distance": 240.0,
		"lod_bias": 2.0, "mesh_height": 6.84, "two_sided": false},
	{"mesh_name": "tree_far", "count": 220, "scale_min": 0.85, "scale_max": 1.5,
		"max_slope": 0.34, "tint": Color(0.28, 0.38, 0.19), "solid": false,
		"wind_anchor": 1.2, "wind": 0.03, "min_distance": 170.0,
		"cull_distance": 340.0, "lod_bias": 4.0, "mesh_height": 6.81, "two_sided": false},
	{"mesh_name": "fern_a", "count": 520, "scale_min": 0.7, "scale_max": 1.3,
		"max_slope": 0.42, "tint": Color(0.26, 0.40, 0.18), "solid": false,
		"wind_anchor": 0.1, "wind": 0.09, "cull_distance": 75.0,
		"lod_bias": 2.5, "mesh_height": 1.73},
	{"mesh_name": "fern_b", "count": 380, "scale_min": 0.7, "scale_max": 1.25,
		"max_slope": 0.42, "tint": Color(0.24, 0.37, 0.17), "solid": false,
		"wind_anchor": 0.1, "wind": 0.09, "cull_distance": 75.0,
		"lod_bias": 2.5, "mesh_height": 1.74},
	{"mesh_name": "bush_a", "count": 620, "scale_min": 0.8, "scale_max": 1.6,
		"max_slope": 0.5, "tint": Color(0.29, 0.36, 0.18), "solid": false,
		"wind_anchor": 0.05, "wind": 0.08, "cull_distance": 70.0,
		"lod_bias": 2.5, "mesh_height": 1.18},
	{"mesh_name": "bush_b", "count": 460, "scale_min": 0.8, "scale_max": 1.5,
		"max_slope": 0.5, "tint": Color(0.31, 0.38, 0.19), "solid": false,
		"wind_anchor": 0.05, "wind": 0.08, "cull_distance": 70.0,
		"lod_bias": 2.5, "mesh_height": 1.18},
	{"mesh_name": "plant", "count": 340, "scale_min": 0.8, "scale_max": 1.4,
		"max_slope": 0.4, "tint": Color(0.33, 0.42, 0.2), "solid": false,
		"wind_anchor": 0.05, "wind": 0.1, "cull_distance": 70.0,
		"lod_bias": 2.5, "mesh_height": 1.27},
	{"mesh_name": "grass_tuft", "count": 1500, "scale_min": 0.55,
		"scale_max": 0.85, "max_slope": 0.38, "tint": Color(0.34, 0.42, 0.19),
		"solid": false, "wind_anchor": 0.0, "wind": 0.13,
		"cull_distance": 55.0, "lod_bias": 3.0, "mesh_height": 0.56},
	# Three species recovered from untitled.fbx, which had never been used.
	# It is the only uploaded asset containing a flower, and its grasses are
	# a different shape from the existing tuft, which breaks up the repetition
	# of a single ground-cover mesh.
	#
	# The originals are dense clumps - 2,298 / 1,467 / 448 triangles for
	# plants about 0.2 m tall - so they were run through
	# tools/mesh_decimate.py first: 342 / 122 / 78 triangles, with the
	# bounding box preserved to within 3 cm and no degenerate faces.
	{"mesh_name": "grass_wide", "count": 420, "scale_min": 0.8,
		"scale_max": 1.6, "max_slope": 0.36, "tint": Color(0.33, 0.44, 0.20),
		"solid": false, "wind_anchor": 0.0, "wind": 0.12,
		"cull_distance": 48.0, "lod_bias": 3.0, "mesh_height": 0.22},
	{"mesh_name": "grass_fine", "count": 700, "scale_min": 0.8,
		"scale_max": 1.7, "max_slope": 0.38, "tint": Color(0.36, 0.46, 0.21),
		"solid": false, "wind_anchor": 0.0, "wind": 0.14,
		"cull_distance": 45.0, "lod_bias": 3.0, "mesh_height": 0.21},
	# Flowers are sparse on purpose: a meadow of solid daisies looks wrong,
	# and scattered ones are what the eye reads as variety.
	{"mesh_name": "daisy", "count": 260, "scale_min": 0.9, "scale_max": 1.5,
		"max_slope": 0.30, "tint": Color(0.86, 0.88, 0.72), "solid": false,
		"wind_anchor": 0.0, "wind": 0.16, "cull_distance": 40.0,
		"lod_bias": 3.0, "mesh_height": 0.17},
	{"mesh_name": "rock_a", "count": 70, "scale_min": 0.25, "scale_max": 0.7,
		"max_slope": 1.0, "tint": Color(0.40, 0.39, 0.37), "solid": true,
		"collision_radius": 0.9, "wind_anchor": 99.0, "wind": 0.0,
		"cull_distance": 200.0, "cast_shadows": true, "prefers_slopes": true,
		"ground_lean": 0.35, "roughness": 0.6, "mesh_height": 4.18, "two_sided": false},
	{"mesh_name": "rock_b", "count": 80, "scale_min": 0.45, "scale_max": 0.8,
		"max_slope": 1.0, "tint": Color(0.38, 0.37, 0.36), "solid": true,
		"collision_radius": 0.7, "wind_anchor": 99.0, "wind": 0.0,
		"cull_distance": 200.0, "prefers_slopes": true, "ground_lean": 0.35,
		"roughness": 0.6, "mesh_height": 2.65, "two_sided": false},
	{"mesh_name": "rock_c", "count": 120, "scale_min": 0.40, "scale_max": 0.7,
		"max_slope": 1.0, "tint": Color(0.42, 0.41, 0.39), "solid": true,
		"collision_radius": 0.5, "wind_anchor": 99.0, "wind": 0.0,
		"cull_distance": 200.0, "prefers_slopes": true, "ground_lean": 0.35,
		"roughness": 0.6, "mesh_height": 3.29, "two_sided": false},
]

## Where the converted assets live.
@export var asset_dir := "res://assets/forest/"
## Keep this radius around the origin clear, so the car has room to start.
@export var clearing_radius := 30.0
## Only place collision bodies within this distance of the origin.
@export var collision_radius := 170.0
## Reproducible layout.
@export var scatter_seed := 90210
## Plants shorter than this are crushable: no collision, and they bend under
## the wheels instead of stopping the car dead.
@export var crushable_height := 0.5
## An instance shorter than this never gets a collider, whatever its species
## says. The scale ranges mean one species can produce both a full-size tree
## and a sapling, and a waist-high invisible obstacle is the single most
## annoying thing a map can have.
@export var min_solid_height := 1.6

## Every plant, tree and rock on the map, fully editable in the inspector.
##
## Open the Forest node, expand Species, and you get one entry per kind with
## its own count, scale range, tint, cull distance and collision settings. Add
## an entry to introduce a new plant, set count to 0 or untick enabled to
## remove one, then tick Rebuild.
##
## Left empty it is filled from DEFAULT_SPECIES on the first build, so the map
## still looks right out of the box.
@export var species: Array[PlantSpecies] = []

## Scales every count at once, for quickly trading detail against frame rate
## without editing each species. 0.5 halves the whole map.
@export_range(0.05, 2.0, 0.05) var density := 1.0

## Restores the built-in species list, discarding any edits. Use this if you
## have changed something into a state you cannot get back from.
@export var reset_species := false:
	set(value):
		reset_species = false
		species = _default_species()
		if is_inside_tree():
			build()

## Tick this in the editor to re-scatter after changing anything above.
@export var rebuild := false:
	set(value):
		rebuild = false
		if is_inside_tree():
			build()

var _terrain: Terrain
var _crusher: CrushablePlants
var _rng := RandomNumberGenerator.new()
var _placed := 0
var _colliders := 0
var _density_scale := 1.0
## Shaders cached by their code, so the twelve species share a handful of
## compiled programs instead of one each. Godot sorts draws by material and
## shader, so fewer distinct shaders means fewer state changes.
var _shader_cache: Dictionary = {}


func _ready() -> void:
	build()


## Scatters the vegetation. Safe to call repeatedly.
func build() -> void:
	for child in get_children():
		remove_child(child)
		child.queue_free()
	_placed = 0
	_colliders = 0
	_shader_cache.clear()

	var found := get_tree().get_nodes_in_group("terrain")
	if found.is_empty():
		# In the editor the terrain may not have built yet on the first pass.
		var sibling := get_parent().get_node_or_null("Terrain") if get_parent() else null
		if sibling == null:
			push_warning("Forest: no terrain in the scene")
			return
		_terrain = sibling as Terrain
	else:
		_terrain = found[0] as Terrain
	if _terrain == null:
		return
	if _terrain.heights.is_empty():
		_terrain.build()
	_rng.seed = scatter_seed

	# The node that tells the crushable plants where the wheels are.
	if not Engine.is_editor_hint():
		_crusher = CrushablePlants.new()
		_crusher.name = "PlantCrusher"
		_crusher.max_height = crushable_height
		add_child(_crusher)

	if species.is_empty():
		species = _default_species()

	# The settings menu owns the density at runtime; the exported value is the
	# author's baseline. Only applied in game, so editing the map in the
	# editor is not affected by whatever the player last chose.
	#
	# The autoload is reached through the scene tree rather than
	# Engine.has_singleton(), which would always be false here: verified in
	# main.cpp:3694, an autoload is registered as a *script language global
	# constant* and added as a child of the root, not as an engine singleton.
	_density_scale = maxf(0.01, density)
	if not Engine.is_editor_hint() and is_inside_tree():
		var settings := get_tree().root.get_node_or_null("GameSettings")
		if settings != null:
			_density_scale = maxf(0.01,
				density * float(settings.get("vegetation_density")))

	var skipped := 0
	for entry in species:
		if entry == null or not entry.enabled:
			continue
		skipped += _scatter(entry)

	print("Forest: %d plants and rocks, %d with collision, %d too small to be solid"
		% [_placed, _colliders, skipped])


## Builds the built-in list as real resources.
func _default_species() -> Array[PlantSpecies]:
	var out: Array[PlantSpecies] = []
	for entry in DEFAULT_SPECIES:
		out.append(PlantSpecies.make(entry))
	return out


## Rotation that tips UP onto the given normal, safe for near-flat ground.
##
## The previous version guarded with `up != Vector3.UP`, an exact float
## comparison, and then did Vector3.UP.cross(up).normalized(). On ground that
## is merely *close* to flat - which the bilinear terrain normals produce
## constantly - the cross product is tiny: its square underflows to a denormal
## or to zero, normalising it returns a non-unit vector or (0,0,0), and
## Basis(axis, angle) throws "must be normalized". Same root cause as the
## wheel contact normal.
##
## Comparing the angle is the honest test: below it there is nothing to
## rotate, so return identity instead of building a degenerate axis.
static func _tilt_towards(up: Vector3) -> Basis:
	var axis := Vector3.UP.cross(up)
	# Guard on the axis length itself rather than on the vectors, since that
	# is the quantity that has to be safely normalisable.
	if axis.length_squared() < 1e-12:
		return Basis()
	return Basis(axis.normalized(), Vector3.UP.angle_to(up))


## Loads the single mesh out of a converted glTF, or null with a warning.
func _load_mesh(mesh_name: String) -> Mesh:
	var path := asset_dir + mesh_name + ".gltf"
	if not ResourceLoader.exists(path):
		push_warning("Forest: missing asset %s" % path)
		return null
	var packed := load(path) as PackedScene
	if packed == null:
		push_warning("Forest: %s is not a scene" % path)
		return null
	var mesh := _find_mesh(packed.instantiate())
	if mesh == null:
		push_warning("Forest: no mesh inside %s" % path)
	return mesh


## Places one species. Returns how many instances were denied collision for
## being too small - see _add_colliders().
func _scatter(entry: PlantSpecies) -> int:
	var mesh := _load_mesh(entry.mesh_name)
	if mesh == null:
		return 0

	# The authored height decides what counts as a small plant, so read it from
	# the mesh rather than trusting a number typed into the inspector. This is
	# what stops a knee-high prop from being given a car-stopping collider.
	var aabb := mesh.get_aabb()
	if aabb.size.y > 0.0:
		entry.mesh_height = aabb.size.y
	if entry.collision_radius <= 0.0:
		entry.collision_radius = maxf(aabb.size.x, aabb.size.z) * 0.25

	var extent: float = _terrain.size * 0.5 - 6.0
	var transforms: Array[Transform3D] = []
	var wanted := maxi(0, int(round(entry.count * _density_scale)))
	if wanted == 0:
		return 0
	var attempts := wanted * 4
	var lo := minf(entry.scale_min, entry.scale_max)
	var hi := maxf(entry.scale_min, entry.scale_max)
	var is_rock := entry.mesh_name.begins_with("rock")

	# A solid prop is never allowed to be generated below the size at which it
	# would get a collider, because the alternative is a rock you can see and
	# drive straight through. rock_a is authored 4.18 m tall with a scale
	# range starting at 0.25, which is a 1.05 m boulder - visible, waist high,
	# and exactly the sort of thing that felt like hitting a small tree. The
	# floor is raised instead of the instance being dropped, so the map keeps
	# the same number of rocks.
	if entry.solid and entry.mesh_height > 0.0:
		var floor_scale := min_solid_height / entry.mesh_height
		lo = maxf(lo, floor_scale)
		hi = maxf(hi, lo)

	for _i in attempts:
		if transforms.size() >= wanted:
			break
		var x := _rng.randf_range(-extent, extent)
		var z := _rng.randf_range(-extent, extent)
		var from_centre := Vector2(x, z).length()
		if from_centre < clearing_radius:
			continue
		# Detail bands: the full-detail mesh is only used close in.
		if entry.max_distance > 0.0 and from_centre > entry.max_distance:
			continue
		if entry.min_distance > 0.0 and from_centre < entry.min_distance:
			continue
		# Keep the road clear. Solid props are pushed right off it plus a
		# margin; ground cover is allowed onto the verge but not the running
		# surface, which is what makes the edge look used rather than mown.
		var road := _terrain.road_offset(x, z)
		var clearance := _terrain.road_width \
			+ (_terrain.road_shoulder if entry.solid else 0.6)
		if road < clearance:
			continue

		var slope := _terrain.sample_slope(x, z)
		if slope > entry.max_slope:
			continue
		if entry.prefers_slopes and slope < 0.12 and _rng.randf() > 0.35:
			continue
		var surface := _terrain.sample_surface(x, z)
		if surface == Terrain.Surface.ROCK and not is_rock:
			if _rng.randf() > 0.15:
				continue

		var y := _terrain.sample_height(x, z)
		var s := _rng.randf_range(lo, hi)
		# Named xform, not basis: "basis" shadows Node3D.basis.
		var xform := Basis(Vector3.UP, _rng.randf_range(0.0, TAU))
		# Sit props on the slope rather than standing them all bolt upright.
		var normal := _terrain.sample_normal(x, z)
		var up := Vector3.UP.lerp(normal, entry.ground_lean).normalized()
		xform = _tilt_towards(up) * xform
		xform = xform.scaled(Vector3(s, s, s))
		transforms.append(Transform3D(xform, Vector3(x, y, z)))

	if transforms.is_empty():
		return 0

	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = mesh
	mm.instance_count = transforms.size()
	for i in transforms.size():
		mm.set_instance_transform(i, transforms[i])

	var mmi := MultiMeshInstance3D.new()
	mmi.name = entry.mesh_name
	mmi.multimesh = mm
	mmi.material_override = _make_material(entry)

	# Shadows were the single biggest cost in the scene and it was not close.
	# A shadow-casting instance is re-drawn once per shadow cascade, and shadow
	# rendering ignores visibility_range entirely, so every full-detail tree on
	# the whole 400 m map was being drawn three more times whether it was on
	# screen or not. Only the near, full-detail band casts now.
	mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON \
		if entry.cast_shadows else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF

	# Distance culling. Small plants are invisible long before they are far
	# away, so drawing them at 300 m is pure waste; the fade margin stops them
	# popping.
	if entry.cull_distance > 0.0:
		mmi.visibility_range_end = entry.cull_distance
		mmi.visibility_range_end_margin = entry.cull_distance * 0.15
		mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
	mmi.extra_cull_margin = 8.0
	mmi.lod_bias = entry.lod_bias
	add_child(mmi)
	if Engine.is_editor_hint() and get_tree() != null:
		mmi.owner = get_tree().edited_scene_root
	if _is_crushable(entry) and _crusher != null:
		_crusher.register_material(mmi.material_override)
	_placed += transforms.size()

	if entry.solid:
		return _add_colliders(transforms, entry)
	return 0


## Gives the solid props a collider. Returns how many instances were refused
## one for being too small.
##
## Two things were wrong here and both of them felt like "I crashed into a
## small tree that is not there":
##
##  1. The collider was sized from a number typed into the table, not from the
##     mesh. rock_a is authored 4.18 m tall but was given a 0.9 m sphere; at
##     its smallest scale (0.25) that is a 0.225 m ball sitting 0.09 m above
##     the ground - a knee-high invisible bump that catches the floor of the
##     car while the rock it belongs to looks like scenery. Colliders are now
##     derived from the mesh AABB, so what you see is what you hit.
##
##  2. Nothing checked how big an *instance* was. A species is marked solid as
##     a whole, but the scale range means the same species can produce a 6 m
##     tree and a 1 m sapling. Anything whose real height is below
##     min_solid_height now gets no collider at all and is registered as
##     crushable instead, so you flatten it.
func _add_colliders(transforms: Array[Transform3D], entry: PlantSpecies) -> int:
	var body := StaticBody3D.new()
	body.name = entry.mesh_name + "_collision"
	body.collision_layer = 1
	body.collision_mask = 1
	var phys := PhysicsMaterial.new()
	phys.friction = 0.9
	phys.bounce = 0.0
	body.physics_material_override = phys
	add_child(body)
	if Engine.is_editor_hint() and get_tree() != null:
		body.owner = get_tree().edited_scene_root

	var is_rock := entry.mesh_name.begins_with("rock")
	var too_small := 0
	for t in transforms:
		if t.origin.length() > collision_radius:
			continue
		# Named prop_scale: "scale" shadows Node3D.scale.
		var prop_scale := t.basis.get_scale().y
		var world_height := entry.mesh_height * prop_scale
		if world_height < min_solid_height:
			too_small += 1
			continue

		var col := CollisionShape3D.new()
		if is_rock:
			# A sphere sized to the rock, sunk so its top matches the mesh.
			# Half the visible height, centred at half the visible height,
			# means the collider reaches the ground and the summit and nothing
			# sticks out below.
			var r := minf(entry.collision_radius * prop_scale, world_height * 0.5)
			var sphere := SphereShape3D.new()
			sphere.radius = maxf(r, 0.05)
			col.shape = sphere
			col.position = t.origin + Vector3.UP * sphere.radius
		else:
			# A trunk: a thin capsule the full height of the tree. The capsule
			# height in Godot spans the whole shape, hemispheres included, so
			# it is the visible height and the centre is half of it.
			var cap := CapsuleShape3D.new()
			cap.radius = maxf(entry.collision_radius * prop_scale * 0.5, 0.05)
			cap.height = maxf(world_height, cap.radius * 2.0 + 0.01)
			col.shape = cap
			col.position = t.origin + Vector3.UP * cap.height * 0.5
		body.add_child(col)
		_colliders += 1

	if body.get_child_count() == 0:
		body.queue_free()
	return too_small


## True for plants the car should drive over rather than into.
##
## Anything not marked solid bends. Grass is under the height threshold
## outright; ferns and bushes are taller but they are still vegetation the car
## should flatten rather than clip through rigidly, so they bend too - just
## from higher up, which the shader handles through wind_anchor.
func _is_crushable(entry: PlantSpecies) -> bool:
	return not entry.solid


## Builds the exact shader a species needs and nothing more.
func _shader_for(entry: PlantSpecies) -> Shader:
	var crushable := _is_crushable(entry)
	var cull := "cull_disabled" if entry.two_sided else "cull_back"
	var code := SHADER_HEADER % cull
	code += SHADER_CRUSH_UNIFORMS if crushable else ""
	code += SHADER_UNIFORMS
	code += SHADER_CRUSH if crushable else ""
	code += SHADER_TAIL

	if _shader_cache.has(code):
		return _shader_cache[code]
	var shader := Shader.new()
	shader.code = code
	_shader_cache[code] = shader
	return shader


func _make_material(entry: PlantSpecies) -> ShaderMaterial:
	var mat := ShaderMaterial.new()
	mat.shader = _shader_for(entry)
	mat.set_shader_parameter("tint",
		Vector3(entry.tint.r, entry.tint.g, entry.tint.b))
	mat.set_shader_parameter("wind_strength", entry.wind)
	mat.set_shader_parameter("anchor_height", entry.wind_anchor)
	mat.set_shader_parameter("wind_speed", _rng.randf_range(0.9, 1.4))
	mat.set_shader_parameter("roughness_value", entry.roughness)
	return mat


func _find_mesh(node: Node) -> Mesh:
	if node is MeshInstance3D:
		return (node as MeshInstance3D).mesh
	for child in node.get_children():
		var m := _find_mesh(child)
		if m != null:
			return m
	return null
