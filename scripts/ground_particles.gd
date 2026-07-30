class_name GroundParticles
extends Node3D

## Dirt, grass and dust thrown up by the wheels.
##
## One emitter per wheel, sitting at the contact patch. What comes out depends
## on the surface the tyre is on and on what the tyre is doing:
##
##   spinning on dirt   a spray of clods thrown backwards, plus dust
##   sliding on grass   torn grass and a lighter haze
##   driving on rock    nothing, tarmac does not come apart
##
## The clods are given real velocity opposite the wheel's motion and normal
## gravity, so they arc and land instead of floating - that is what makes it
## read as thrown material rather than smoke.

## Slip at which material starts being thrown.
@export var slip_threshold := 0.28
## Particles per second from one wheel at full slip.
@export var max_rate := 130.0
## How fast the clods leave the tyre, relative to the slip speed.
@export var throw_speed := 0.55
## Colour of the material for each surface, matching Terrain.Surface.
@export var surface_colours: Array[Color] = [
	Color(0.30, 0.34, 0.16),   # grass: torn green
	Color(0.36, 0.27, 0.17),   # dirt: brown
	Color(0.42, 0.41, 0.39),   # rock: pale grey (barely used)
]

var _vehicle: Vehicle
var _emitters: Array[GPUParticles3D] = []
var _dust: Array[GPUParticles3D] = []


func _ready() -> void:
	_vehicle = get_parent() as Vehicle
	if _vehicle == null:
		push_warning("GroundParticles expects to be a child of a Vehicle")
		return

	_vehicle.ensure_wheels()
	for wheel in _vehicle.get_wheels():
		_emitters.append(_make_clods(wheel))
		_dust.append(_make_dust(wheel))


## Chunks of earth: small, fast, affected by gravity so they arc away.
func _make_clods(wheel: RayWheel) -> GPUParticles3D:
	var p := GPUParticles3D.new()
	p.name = "Clods"
	p.amount = 64
	p.lifetime = 1.2
	p.randomness = 0.8
	p.fixed_fps = 30
	p.local_coords = false
	p.emitting = false
	p.draw_pass_1 = _clod_mesh()

	var mat := ParticleProcessMaterial.new()
	mat.direction = Vector3(0.0, 0.45, 1.0)
	mat.spread = 32.0
	mat.initial_velocity_min = 2.5
	mat.initial_velocity_max = 8.0
	mat.gravity = Vector3(0.0, -9.81, 0.0)
	mat.damping_min = 0.2
	mat.damping_max = 1.0
	mat.scale_min = 0.35
	mat.scale_max = 1.15
	mat.angle_min = -180.0
	mat.angle_max = 180.0
	mat.angular_velocity_min = -420.0
	mat.angular_velocity_max = 420.0
	mat.color = Color(0.36, 0.27, 0.17)
	p.process_material = mat

	var draw := StandardMaterial3D.new()
	draw.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	draw.vertex_color_use_as_albedo = true
	draw.albedo_color = Color(1, 1, 1, 1)
	draw.roughness = 1.0
	draw.cull_mode = BaseMaterial3D.CULL_DISABLED
	p.material_override = draw

	wheel.add_child(p)
	p.position = Vector3(0.0, -wheel.spring_length - wheel.tyre_radius, 0.0)
	return p


## The haze that hangs behind the car once the ground has been disturbed.
func _make_dust(wheel: RayWheel) -> GPUParticles3D:
	var p := GPUParticles3D.new()
	p.name = "Dust"
	p.amount = 40
	p.lifetime = 1.9
	p.randomness = 0.75
	p.fixed_fps = 30
	p.local_coords = false
	p.emitting = false
	p.draw_pass_1 = _dust_mesh()

	var mat := ParticleProcessMaterial.new()
	mat.direction = Vector3(0.0, 1.0, 0.35)
	mat.spread = 60.0
	mat.initial_velocity_min = 0.5
	mat.initial_velocity_max = 2.4
	mat.gravity = Vector3(0.0, 0.35, 0.0)
	mat.damping_min = 1.2
	mat.damping_max = 2.4
	mat.scale_min = 0.6
	mat.scale_max = 1.6
	mat.angle_min = -180.0
	mat.angle_max = 180.0
	mat.angular_velocity_min = -40.0
	mat.angular_velocity_max = 40.0
	mat.turbulence_enabled = true
	mat.turbulence_noise_strength = 0.35
	mat.turbulence_noise_scale = 1.6
	mat.color = Color(0.55, 0.48, 0.38, 0.5)

	var curve := Curve.new()
	curve.add_point(Vector2(0.0, 0.4))
	curve.add_point(Vector2(1.0, 2.2))
	var ct := CurveTexture.new()
	ct.curve = curve
	mat.scale_curve = ct

	var grad := Gradient.new()
	grad.set_color(0, Color(1, 1, 1, 0.0))
	grad.set_color(1, Color(1, 1, 1, 0.0))
	grad.add_point(0.15, Color(1, 1, 1, 0.42))
	grad.add_point(0.5, Color(1, 1, 1, 0.22))
	var ramp := GradientTexture1D.new()
	ramp.gradient = grad
	mat.color_ramp = ramp
	p.process_material = mat

	var draw := StandardMaterial3D.new()
	draw.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	draw.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	draw.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	draw.billboard_keep_scale = true
	draw.vertex_color_use_as_albedo = true
	draw.albedo_texture = _puff_texture()
	draw.cull_mode = BaseMaterial3D.CULL_DISABLED
	draw.proximity_fade_enabled = true
	draw.proximity_fade_distance = 0.5
	p.material_override = draw

	wheel.add_child(p)
	p.position = Vector3(0.0, -wheel.spring_length - wheel.tyre_radius, 0.0)
	return p


