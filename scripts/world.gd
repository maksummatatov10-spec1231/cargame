extends Node3D

## Builds the stage 1 test ground: a flat 200 x 200 m plate with a proper
## collision surface, a subtle grid so the sense of speed reads well, and a few
## reference objects so suspension travel is visible.

@export var ground_size := 200.0
## Physics surface used by the tyres. The plate is a StaticBody3D on layer 1,
## which is what the wheel raycasts look for.
@export var ground_colour := Color(0.34, 0.38, 0.31)

@onready var _car : Vehicle = $Car


func _ready() -> void:
	_build_ground()
	_build_props()


func _build_ground() -> void:
	var body := StaticBody3D.new()
	body.name = "Ground"
	body.collision_layer = 1
	body.collision_mask = 1

	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(ground_size, 2.0, ground_size)
	shape.shape = box
	shape.position = Vector3(0.0, -1.0, 0.0)
	body.add_child(shape)

	var mesh := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(ground_size, ground_size)
	plane.subdivide_width = 64
	plane.subdivide_depth = 64
	mesh.mesh = plane

	var mat := StandardMaterial3D.new()
	mat.albedo_color = ground_colour
	mat.roughness = 0.92
	mat.metallic = 0.0
	mat.uv1_scale = Vector3(ground_size * 0.5, ground_size * 0.5, 1.0)
	mat.albedo_texture = _make_grid_texture()
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	mesh.material_override = mat
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	body.add_child(mesh)

	# A physics material with realistic asphalt friction; the tyre model does the
	# real work but this keeps body-on-ground scrapes from sliding forever.
	var phys := PhysicsMaterial.new()
	phys.friction = 0.9
	phys.rough = true
	phys.bounce = 0.0
	body.physics_material_override = phys

	add_child(body)


## A 2 m grid drawn into a small texture, tiled across the plate. Gives the eye
## something to track for speed without needing an external asset.
func _make_grid_texture() -> ImageTexture:
	var size := 128
	var img := Image.create(size, size, true, Image.FORMAT_RGB8)
	var base := Color(0.55, 0.58, 0.5)
	var line := Color(0.45, 0.48, 0.42)
	for y in size:
		for x in size:
			var edge := x < 2 or y < 2
			img.set_pixel(x, y, line if edge else base)
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


## Kerbs and a ramp so the suspension can be seen working.
func _build_props() -> void:
	var props := Node3D.new()
	props.name = "Props"
	add_child(props)

	# A low ramp to launch off.
	_add_box(props, Vector3(0.0, 0.0, -34.0), Vector3(7.0, 1.6, 9.0),
		Color(0.55, 0.5, 0.45), deg_to_rad(-10.0))
	# A pair of speed bumps.
	for i in 3:
		_add_box(props, Vector3(-16.0, 0.06, 8.0 + i * 7.0), Vector3(9.0, 0.14, 0.55),
			Color(0.72, 0.66, 0.3), 0.0)
	# Kerb blocks to bump into.
	for i in 6:
		var a := TAU * i / 6.0
		_add_box(props, Vector3(cos(a) * 26.0, 0.4, sin(a) * 26.0 + 20.0),
			Vector3(2.4, 0.8, 2.4), Color(0.5, 0.52, 0.55), a)


func _add_box(parent: Node3D, pos: Vector3, size: Vector3, colour: Color, yaw: float) -> void:
	var body := StaticBody3D.new()
	body.position = pos
	body.rotation.y = yaw if size.y > 0.5 else 0.0
	if size.y > 0.5 and absf(yaw) < 1.0:
		body.rotation = Vector3(yaw, 0.0, 0.0)
	body.collision_layer = 1

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
	mat.roughness = 0.85
	mesh.material_override = mat
	body.add_child(mesh)

	parent.add_child(body)
