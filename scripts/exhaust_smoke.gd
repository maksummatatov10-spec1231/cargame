class_name ExhaustSmoke
extends Node3D

## Exhaust smoke for the two tailpipes, plus tyre smoke when a wheel spins up.
##
## Everything is built in code so the scene stays readable and the particle
## material is shared between both pipes.
##
## The behaviour is driven by what the engine and tyres are actually doing:
##   * a thin idle haze that is always there
##   * a puff when the throttle is opened (the turbo dumping fuel in)
##   * more volume the harder the engine is working
##   * white tyre smoke when a driven wheel is slipping badly
##
## Real exhaust drifts and expands rather than shooting out, so the particles
## are given a low initial velocity, a little turbulence and a lot of damping.

## Where the tailpipes are, in car space.
##
## Measured out of the model rather than guessed: the twin chrome tips on the
## Chassis_METAL surface sit at x = +/-0.374, y = 0.274, z = 2.201. The emitter
## is nudged 4 cm further back so the smoke starts just outside the pipe.
const PIPE_POSITIONS := [
	Vector3(-0.374, 0.274, 2.24),
	Vector3(0.374, 0.274, 2.24),
]

## Base colour of the smoke. Kept a light grey so it reads against both the
## asphalt and the sky without looking like a fire.
@export var smoke_colour := Color(0.62, 0.63, 0.64, 1.0)
## Particles per second at idle, and at full load.
@export var idle_rate := 7.0
@export var load_rate := 26.0
## Extra particles for the moment the throttle is opened.
@export var burst_rate := 55.0
## How long a throttle stab keeps producing extra smoke.
@export var burst_time := 0.35
## Particles per second from one badly slipping tyre.
@export var tyre_smoke_rate := 90.0
## Slip ratio at which the tyres start to smoke.
@export var tyre_smoke_threshold := 0.35

var _pipes : Array[GPUParticles3D] = []
var _tyre_emitters : Array[GPUParticles3D] = []
var _vehicle : Vehicle
var _burst_left := 0.0
var _prev_throttle := 0.0


func _ready() -> void:
	_vehicle = get_parent() as Vehicle
	if _vehicle == null:
		push_warning("ExhaustSmoke expects to be a child of a Vehicle")
		return

	var mesh := _make_particle_mesh()
	for pos in PIPE_POSITIONS:
		_pipes.append(_make_pipe(pos, mesh))

	# One tyre smoke emitter per driven wheel, parented to the wheel so it
	# follows the suspension and the car.
	for wheel in _vehicle.get_wheels():
		if wheel.is_driven:
			_tyre_emitters.append(_make_tyre_emitter(wheel, mesh))


## A small quad is all that is needed; the material does the work.
func _make_particle_mesh() -> QuadMesh:
	var mesh := QuadMesh.new()
	mesh.size = Vector2(0.34, 0.34)
	return mesh


func _make_pipe(pos: Vector3, mesh: Mesh) -> GPUParticles3D:
	var particles := GPUParticles3D.new()
	particles.name = "Pipe"
	particles.position = pos
	particles.amount = 48
	particles.lifetime = 1.5
	particles.explosiveness = 0.0
	particles.randomness = 0.55
	particles.fixed_fps = 30
	particles.local_coords = false        # smoke is left behind, not carried
	particles.draw_order = GPUParticles3D.DRAW_ORDER_VIEW_DEPTH
	particles.draw_pass_1 = mesh
	particles.process_material = _make_process_material()
	particles.material_override = _make_draw_material()
	particles.emitting = true
	add_child(particles)
	return particles


func _make_tyre_emitter(wheel: RayWheel, mesh: Mesh) -> GPUParticles3D:
	var particles := GPUParticles3D.new()
	particles.name = "TyreSmoke"
	particles.amount = 40
	particles.lifetime = 1.1
	particles.randomness = 0.7
	particles.fixed_fps = 30
	particles.local_coords = false
	particles.draw_order = GPUParticles3D.DRAW_ORDER_VIEW_DEPTH
	particles.draw_pass_1 = mesh

	var mat := _make_process_material()
	mat.direction = Vector3(0.0, 1.0, 0.0)
	mat.spread = 55.0
	mat.initial_velocity_min = 0.6
	mat.initial_velocity_max = 2.2
	mat.scale_min = 0.5
	mat.scale_max = 1.1
	mat.color = Color(0.86, 0.86, 0.87, 0.85)
	particles.process_material = mat
	particles.material_override = _make_draw_material()
	particles.emitting = false

	# Sits at the bottom of the tyre, where the rubber meets the road.
	wheel.add_child(particles)
	particles.position = Vector3(0.0, -wheel.spring_length - wheel.tyre_radius, 0.0)
	return particles


