extends Node3D

## Stage 2 world: hilly terrain, forest and props.
##
## The flat 200 x 200 m plate from stage 1 is gone. The ground is now a
## procedural heightfield ([Terrain]) with real surface types, and the
## vegetation ([Forest]) is scattered over it. This script only adds the
## man-made test props - a ramp and some kerb blocks near the spawn - and
## seats them on whatever height the terrain generated.

## Where the test props are placed, relative to the spawn.
@export var prop_area := 40.0

var _terrain: Terrain


func _ready() -> void:
	var found := get_tree().get_nodes_in_group("terrain")
	if not found.is_empty():
		_terrain = found[0] as Terrain
	_build_props()


## Returns the terrain height at a point, or 0 if there is no terrain.
func _ground(x: float, z: float) -> float:
	return _terrain.sample_height(x, z) if _terrain else 0.0


## A ramp and some kerbs near the spawn, so the suspension can be seen working
## on something with known dimensions rather than only on the hills.
func _build_props() -> void:
	var props := Node3D.new()
	props.name = "Props"
	add_child(props)

	# A ramp to launch off, pitched about X so one end is buried.
	var ramp_z := -prop_area
	_add_box(props, Vector3(0.0, _ground(0.0, ramp_z) + 0.55, ramp_z),
		Vector3(8.0, 1.2, 11.0), Color(0.5, 0.46, 0.4),
		Vector3(deg_to_rad(-11.0), 0.0, 0.0))

	# Speed bumps.
	for i in 3:
		var z := 10.0 + i * 7.0
		_add_box(props, Vector3(-18.0, _ground(-18.0, z) + 0.05, z),
			Vector3(9.0, 0.1, 0.6), Color(0.66, 0.6, 0.28), Vector3.ZERO)

	# Kerb blocks in a ring, yawed so they are not all axis aligned.
	for i in 6:
		var a := TAU * i / 6.0
		var x := cos(a) * 24.0
		var z := sin(a) * 24.0 + 18.0
		_add_box(props, Vector3(x, _ground(x, z) + 0.4, z),
			Vector3(2.4, 0.8, 2.4), Color(0.46, 0.48, 0.5), Vector3(0.0, a, 0.0))


func _add_box(parent: Node3D, pos: Vector3, size: Vector3, colour: Color,
		euler: Vector3) -> void:
	var body := StaticBody3D.new()
	body.position = pos
	body.rotation = euler
	body.collision_layer = 1
	body.collision_mask = 1

	var col := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = size
	col.shape = box
	body.add_child(col)

	var mesh := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mesh.mesh = bm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = colour
	mat.roughness = 0.88
	mesh.material_override = mat
	mesh.gi_mode = GeometryInstance3D.GI_MODE_STATIC
	body.add_child(mesh)

	parent.add_child(body)
