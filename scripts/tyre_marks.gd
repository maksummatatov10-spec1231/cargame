class_name TyreMarks
extends Node3D

## Tyre tracks laid into an [ImmediateMesh], plus dirt thrown up by the wheels.
##
## Why a mesh and not decals: Godot's decals are projected volumes, and a car
## leaves hundreds of overlapping marks in a minute. Stacking that many decals
## collapses the frame rate, which is the standard advice in the engine's own
## community. Building a triangle strip under each wheel costs two vertices per
## wheel per segment and draws in a single pass.
##
## Each wheel keeps its own ribbon. A new pair of vertices is appended whenever
## the wheel has travelled far enough and is actually marking the ground -
## either sliding, spinning, braking hard, or simply driving over something
## soft like dirt. The ribbon is a ring buffer, so old marks disappear rather
## than growing without limit.

## Metres between segments. Smaller looks smoother and costs more.
@export var segment_length := 0.32
## How many segments each wheel remembers.
@export var max_segments := 220
## Slip at which a mark starts to appear on tarmac.
@export var slip_threshold := 0.22
## Height above the ground the ribbon sits, to avoid z-fighting.
@export var surface_offset := 0.035
## Seconds a mark takes to fade out.
@export var fade_time := 14.0

var _vehicle: Vehicle
var _mesh: ImmediateMesh
var _instance: MeshInstance3D
## Per wheel: array of {left, right, strength, age, surface}
var _ribbons: Array = []
var _last_point: Array = []


func _ready() -> void:
	_vehicle = get_parent() as Vehicle
	if _vehicle == null:
		push_warning("TyreMarks expects to be a child of a Vehicle")
		return

	_mesh = ImmediateMesh.new()
	_instance = MeshInstance3D.new()
	_instance.name = "MarkMesh"
	_instance.mesh = _mesh
	_instance.material_override = _make_material()
	_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	# The ribbon is built in world space, so the node must not inherit the
	# car's transform or the marks would drive along with it.
	_instance.top_level = true
	_instance.extra_cull_margin = 200.0
	add_child(_instance)

	# The vehicle readies after its children, so ask it to collect the wheels
	# before sizing the per-wheel arrays.
	_vehicle.ensure_wheels()
	_resize_to_wheels()


func _make_material() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.vertex_color_use_as_albedo = true
	mat.albedo_color = Color(1, 1, 1, 1)
	mat.roughness = 0.95
	mat.metallic = 0.0
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	# Sits just above the terrain; the offset plus this keeps it from flickering.
	mat.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_OPAQUE_ONLY
	mat.no_depth_test = false
	return mat


func _physics_process(delta: float) -> void:
	if _vehicle == null:
		return

	var wheels := _vehicle.get_wheels()
	if wheels.size() != _ribbons.size():
		_resize_to_wheels()
	for i in wheels.size():
		_update_ribbon(i, wheels[i], delta)

	_rebuild()


## Keeps the per-wheel state arrays the same length as the wheel list.
func _resize_to_wheels() -> void:
	var count := _vehicle.get_wheels().size()
	_ribbons.clear()
	_last_point.clear()
	for _i in count:
		_ribbons.append([])
		_last_point.append(Vector3.INF)


func _update_ribbon(index: int, wheel: RayWheel, delta: float) -> void:
	if index >= _ribbons.size():
		return
	var ribbon: Array = _ribbons[index]

	# Age everything and drop what has faded.
	for seg in ribbon:
		seg["age"] += delta
	while not ribbon.is_empty() and ribbon[0]["age"] > fade_time:
		ribbon.pop_front()

	if not wheel.grounded:
		_last_point[index] = Vector3.INF
		return

	# How strongly this wheel is marking the ground.
	var slide := absf(wheel.slip_angle) / deg_to_rad(maxf(wheel.peak_slip_angle_deg, 1.0))
	var spin := absf(wheel.slip_ratio) / maxf(wheel.peak_slip_ratio, 0.01)
	var slip := maxf(slide, spin)
	# Soft ground takes an imprint even when the tyre is not slipping at all.
	var rolling := wheel.surface_looseness * 0.55
	var strength := clampf(maxf((slip - slip_threshold) / 1.6, 0.0) + rolling, 0.0, 1.0)
	if strength <= 0.02:
		_last_point[index] = Vector3.INF
		return

	var centre := wheel.contact_point + wheel.surface_normal * surface_offset
	var previous: Vector3 = _last_point[index]
	if previous != Vector3.INF and centre.distance_to(previous) < segment_length:
		return
	_last_point[index] = centre

	# Lay the strip across the width of the tyre, square to its direction of
	# travel rather than to the car, so it curves properly in a slide.
	var right := wheel.global_basis.x
	var across := right - wheel.surface_normal * right.dot(wheel.surface_normal)
	if across.length_squared() < 1e-6:
		return
	across = across.normalized() * (wheel.tyre_width * 0.5)

	ribbon.append({
		"left": centre - across,
		"right": centre + across,
		"strength": strength,
		"age": 0.0,
		"surface": wheel.surface_type,
	})
	while ribbon.size() > max_segments:
		ribbon.pop_front()


## Rebuilds the whole mesh. ImmediateMesh is cleared and refilled each frame,
## which is what it is designed for.
func _rebuild() -> void:
	_mesh.clear_surfaces()
	var any := false
	for ribbon in _ribbons:
		if ribbon.size() >= 2:
			any = true
			break
	if not any:
		return

	_mesh.surface_begin(Mesh.PRIMITIVE_TRIANGLES)
	for ribbon in _ribbons:
		for i in range(1, ribbon.size()):
			var a: Dictionary = ribbon[i - 1]
			var b: Dictionary = ribbon[i]
			var ca := _colour(a)
			var cb := _colour(b)
			if ca.a <= 0.004 and cb.a <= 0.004:
				continue

			# Two triangles per segment, wound so both faces are visible.
			_mesh.surface_set_color(ca)
			_mesh.surface_add_vertex(a["left"])
			_mesh.surface_set_color(ca)
			_mesh.surface_add_vertex(a["right"])
			_mesh.surface_set_color(cb)
			_mesh.surface_add_vertex(b["left"])

			_mesh.surface_set_color(cb)
			_mesh.surface_add_vertex(b["left"])
			_mesh.surface_set_color(ca)
			_mesh.surface_add_vertex(a["right"])
			_mesh.surface_set_color(cb)
			_mesh.surface_add_vertex(b["right"])
	_mesh.surface_end()


## Marks are dark rubber on rock, and lighter scuffed earth on soft ground.
func _colour(seg: Dictionary) -> Color:
	var fade := 1.0 - clampf(seg["age"] / maxf(fade_time, 0.01), 0.0, 1.0)
	fade = fade * fade
	var alpha := clampf(seg["strength"], 0.0, 1.0) * fade * 0.72
	match int(seg["surface"]):
		Terrain.Surface.DIRT:
			return Color(0.24, 0.18, 0.12, alpha)
		Terrain.Surface.GRASS:
			return Color(0.18, 0.17, 0.11, alpha * 0.85)
		_:
			return Color(0.05, 0.05, 0.055, alpha)