func _make_process_material() -> ParticleProcessMaterial:
	var mat := ParticleProcessMaterial.new()
	mat.direction = Vector3(0.0, 0.12, 1.0)
	mat.spread = 14.0
	mat.initial_velocity_min = 0.7
	mat.initial_velocity_max = 1.8
	# Slight upward drift: hot exhaust is buoyant.
	mat.gravity = Vector3(0.0, 0.55, 0.0)
	mat.damping_min = 1.4
	mat.damping_max = 2.6
	mat.scale_min = 0.35
	mat.scale_max = 0.7
	mat.angle_min = -180.0
	mat.angle_max = 180.0
	mat.angular_velocity_min = -35.0
	mat.angular_velocity_max = 35.0
	mat.turbulence_enabled = true
	mat.turbulence_noise_strength = 0.28
	mat.turbulence_noise_scale = 1.4
	mat.color = smoke_colour

	# Puff out as it cools and disperses.
	var curve := Curve.new()
	curve.add_point(Vector2(0.0, 0.35))
	curve.add_point(Vector2(0.35, 1.0))
	curve.add_point(Vector2(1.0, 1.9))
	var scale_curve := CurveTexture.new()
	scale_curve.curve = curve
	mat.scale_curve = scale_curve

	# Fade in quickly, then away to nothing.
	var gradient := Gradient.new()
	gradient.set_color(0, Color(1, 1, 1, 0.0))
	gradient.set_color(1, Color(1, 1, 1, 0.0))
	gradient.add_point(0.12, Color(1, 1, 1, 0.55))
	gradient.add_point(0.45, Color(1, 1, 1, 0.32))
	var ramp := GradientTexture1D.new()
	ramp.gradient = gradient
	mat.color_ramp = ramp
	return mat


## Soft, unshaded, additive-free billboard. Smoke should not receive harsh
## lighting or it flickers as the car moves.
func _make_draw_material() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.blend_mode = BaseMaterial3D.BLEND_MODE_MIX
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	mat.billboard_keep_scale = true
	mat.vertex_color_use_as_albedo = true
	mat.albedo_color = Color(1, 1, 1, 1)
	mat.albedo_texture = _make_puff_texture()
	mat.disable_receive_shadows = false
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.no_depth_test = false
	# Soft particles: fades where the quad intersects geometry, so the smoke
	# does not cut a hard line across the road.
	mat.proximity_fade_enabled = true
	mat.proximity_fade_distance = 0.6
	mat.distance_fade_mode = BaseMaterial3D.DISTANCE_FADE_PIXEL_ALPHA
	mat.distance_fade_min_distance = 140.0
	mat.distance_fade_max_distance = 170.0
	return mat


## A soft round blob with a little internal structure, so the smoke does not
## look like a bag of identical circles.
func _make_puff_texture() -> ImageTexture:
	var size := 64
	var img := Image.create(size, size, true, Image.FORMAT_RGBA8)
	var centre := (size - 1) * 0.5
	for y in size:
		for x in size:
			var dx := (x - centre) / centre
			var dy := (y - centre) / centre
			var d := sqrt(dx * dx + dy * dy)
			# Smooth falloff to zero at the edge of the quad.
			var a := clampf(1.0 - d, 0.0, 1.0)
			a = a * a * (3.0 - 2.0 * a)
			# Break up the disc so it reads as a puff.
			var n := 0.82 + 0.18 * sin(x * 0.9) * cos(y * 0.7)
			img.set_pixel(x, y, Color(1.0, 1.0, 1.0, a * n))
	img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


func _process(delta: float) -> void:
	if _vehicle == null:
		return

	var throttle : float = _vehicle.throttle
	# A stab of throttle produces a visible puff.
	if throttle > _prev_throttle + 0.25:
		_burst_left = burst_time
	_prev_throttle = throttle
	_burst_left = maxf(0.0, _burst_left - delta)

	# Named engine_load, not load: "load" is a built-in function.
	var engine_load := clampf(throttle, 0.0, 1.0)
	var revs := clampf(_vehicle.engine_rpm / maxf(_vehicle.redline_rpm, 1.0), 0.0, 1.0)
	var rate := idle_rate + load_rate * engine_load * (0.45 + 0.55 * revs)
	if _burst_left > 0.0:
		rate += burst_rate * (_burst_left / maxf(burst_time, 0.001))
	# The turbo makes it noticeably dirtier.
	rate *= 1.0 + 0.6 * _vehicle.boost

	for pipe in _pipes:
		pipe.amount_ratio = clampf(rate / 80.0, 0.05, 1.0)
		pipe.emitting = true

	# Tyre smoke: only when a driven wheel is genuinely slipping on the ground.
	var i := 0
	for wheel in _vehicle.get_wheels():
		if not wheel.is_driven:
			continue
		if i >= _tyre_emitters.size():
			break
		var emitter := _tyre_emitters[i]
		var slip := absf(wheel.slip_ratio)
		var smoking := wheel.grounded and slip > tyre_smoke_threshold
		emitter.emitting = smoking
		if smoking:
			emitter.amount_ratio = clampf(
				(slip - tyre_smoke_threshold) / 0.9, 0.15, 1.0)
		i += 1
