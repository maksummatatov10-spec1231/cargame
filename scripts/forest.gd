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

const WIND_SHADER := """
shader_type spatial;
// depth_prepass_alpha runs an alpha-discard prepass. This shader is fully
// opaque and never writes ALPHA, so the prepass had nothing to keep and the
// plants rendered invisible while their collision still worked - you could
// drive through trees you could not see. Plain opaque rendering is correct
// here, and cheaper.
render_mode cull_disabled, diffuse_burley;

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

## Species to scatter: scene, count, scale range, slope limit, surfaces.
const SPECIES := [
	# Three detail bands by distance from the map centre, where the player
	# starts. 420 full-detail trees would be 11.9 M triangles on their own.
	#
	# Only the near band casts shadows. Shadow rendering ignores
	# visibility_range and redraws every caster once per cascade, so letting
	# all 420 cast turned 12.5 M triangles into 37.5 M of shadow work per
	# frame - that was the lag, and it dwarfed everything else in the scene.
	{"name": "tree", "count": 70, "scale": [0.85, 1.5], "max_slope": 0.34,
		"tint": Color(0.30, 0.40, 0.20), "collide": true, "radius": 0.55,
		"anchor": 1.2, "wind": 0.035, "max_dist": 80.0, "cull": 170.0,
		"shadows": true},
	{"name": "tree_lod", "count": 180, "scale": [0.85, 1.5], "max_slope": 0.34,
		"tint": Color(0.29, 0.39, 0.19), "collide": true, "radius": 0.55,
		"anchor": 1.2, "wind": 0.035, "min_dist": 80.0, "max_dist": 170.0,
		"cull": 240.0, "lod_bias": 2.0},
	{"name": "tree_far", "count": 220, "scale": [0.85, 1.5], "max_slope": 0.34,
		"tint": Color(0.28, 0.38, 0.19), "collide": false, "radius": 0.55,
		"anchor": 1.2, "wind": 0.03, "min_dist": 170.0, "cull": 340.0,
		"lod_bias": 4.0},
	{"name": "fern_a", "count": 520, "scale": [0.7, 1.3], "max_slope": 0.42,
		"tint": Color(0.26, 0.40, 0.18), "collide": false, "radius": 0.0,
		"anchor": 0.1, "wind": 0.09, "cull": 75.0, "lod_bias": 2.5},
	{"name": "fern_b", "count": 380, "scale": [0.7, 1.25], "max_slope": 0.42,
		"tint": Color(0.24, 0.37, 0.17), "collide": false, "radius": 0.0,
		"anchor": 0.1, "wind": 0.09, "cull": 75.0, "lod_bias": 2.5},
	{"name": "bush_a", "count": 620, "scale": [0.8, 1.6], "max_slope": 0.5,
		"tint": Color(0.29, 0.36, 0.18), "collide": false, "radius": 0.0,
		"anchor": 0.05, "wind": 0.08, "cull": 70.0, "lod_bias": 2.5},
	{"name": "bush_b", "count": 460, "scale": [0.8, 1.5], "max_slope": 0.5,
		"tint": Color(0.31, 0.38, 0.19), "collide": false, "radius": 0.0,
		"anchor": 0.05, "wind": 0.08, "cull": 70.0, "lod_bias": 2.5},
	{"name": "plant", "count": 340, "scale": [0.8, 1.4], "max_slope": 0.4,
		"tint": Color(0.33, 0.42, 0.2), "collide": false, "radius": 0.0,
		"anchor": 0.05, "wind": 0.1, "cull": 70.0, "lod_bias": 2.5},
	{"name": "grass_tuft", "count": 1500, "scale": [0.8, 1.8], "max_slope": 0.38,
		"tint": Color(0.34, 0.42, 0.19), "collide": false, "radius": 0.0,
		"anchor": 0.0, "wind": 0.13, "cull": 55.0, "lod_bias": 3.0},
	{"name": "rock_a", "count": 70, "scale": [0.25, 0.7], "max_slope": 1.0,
		"tint": Color(0.40, 0.39, 0.37), "collide": true, "radius": 0.9,
		"anchor": 99.0, "wind": 0.0, "cull": 200.0, "shadows": true},
	{"name": "rock_b", "count": 80, "scale": [0.2, 0.6], "max_slope": 1.0,
		"tint": Color(0.38, 0.37, 0.36), "collide": true, "radius": 0.7,
		"anchor": 99.0, "wind": 0.0, "cull": 200.0},
	{"name": "rock_c", "count": 120, "scale": [0.15, 0.5], "max_slope": 1.0,
		"tint": Color(0.42, 0.41, 0.39), "collide": true, "radius": 0.5,
		"anchor": 99.0, "wind": 0.0, "cull": 200.0},
]

## Where the converted assets live.
@export var asset_dir := "res://assets/forest/"
## Keep this radius around the origin clear, so the car has room to start.
@export var clearing_radius := 30.0
## Only place collision bodies within this distance of the origin.
@export var collision_radius := 170.0
## Reproducible layout.
@export var scatter_seed := 90210

## Tick this in the editor to re-scatter after changing anything above.
@export var rebuild := false:
	set(value):
		rebuild = false
		if is_inside_tree():
			build()

var _terrain: Terrain
var _rng := RandomNumberGenerator.new()
var _placed := 0
var _colliders := 0


func _ready() -> void:
	build()


## Scatters the vegetation. Safe to call repeatedly.
func build() -> void:
	for child in get_children():
		remove_child(child)
		child.queue_free()
	_placed = 0
	_colliders = 0

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

	for species in SPECIES:
		_scatter(species)

	print("Forest: %d plants and rocks, %d with collision" % [_placed, _colliders])


func _scatter(species: Dictionary) -> void:
	var path: String = asset_dir + String(species["name"]) + ".gltf"
	if not ResourceLoader.exists(path):
		push_warning("Forest: missing asset %s" % path)
		return
	var packed := load(path) as PackedScene
	if packed == null:
		return
	var mesh := _find_mesh(packed.instantiate())
	if mesh == null:
		push_warning("Forest: no mesh inside %s" % path)
		return

	var extent: float = _terrain.size * 0.5 - 6.0
	var transforms: Array[Transform3D] = []
	var attempts: int = int(species["count"]) * 4
	var wanted: int = int(species["count"])
	# Pulled out of a Dictionary, so a malformed entry would otherwise crash on
	# the first instance rather than being reported here.
	var scale_range: Array = species.get("scale", [1.0, 1.0])
	if scale_range.size() < 2:
		push_warning("Forest: bad scale range for %s" % species.get("name", "?"))
		scale_range = [1.0, 1.0]
	var max_slope: float = species["max_slope"]

	for _i in attempts:
		if transforms.size() >= wanted:
			break
		var x := _rng.randf_range(-extent, extent)
		var z := _rng.randf_range(-extent, extent)
		var from_centre := Vector2(x, z).length()
		if from_centre < clearing_radius:
			continue
		# Detail bands: the full-detail mesh is only used close in.
		if species.has("max_dist") and from_centre > float(species["max_dist"]):
			continue
		if species.has("min_dist") and from_centre < float(species["min_dist"]):
			continue
		var slope := _terrain.sample_slope(x, z)
		if slope > max_slope:
			continue
		# Rocks want slopes; plants want gentler ground.
		if String(species["name"]).begins_with("rock"):
			if slope < 0.12 and _rng.randf() > 0.35:
				continue
		var surface := _terrain.sample_surface(x, z)
		if surface == Terrain.Surface.ROCK and not String(species["name"]).begins_with("rock"):
			if _rng.randf() > 0.15:
				continue

		var y := _terrain.sample_height(x, z)
		var s := _rng.randf_range(float(scale_range[0]), float(scale_range[1]))
		# Named xform, not basis: "basis" shadows Node3D.basis.
		var xform := Basis(Vector3.UP, _rng.randf_range(0.0, TAU))
		# Sit props on the slope rather than standing them all bolt upright.
		var normal := _terrain.sample_normal(x, z)
		var lean: float = 0.35 if String(species["name"]).begins_with("rock") else 0.5
		var up := Vector3.UP.lerp(normal, lean).normalized()
		var tilt := Basis(Vector3.UP.cross(up).normalized() if up != Vector3.UP else Vector3.RIGHT,
			Vector3.UP.angle_to(up)) if up != Vector3.UP else Basis()
		xform = tilt * xform
		xform = xform.scaled(Vector3(s, s, s))
		transforms.append(Transform3D(xform, Vector3(x, y, z)))

	if transforms.is_empty():
		return

	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = mesh
	mm.instance_count = transforms.size()
	for i in transforms.size():
		mm.set_instance_transform(i, transforms[i])

	var mmi := MultiMeshInstance3D.new()
	mmi.name = String(species["name"])
	mmi.multimesh = mm
	mmi.material_override = _make_material(species)

	# Shadows were the single biggest cost in the scene and it was not close.
	# A shadow-casting instance is re-drawn once per shadow cascade, and shadow
	# rendering ignores visibility_range entirely, so every full-detail tree on
	# the whole 400 m map was being drawn three more times whether it was on
	# screen or not: 12.5 M triangles of geometry becoming 37.5 M of shadow
	# work every frame. Only the near, full-detail band casts now.
	mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	if bool(species.get("shadows", false)):
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON

	# Distance culling. Small plants are invisible long before they are far
	# away, so drawing them at 300 m is pure waste; the fade margin stops them
	# popping. This is the single biggest saving on a weak machine.
	var cull: float = float(species.get("cull", 0.0))
	if cull > 0.0:
		mmi.visibility_range_end = cull
		mmi.visibility_range_end_margin = cull * 0.15
		mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
	mmi.extra_cull_margin = 8.0
	# Let Godot skip lighting maths on things too far to matter.
	mmi.lod_bias = float(species.get("lod_bias", 1.0))
	add_child(mmi)
	if Engine.is_editor_hint() and get_tree() != null:
		mmi.owner = get_tree().edited_scene_root
	_placed += transforms.size()

	if bool(species["collide"]):
		_add_colliders(transforms, float(species["radius"]), String(species["name"]))


## Trunks and rocks get a simple capsule or sphere. A convex hull per tree
## would be more accurate, but a car hitting a tree only ever touches the
## trunk, and hundreds of hulls would slow the broadphase down for nothing.
func _add_colliders(transforms: Array[Transform3D], radius: float,
		species_name: String) -> void:
	var body := StaticBody3D.new()
	body.name = species_name + "_collision"
	body.collision_layer = 1
	body.collision_mask = 1
	var phys := PhysicsMaterial.new()
	phys.friction = 0.9
	phys.bounce = 0.05
	body.physics_material_override = phys
	add_child(body)
	if Engine.is_editor_hint() and get_tree() != null:
		body.owner = get_tree().edited_scene_root

	var is_rock := species_name.begins_with("rock")
	for t in transforms:
		if t.origin.length() > collision_radius:
			continue
		var col := CollisionShape3D.new()
		# Named prop_scale: "scale" shadows Node3D.scale.
		var prop_scale := t.basis.get_scale().y
		if is_rock:
			var sphere := SphereShape3D.new()
			sphere.radius = radius * prop_scale
			col.shape = sphere
			col.position = t.origin + Vector3.UP * radius * prop_scale * 0.4
		else:
			var cap := CapsuleShape3D.new()
			cap.radius = radius * prop_scale * 0.5
			cap.height = 6.0 * prop_scale
			col.shape = cap
			col.position = t.origin + Vector3.UP * 3.0 * prop_scale
		body.add_child(col)
		_colliders += 1


func _make_material(species: Dictionary) -> ShaderMaterial:
	var shader := Shader.new()
	shader.code = WIND_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = shader
	var tint: Color = species["tint"]
	mat.set_shader_parameter("tint", Vector3(tint.r, tint.g, tint.b))
	mat.set_shader_parameter("wind_strength", float(species["wind"]))
	mat.set_shader_parameter("anchor_height", float(species["anchor"]))
	mat.set_shader_parameter("wind_speed", _rng.randf_range(0.9, 1.4))
	var is_rock := String(species["name"]).begins_with("rock")
	mat.set_shader_parameter("roughness_value", 0.6 if is_rock else 0.9)
	return mat


func _find_mesh(node: Node) -> Mesh:
	if node is MeshInstance3D:
		return (node as MeshInstance3D).mesh
	for child in node.get_children():
		var m := _find_mesh(child)
		if m != null:
			return m
	return null
