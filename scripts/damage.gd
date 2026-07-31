class_name VehicleDamage
extends Node

## Collision damage: dents in the bodywork, and handling that degrades.
##
## HOW THE DENTS WORK
##
## They are a vertex shader, not a rebuilt mesh. Measured: the BMW body is
## 88,326 vertices, and touching every one of them from GDScript costs
## roughly 26 ms - one and a half frames - for a single impact. Doing it
## continuously is out of the question. The plants already deform in a vertex
## shader for the same reason (forest.gd), so this follows that pattern.
##
## Each impact becomes a dent: a world-space point, a radius and a depth.
## The shader pulls vertices near that point inwards along the impact
## direction, falling off smoothly to nothing at the radius. Dents accumulate
## in a fixed-size array, so the cost is constant however long you drive.
##
## HOW THE HANDLING DAMAGE WORKS
##
## Nothing is scripted as "now understeer". Damage scales the physical
## quantities the simulation already uses, so the consequences fall out of
## the tyre model:
##
##   * a bent corner loses camber and toe, so that tyre's contact patch is
##     no longer square to the road and it generates less grip;
##   * a damaged engine loses peak torque;
##   * a bent steering rack adds a pull to one side.
##
## That means a damaged car behaves differently for the same reason a real
## one does, rather than because a number was faked.

## Fired when the car takes a hit worth reacting to. `severity` is 0..1.
signal damaged(severity: float)

## Most dents tracked at once. Each is a vec4 (position) plus a vec4
## (direction and depth), so 12 dents is 24 vec4s of uniform - trivial.
const MAX_DENTS := 12

const DENT_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;

uniform vec4 albedo_colour = vec4(0.6, 0.6, 0.6, 1.0);
uniform float metallic_value = 0.3;
uniform float roughness_value = 0.5;
uniform bool has_texture = false;
uniform sampler2D albedo_texture : source_color, filter_linear_mipmap;

// xyz is the impact point in the model's own space, w is the radius.
uniform vec4 dent_points[12];
// xyz is the direction the panel was pushed, w is the depth in metres.
uniform vec4 dent_normals[12];

varying float dent_amount;

void vertex() {
	float worst = 0.0;
	vec3 push = vec3(0.0);

	for (int i = 0; i < 12; i++) {
		float radius = dent_points[i].w;
		if (radius <= 0.001) {
			continue;
		}
		float d = distance(VERTEX, dent_points[i].xyz);
		if (d > radius) {
			continue;
		}
		// Smooth falloff, deepest at the centre of the impact. Squaring the
		// smoothstep gives a crater profile rather than a cone, which reads
		// much more like sheet metal.
		float t = 1.0 - smoothstep(0.0, radius, d);
		float amount = t * t;
		push += dent_normals[i].xyz * dent_normals[i].w * amount;
		worst = max(worst, amount);
	}

	VERTEX += push;
	dent_amount = worst;

	// A dented panel is no longer flat, so perturb the normal towards the
	// impact direction. Without this the dent is invisible on a smooth body
	// colour - the shape changes but the shading does not.
	if (worst > 0.001) {
		NORMAL = normalize(NORMAL + push * 4.0);
	}
}

