extends Node3D

## Builds the stage 1 test ground: a flat 200 x 200 m asphalt plate with a
## proper collision surface, procedurally textured so the sun has something to
## catch, plus a ramp and kerbs that show the suspension working.

## Resolution of the procedurally generated asphalt textures.
const GROUND_TEX_SIZE := 256

@export var ground_size := 200.0
## Base colour of the asphalt. The generated textures modulate this.
@export var ground_colour := Color(0.34, 0.38, 0.31)


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
	# Enough subdivisions that per-vertex lighting and SDFGI have something to
	# work with, without turning the plate into a million triangles.
	plane.subdivide_width = 96
	plane.subdivide_depth = 96
	mesh.mesh = plane
	mesh.material_override = _make_ground_material()
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	mesh.gi_mode = GeometryInstance3D.GI_MODE_STATIC
	body.add_child(mesh)

	var phys := PhysicsMaterial.new()
	phys.friction = 0.9
	phys.rough = true
	phys.bounce = 0.0
	body.physics_material_override = phys

	add_child(body)


## Asphalt: a tiling albedo with real grain, a matching normal map so the sun
## catches the texture, and a roughness map so it is not uniformly matte.
## Without the normal and roughness maps a big flat plane reads as dead grey.
func _make_ground_material() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = ground_colour
	mat.albedo_texture = _make_asphalt_albedo()
	mat.normal_enabled = true
	mat.normal_texture = _make_asphalt_normal()
	mat.normal_scale = 0.65
	mat.roughness = 1.0
	mat.roughness_texture = _make_asphalt_roughness()
	mat.metallic = 0.0
	mat.metallic_specular = 0.35
	mat.uv1_scale = Vector3(ground_size / 6.0, ground_size / 6.0, 1.0)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	mat.texture_repeat = true
	return mat


## Deterministic value noise, so the ground looks the same every run.
func _noise(x: int, y: int, seed_value: int) -> float:
	var n := x * 374761393 + y * 668265263 + seed_value * 1274126177
	n = (n ^ (n >> 13)) * 1274126177
	return float((n ^ (n >> 16)) & 0xFFFF) / 65535.0


func _fbm(x: int, y: int, size: int, octaves: int, seed_value: int) -> float:
	var total := 0.0
	var amplitude := 1.0
	var norm := 0.0
	var step := 1
	for o in octaves:
		var v := _noise((x * step) % size, (y * step) % size, seed_value + o)
		total += v * amplitude
		norm += amplitude
		amplitude *= 0.5
		step *= 2
	return total / maxf(norm, 0.0001)


func _make_asphalt_albedo() -> ImageTexture:
	var size := GROUND_TEX_SIZE
	var img := Image.create(size, size, true, Image.FORMAT_RGB8)
	for y in size:
		for x in size:
			# Coarse patchiness plus fine aggregate grain.
			var grain := _fbm(x, y, size, 4, 11)
			var patch := _fbm(x / 8, y / 8, size, 2, 29)
			var v := 0.62 + 0.30 * grain + 0.16 * patch
			var c := ground_colour * v
			# A few lighter stones scattered through the mix.
			if grain > 0.86:
				c = c.lerp(Color(0.62, 0.62, 0.6), 0.45)
			img.set_pixel(x, y, c)
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


func _make_asphalt_normal() -> ImageTexture:
	var size := GROUND_TEX_SIZE
	var height := PackedFloat32Array()
	height.resize(size * size)
	for y in size:
		for x in size:
			height[y * size + x] = _fbm(x, y, size, 4, 11)

	var img := Image.create(size, size, true, Image.FORMAT_RGB8)
	for y in size:
		for x in size:
			var l := height[y * size + (x - 1 + size) % size]
			var r := height[y * size + (x + 1) % size]
			var d := height[((y - 1 + size) % size) * size + x]
			var u := height[((y + 1) % size) * size + x]
			# Sobel-style gradient packed into a tangent space normal.
			var nx := (l - r) * 0.5
			var ny := (d - u) * 0.5
			var n := Vector3(nx, ny, 0.09).normalized()
			img.set_pixel(x, y, Color(
				n.x * 0.5 + 0.5,
				n.y * 0.5 + 0.5,
				n.z * 0.5 + 0.5))
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


func _make_asphalt_roughness() -> ImageTexture:
	var size := GROUND_TEX_SIZE
	var img := Image.create(size, size, true, Image.FORMAT_RGB8)
	for y in size:
		for x in size:
			# Slightly polished where the aggregate is worn smooth.
			var v := 0.74 + 0.22 * _fbm(x, y, size, 3, 47)
			img.set_pixel(x, y, Color(v, v, v))
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


## Kerbs and a ramp so the suspension can be seen working.
func _build_props() -> void:
	var props := Node3D.new()
	props.name = "Props"
	add_child(props)

	# A ramp to launch off. It is pitched about X, so one end is buried in the
	# ground and the other rises: that is what makes it drivable rather than a
	# step. Raised by half its height so the leading edge meets the ground.
	_add_box(props, Vector3(0.0, 0.55, -34.0), Vector3(8.0, 1.2, 11.0),
		Color(0.55, 0.5, 0.45), Vector3(deg_to_rad(-11.0), 0.0, 0.0))

	# Speed bumps, to watch the suspension work at low speed.
	for i in 3:
		_add_box(props, Vector3(-16.0, 0.05, 8.0 + i * 7.0), Vector3(9.0, 0.1, 0.6),
			Color(0.72, 0.66, 0.3), Vector3.ZERO)

	# Kerb blocks to bump into, yawed so they are not all axis aligned.
	for i in 6:
		var a := TAU * i / 6.0
		_add_box(props, Vector3(cos(a) * 26.0, 0.4, sin(a) * 26.0 + 20.0),
			Vector3(2.4, 0.8, 2.4), Color(0.5, 0.52, 0.55), Vector3(0.0, a, 0.0))


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
	mat.roughness = 0.85
	mesh.material_override = mat
	body.add_child(mesh)

	parent.add_child(body)