## Irregular little tetrahedra read as clods far better than quads do.
func _clod_mesh() -> ArrayMesh:
	var verts := PackedVector3Array([
		Vector3(0.0, 0.05, 0.0),
		Vector3(-0.04, -0.02, 0.03),
		Vector3(0.045, -0.02, 0.025),
		Vector3(0.0, -0.02, -0.05),
	])
	var idx := PackedInt32Array([0, 1, 2, 0, 2, 3, 0, 3, 1, 1, 3, 2])
	var normals := PackedVector3Array()
	for v in verts:
		normals.append(v.normalized())

	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = verts
	arrays[Mesh.ARRAY_NORMAL] = normals
	arrays[Mesh.ARRAY_INDEX] = idx
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return mesh


func _dust_mesh() -> QuadMesh:
	var m := QuadMesh.new()
	m.size = Vector2(0.55, 0.55)
	return m


func _puff_texture() -> ImageTexture:
	var size := 48
	var img := Image.create(size, size, true, Image.FORMAT_RGBA8)
	var c := (size - 1) * 0.5
	for y in size:
		for x in size:
			var dx := (x - c) / c
			var dy := (y - c) / c
			var d := sqrt(dx * dx + dy * dy)
			var a := clampf(1.0 - d, 0.0, 1.0)
			a = a * a * (3.0 - 2.0 * a)
			img.set_pixel(x, y, Color(1, 1, 1, a))
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


func _physics_process(_delta: float) -> void:
	if _vehicle == null:
		return
	var wheels := _vehicle.get_wheels()
	var count := mini(wheels.size(), mini(_emitters.size(), _dust.size()))
	for i in count:
		_update_wheel(wheels[i], _emitters[i], _dust[i])


func _update_wheel(wheel: RayWheel, clods: GPUParticles3D, dust: GPUParticles3D) -> void:
	if not wheel.grounded or wheel.surface_looseness <= 0.01:
		clods.emitting = false
		dust.emitting = false
		return

	var spin := absf(wheel.slip_ratio) / maxf(wheel.peak_slip_ratio, 0.01)
	var slide := absf(wheel.slip_angle) / deg_to_rad(maxf(wheel.peak_slip_angle_deg, 1.0))
	var slip := maxf(spin, slide)
	var active := slip > slip_threshold

	clods.emitting = active
	dust.emitting = active
	if not active:
		return

	var intensity := clampf((slip - slip_threshold) / 1.4, 0.0, 1.0) * wheel.surface_looseness
	clods.amount_ratio = clampf(intensity, 0.08, 1.0)
	dust.amount_ratio = clampf(intensity * 0.85, 0.05, 1.0)

	var colour := _surface_colour(wheel.surface_type)
	var clod_mat := clods.process_material as ParticleProcessMaterial
	clod_mat.color = colour
	# Material leaves the tyre roughly opposite the way the contact patch is
	# sliding, and faster the harder it is spinning.
	var speed := clampf(slip * throw_speed * 6.0, 2.0, 14.0)
	clod_mat.initial_velocity_min = speed * 0.4
	clod_mat.initial_velocity_max = speed

	var dust_mat := dust.process_material as ParticleProcessMaterial
	dust_mat.color = Color(colour.r * 1.5 + 0.2, colour.g * 1.4 + 0.2,
		colour.b * 1.4 + 0.2, 0.45)


func _surface_colour(surface: int) -> Color:
	if surface >= 0 and surface < surface_colours.size():
		return surface_colours[surface]
	return surface_colours[1]