void fragment() {
	vec4 base = albedo_colour;
	if (has_texture) {
		base *= texture(albedo_texture, UV);
	}
	// Damaged metal loses its polish: bare, scuffed and duller.
	ALBEDO = mix(base.rgb, base.rgb * 0.55 + vec3(0.05), dent_amount * 0.8);
	ALPHA = base.a;
	METALLIC = mix(metallic_value, metallic_value * 0.4, dent_amount);
	ROUGHNESS = mix(roughness_value, min(roughness_value + 0.45, 1.0),
		dent_amount);
}
"""

## Impacts below this impulse are ignored - kerbs, gentle nudges and the
## constant small contacts of driving over rough ground.
@export var impact_threshold := 900.0
## Impulse at which a single hit does maximum damage.
@export var impact_full := 12000.0
## Radius of the dent a maximum-severity hit leaves, in metres.
@export var dent_radius := 0.85
## How deep a maximum-severity dent is pulled in, in metres.
@export var dent_depth := 0.16
## Total damage the car can take before it is at its worst, in impulse-seconds.
@export var wreck_threshold := 45000.0
## How much grip a fully damaged corner loses, as a fraction.
@export_range(0.0, 0.9) var grip_loss := 0.35
## How much peak torque a fully damaged engine loses, as a fraction.
@export_range(0.0, 0.9) var power_loss := 0.4
## Worst steering pull a bent rack can add, in radians.
@export var steer_pull := 0.05

## 0 = pristine, 1 = as bad as it gets.
var total_damage := 0.0
## Per-corner damage, indexed the same way as the vehicle's wheels.
var corner_damage: Array[float] = [0.0, 0.0, 0.0, 0.0]

var _vehicle: Vehicle
var _materials: Array[ShaderMaterial] = []
var _dent_points := PackedVector4Array()
var _dent_normals := PackedVector4Array()
var _dent_count := 0
var _next_dent := 0
var _base_torque := 0.0
var _base_camber: Array[float] = []
var _base_toe: Array[float] = []
var _base_friction: Array[float] = []
var _cooldown := 0.0


func _ready() -> void:
	_dent_points.resize(MAX_DENTS)
	_dent_normals.resize(MAX_DENTS)
	for i in MAX_DENTS:
		_dent_points[i] = Vector4.ZERO
		_dent_normals[i] = Vector4.ZERO

	_vehicle = get_parent() as Vehicle
	if _vehicle == null:
		push_warning("VehicleDamage: parent is not a Vehicle")
		set_physics_process(false)
		return

	# Capture the undamaged setup, so damage always scales from the design
	# figures rather than compounding on whatever the values are now.
	_vehicle.ensure_wheels()
	_base_torque = _vehicle.peak_torque
	for w in _vehicle.get_wheels():
		_base_camber.append(w.camber_deg)
		_base_toe.append(w.toe_deg)
		_base_friction.append(w.friction_coefficient)

	# Collect the body materials so the dents can be pushed into them.
	_collect_materials.call_deferred()


## Finds the shader materials on the car's visual model.
##
## Deferred because the model instances its glTF in _ready(), and a child's
## _ready() runs before its parent's - the same ordering that caused the
## "Node not found" crash in v2.1.
func _collect_materials() -> void:
	if _vehicle == null or not is_instance_valid(_vehicle):
		return
	var model := _vehicle.get_node_or_null("Smooth/Model")
	if model == null:
		return
	_apply_dent_shader(model)


func _apply_dent_shader(node: Node) -> void:
	var mesh_node := node as MeshInstance3D
	if mesh_node != null and mesh_node.mesh != null:
		for i in mesh_node.mesh.get_surface_count():
			var source := mesh_node.mesh.surface_get_material(i)
			var mat := _make_dent_material(source)
			mesh_node.set_surface_override_material(i, mat)
			_materials.append(mat)
	for child in node.get_children():
		_apply_dent_shader(child)


## Wraps an imported material in a shader that can dent it.
##
## The original colour, metallic and roughness are read off the source
## material and passed through, so the car looks exactly as it did before it
## was hit - the shader only adds the displacement.
func _make_dent_material(source: Material) -> ShaderMaterial:
	var shader := Shader.new()
	shader.code = DENT_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = shader

	var albedo := Color(0.6, 0.6, 0.6)
	var metallic := 0.3
	var roughness := 0.5
	var std := source as StandardMaterial3D
	if std != null:
		albedo = std.albedo_color
		metallic = std.metallic
		roughness = std.roughness
		if std.albedo_texture != null:
			mat.set_shader_parameter("albedo_texture", std.albedo_texture)
			mat.set_shader_parameter("has_texture", true)
		if std.transparency != BaseMaterial3D.TRANSPARENCY_DISABLED:
			mat.render_priority = 1

	mat.set_shader_parameter("albedo_colour",
		Vector4(albedo.r, albedo.g, albedo.b, albedo.a))
	mat.set_shader_parameter("metallic_value", metallic)
	mat.set_shader_parameter("roughness_value", roughness)
	mat.set_shader_parameter("dent_points", _dent_points)
	mat.set_shader_parameter("dent_normals", _dent_normals)
	return mat




func _physics_process(delta: float) -> void:
	if _vehicle == null or not is_instance_valid(_vehicle):
		return
	_cooldown = maxf(0.0, _cooldown - delta)


## Called by the vehicle from _integrate_forces, where the contact data lives.
func report_contacts(state: PhysicsDirectBodyState3D) -> void:
	if _vehicle == null:
		return
	for i in state.get_contact_count():
		var impulse := state.get_contact_impulse(i).length()
		if impulse < impact_threshold:
			continue
		# One dent per impact, not one per tick of the same impact.
		if _cooldown > 0.0:
			continue
		_cooldown = 0.12

		var severity := clampf(
			(impulse - impact_threshold) / maxf(impact_full - impact_threshold, 1.0),
			0.0, 1.0)
		var point := state.get_contact_local_position(i)
		var normal := state.get_contact_local_normal(i)
		_add_dent(point, normal, severity)
		_apply_damage(point, severity, impulse)
		damaged.emit(severity)


## Records a dent at a world position.
func _add_dent(world_point: Vector3, world_normal: Vector3,
		severity: float) -> void:
	if _vehicle == null:
		return
	# The shader works in model space, so the impact has to be brought there.
	var local := _vehicle.global_transform.affine_inverse() * world_point
	var dir := _vehicle.global_transform.basis.inverse() * world_normal
	if dir.length_squared() < 1e-6:
		dir = Vector3.UP
	dir = dir.normalized()

	var radius := dent_radius * (0.45 + 0.55 * severity)
	var depth := dent_depth * severity

	_dent_points[_next_dent] = Vector4(local.x, local.y, local.z, radius)
	# The panel is pushed INTO the car, i.e. along the contact normal.
	_dent_normals[_next_dent] = Vector4(dir.x, dir.y, dir.z, depth)
	_next_dent = (_next_dent + 1) % MAX_DENTS
	_dent_count = mini(_dent_count + 1, MAX_DENTS)
	_push_dents()


func _push_dents() -> void:
	for mat in _materials:
		if is_instance_valid(mat):
			mat.set_shader_parameter("dent_points", _dent_points)
			mat.set_shader_parameter("dent_normals", _dent_normals)


## Turns an impact into mechanical damage.
##
## Which corner suffers is decided by where the car was hit, so a front-left
## impact bends the front-left corner - not an average spread over the car.
func _apply_damage(world_point: Vector3, severity: float,
		impulse: float) -> void:
	total_damage = clampf(total_damage + impulse / maxf(wreck_threshold, 1.0),
		0.0, 1.0)

	var local := _vehicle.global_transform.affine_inverse() * world_point
	var wheels := _vehicle.get_wheels()
	for i in wheels.size():
		if i >= corner_damage.size():
			break
		var corner := wheels[i].position
		# Same side and same end of the car takes most of it.
		var same_side := signf(corner.x) == signf(local.x) or absf(local.x) < 0.3
		var same_end := signf(corner.z) == signf(local.z) or absf(local.z) < 0.5
		var share := 0.15
		if same_side and same_end:
			share = 1.0
		elif same_side or same_end:
			share = 0.4
		corner_damage[i] = clampf(corner_damage[i] + severity * share * 0.35,
			0.0, 1.0)

	_apply_mechanical()


## Pushes the damage into the physical parameters the simulation reads.
func _apply_mechanical() -> void:
	if _vehicle == null:
		return

	# A damaged engine simply makes less torque. Scaled from the design
	# figure, never from the current one.
	_vehicle.peak_torque = _base_torque * (1.0 - power_loss * total_damage)

	var wheels := _vehicle.get_wheels()
	for i in wheels.size():
		if i >= corner_damage.size() or i >= _base_camber.size():
			break
		var d := corner_damage[i]
		var w := wheels[i]
		# A bent corner: the wheel is knocked out of alignment, so its
		# contact patch no longer sits flat. The tyre model turns that into
		# lost grip on its own.
		w.camber_deg = _base_camber[i] - 6.0 * d
		w.toe_deg = _base_toe[i] + 1.6 * d * signf(w.position.x)
		w.friction_coefficient = _base_friction[i] * (1.0 - grip_loss * d)


## Steering pull from a bent rack, in radians. Read by the vehicle.
func steering_offset() -> float:
	if corner_damage.size() < 2:
		return 0.0
	# The difference between the two front corners is what pulls the car.
	return (corner_damage[0] - corner_damage[1]) * steer_pull


## Puts the car back together. Called on respawn.
func repair() -> void:
	total_damage = 0.0
	for i in corner_damage.size():
		corner_damage[i] = 0.0
	for i in MAX_DENTS:
		_dent_points[i] = Vector4.ZERO
		_dent_normals[i] = Vector4.ZERO
	_dent_count = 0
	_next_dent = 0
	_push_dents()
	_apply_mechanical()


## How many dents are currently showing, for the checks and the HUD.
func dent_count() -> int:
	return _dent_count
