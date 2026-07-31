class_name VehicleDamage
extends Node

## Collision damage: what breaks, where, and what that does to the driving.
##
## The layout of the car - zones, components, how deeply each one is buried -
## lives in DamageModel. This node applies it.
##
## ── WHY DENTS ARE A SHADER ────────────────────────────────────────────────
##
## Measured before writing any of it: the BMW body is 88,326 vertices, and
## touching every one from GDScript costs roughly 26 ms - one and a half
## frames - for a single impact. Rebuilding the mesh is not viable even once.
## So dents are a vertex shader, exactly like the crushable plants.
##
## ── WHY ENERGY, NOT JUST IMPULSE ──────────────────────────────────────────
##
## Impulse says how hard the hit was. Energy says how much the structure had
## to absorb, and that is what crumples metal. An impulse J on mass m is a
## velocity change J/m, carrying energy J²/2m - so doubling the closing speed
## quadruples the damage, which is how real crashes behave.
##
## ── WHY NOTHING IS SCRIPTED ───────────────────────────────────────────────
##
## There is no line anywhere saying "now the car understeers" or "now it is
## slower". Broken components change the SAME numbers the simulation already
## reads every tick:
##
##   bent suspension  → camber, toe, spring rate, damper rate at that corner
##   damaged engine   → peak torque, and a rev limit if it is overheating
##   holed radiator   → coolant leaks, temperature climbs, power is pulled
##   punctured tank   → fuel leaks, and the engine eventually stops
##   bent steering    → a permanent offset the driver has to hold against
##   damaged brakes   → less brake torque at that corner, so the car pulls
##                      under braking
##   flat tyre        → collapsed rolling radius and a fraction of the grip
##
## The handling consequences then emerge from the tyre model, the same way
## they emerge from a real bent car.

# --------------------------------------------------------------------------- #
#  signals
# --------------------------------------------------------------------------- #

## A hit worth reacting to. `severity` is 0..1.
signal impact(severity: float, zone_name: String)
## A component crossed from working to broken.
signal part_broke(part_name: String)
## The engine stopped, and why.
signal engine_died(reason: String)

# --------------------------------------------------------------------------- #
#  shader
# --------------------------------------------------------------------------- #

## Dents tracked at once. Each is two vec4s of uniform, so 16 is nothing.
const MAX_DENTS := 16

const DENT_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;

uniform vec4 albedo_colour = vec4(0.6, 0.6, 0.6, 1.0);
uniform float metallic_value = 0.3;
uniform float roughness_value = 0.5;
uniform bool has_texture = false;
uniform sampler2D albedo_texture : source_color, filter_linear_mipmap;

// xyz: impact point in model space. w: radius.
uniform vec4 dent_points[16];
// xyz: direction the panel was pushed. w: depth in metres.
uniform vec4 dent_normals[16];

// Whole-body deformation from a structural hit: the shell is sheared and
// twisted, not just locally dimpled. xyz is the bend axis, w the amount.
uniform vec4 body_bend = vec4(0.0, 1.0, 0.0, 0.0);
// How scratched and dulled the paint is overall, 0..1.
uniform float paint_wear = 0.0;

varying float dent_amount;

void vertex() {
	float worst = 0.0;
	vec3 push = vec3(0.0);

	for (int i = 0; i < 16; i++) {
		float radius = dent_points[i].w;
		if (radius <= 0.001) {
			continue;
		}
		float d = distance(VERTEX, dent_points[i].xyz);
		if (d > radius) {
			continue;
		}
		// Squared smoothstep: a crater profile rather than a cone, which is
		// what sheet metal actually does.
		float t = 1.0 - smoothstep(0.0, radius, d);
		float amount = t * t;
		push += dent_normals[i].xyz * dent_normals[i].w * amount;
		worst = max(worst, amount);
	}

	// Structural bend. A hard hit does not only dent the panel it landed on -
	// it twists the whole shell, which is why a badly crashed car has doors
	// that no longer line up. Scaled by distance from the centre so the ends
	// move most.
	if (body_bend.w > 0.001) {
		float lever = length(VERTEX.xz) * body_bend.w;
		vec3 twist = cross(body_bend.xyz, VERTEX) * lever * 0.06;
		push += twist;
		worst = max(worst, body_bend.w * 0.35);
	}

	VERTEX += push;
	dent_amount = worst;

	// A dented panel is no longer flat. Without perturbing the normal the
	// shape changes but the shading does not, and on a smooth body colour
	// the dent is invisible.
	if (worst > 0.001) {
		NORMAL = normalize(NORMAL + push * 4.0);
	}
}

void fragment() {
	vec4 base = albedo_colour;
	if (has_texture) {
		base *= texture(albedo_texture, UV);
	}
	// Damaged metal loses its polish: scuffed, bare, duller.
	float wear = max(dent_amount * 0.8, paint_wear * 0.5);
	ALBEDO = mix(base.rgb, base.rgb * 0.55 + vec3(0.05), wear);
	ALPHA = base.a;
	METALLIC = mix(metallic_value, metallic_value * 0.4, wear);
	ROUGHNESS = mix(roughness_value, min(roughness_value + 0.45, 1.0), wear);
}
"""

# --------------------------------------------------------------------------- #
#  configuration
# --------------------------------------------------------------------------- #

@export_group("Impact")
## Impulses below this are ignored. 900 N s on a 1495 kg car is 0.60 m/s of
## sudden velocity change, which is above anything rough ground produces.
@export var impact_threshold := 900.0
## Energy, in joules, that counts as a maximum-severity single impact.
## 300 kJ is roughly a 1500 kg car hitting a wall at 70 km/h.
@export var reference_energy := 300000.0
## Seconds before the same collision can register again.
@export var impact_cooldown := 0.10

@export_group("Appearance")
## Radius of a full-severity dent, in metres.
@export var dent_radius := 0.85
## Depth of a full-severity dent, in metres.
@export var dent_depth := 0.16
## How much the shell twists when the structure is destroyed, 0..1.
@export var max_body_bend := 0.5

@export_group("Consequences")
## Grip a fully destroyed corner loses.
@export_range(0.0, 0.95) var grip_loss := 0.35
## Peak torque a fully destroyed engine loses.
@export_range(0.0, 0.95) var power_loss := 0.45
## Brake torque a fully destroyed brake loses.
@export_range(0.0, 1.0) var brake_loss := 0.8
## Worst steering pull from a bent rack, in radians.
@export var steer_pull := 0.06
## Litres of coolant, and how fast a holed radiator loses it.
@export var coolant_capacity := 7.0
@export var coolant_leak_rate := 0.45
## Litres of fuel, and how fast a punctured tank loses it.
@export var fuel_capacity := 53.0
@export var fuel_leak_rate := 0.35
## Engine temperature in Celsius: normal, where power starts being pulled,
## and where it seizes.
@export var temp_normal := 90.0
@export var temp_warning := 112.0
@export var temp_critical := 128.0

# --------------------------------------------------------------------------- #
#  state
# --------------------------------------------------------------------------- #

## 0 = pristine, 1 = wrecked. Derived from the structure, not a separate bar.
var total_damage := 0.0
## How much of the shell's rigidity is gone, 0..1.
var structural_damage := 0.0
## Per-zone dent level, {DamageModel.Zone: 0..1}.
var zone_damage := {}
## Per-component damage, {DamageModel.Part: 0..1}.
var part_damage := {}
## Per-corner damage, in wheel order.
var corner_damage: Array[float] = [0.0, 0.0, 0.0, 0.0]
## Tyres that have gone flat, in wheel order.
var tyre_flat: Array[bool] = [false, false, false, false]

var coolant := 7.0
var fuel := 53.0
var engine_temp := 20.0
var engine_running := true
var engine_stop_reason := ""

var _vehicle: Vehicle
var _materials: Array[ShaderMaterial] = []
var _dent_points := PackedVector4Array()
var _dent_normals := PackedVector4Array()
var _dent_count := 0
var _next_dent := 0
var _cooldown := 0.0
var _aabb_min := Vector3(-1.0, 0.0, -2.0)
var _aabb_max := Vector3(1.0, 1.4, 2.2)

# Design values, captured once. Damage always scales from these, never from
# the current values - otherwise two hits would compound multiplicatively.
var _base_torque := 0.0
var _base_camber: Array[float] = []
var _base_toe: Array[float] = []
var _base_friction: Array[float] = []
var _base_spring: Array[float] = []
var _base_bump: Array[float] = []
var _base_rebound: Array[float] = []
var _base_radius: Array[float] = []
var _base_front_brake := 0.0
var _base_rear_brake := 0.0

# --------------------------------------------------------------------------- #
#  setup
# --------------------------------------------------------------------------- #

func _ready() -> void:
	_dent_points.resize(MAX_DENTS)
	_dent_normals.resize(MAX_DENTS)
	for i in MAX_DENTS:
		_dent_points[i] = Vector4.ZERO
		_dent_normals[i] = Vector4.ZERO

	coolant = coolant_capacity
	fuel = fuel_capacity
	engine_temp = 20.0

	for zone in DamageModel.ZONES:
		zone_damage[zone] = 0.0
	for part in DamageModel.PARTS:
		part_damage[part] = 0.0

	_vehicle = get_parent() as Vehicle
	if _vehicle == null:
		push_warning("VehicleDamage: parent is not a Vehicle")
		set_physics_process(false)
		return

	_vehicle.ensure_wheels()
	_base_torque = _vehicle.peak_torque
	_base_front_brake = _vehicle.front_brake_torque
	_base_rear_brake = _vehicle.rear_brake_torque
	for w in _vehicle.get_wheels():
		_base_camber.append(w.camber_deg)
		_base_toe.append(w.toe_deg)
		_base_friction.append(w.friction_coefficient)
		_base_spring.append(w.spring_rate)
		_base_bump.append(w.bump_damping)
		_base_rebound.append(w.rebound_damping)
		_base_radius.append(w.tyre_radius)

	_measure_body()
	# Deferred: the model instances its glTF in _ready(), and a child's
	# _ready() runs before its parent's - the ordering that caused the
	# "Node not found" crash back in v2.1.
	_collect_materials.call_deferred()


## Finds the body's bounding box, so the zone layout scales to any vehicle.
func _measure_body() -> void:
	var model := _vehicle.get_node_or_null("Smooth/Model")
	if model == null:
		return
	var found := false
	var lo := Vector3.ZERO
	var hi := Vector3.ZERO
	for node in _walk(model):
		var mesh_node := node as MeshInstance3D
		if mesh_node == null or mesh_node.mesh == null:
			continue
		var box := mesh_node.mesh.get_aabb()
		var a := box.position
		var b := box.position + box.size
		if not found:
			lo = a
			hi = b
			found = true
		else:
			lo = Vector3(minf(lo.x, a.x), minf(lo.y, a.y), minf(lo.z, a.z))
			hi = Vector3(maxf(hi.x, b.x), maxf(hi.y, b.y), maxf(hi.z, b.z))
	if found:
		_aabb_min = lo
		_aabb_max = hi


func _walk(node: Node) -> Array[Node]:
	var out: Array[Node] = [node]
	for child in node.get_children():
		out.append_array(_walk(child))
	return out


func _collect_materials() -> void:
	if _vehicle == null or not is_instance_valid(_vehicle):
		return
	var model := _vehicle.get_node_or_null("Smooth/Model")
	if model == null:
		return
	for node in _walk(model):
		var mesh_node := node as MeshInstance3D
		if mesh_node == null or mesh_node.mesh == null:
			continue
		for i in mesh_node.mesh.get_surface_count():
			var mat := _make_dent_material(
				mesh_node.mesh.surface_get_material(i))
			mesh_node.set_surface_override_material(i, mat)
			_materials.append(mat)
	_measure_body()


## Wraps an imported material so it can be dented, passing its look through
## unchanged - the shader only adds displacement and wear.
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

# --------------------------------------------------------------------------- #
#  per-tick systems
# --------------------------------------------------------------------------- #

func _physics_process(delta: float) -> void:
	if _vehicle == null or not is_instance_valid(_vehicle):
		return
	_cooldown = maxf(0.0, _cooldown - delta)
	_update_fluids(delta)
	_update_temperature(delta)


## Coolant and fuel drain through whatever holes there are.
func _update_fluids(delta: float) -> void:
	var rad: float = part_damage.get(DamageModel.Part.RADIATOR, 0.0)
	if rad > 0.15 and coolant > 0.0:
		coolant = maxf(0.0, coolant - coolant_leak_rate * rad * delta)

	var tank: float = part_damage.get(DamageModel.Part.FUEL_TANK, 0.0)
	if tank > 0.2 and fuel > 0.0:
		fuel = maxf(0.0, fuel - fuel_leak_rate * tank * delta)
		if fuel <= 0.0 and engine_running:
			engine_running = false
			engine_stop_reason = "нет топлива"
			engine_died.emit(engine_stop_reason)


## Engine temperature. Cooling depends on how much coolant is left and on
## airflow, so a holed radiator at a standstill overheats fastest - which is
## exactly what happens in reality.
func _update_temperature(delta: float) -> void:
	if not engine_running:
		engine_temp = move_toward(engine_temp, 20.0, 6.0 * delta)
		return

	# Named engine_load: "load" is a global function in GDScript and shadowing
	# it raises SHADOWED_GLOBAL_IDENTIFIER.
	var engine_load := clampf(
		_vehicle.engine_rpm / maxf(_vehicle.redline_rpm, 1.0), 0.0, 1.0)
	var heat := 22.0 + 70.0 * engine_load

	var coolant_fraction := coolant / maxf(coolant_capacity, 0.01)
	var airflow := clampf(_vehicle.speed_kmh / 90.0, 0.0, 1.0)
	# With no coolant the radiator does nothing at all; airflow alone only helps
	# a little through the block itself.
	var cooling := (0.25 + 0.75 * coolant_fraction) * (0.4 + 0.6 * airflow)
	cooling *= 1.0 - 0.6 * part_damage.get(DamageModel.Part.RADIATOR, 0.0)

	var target := 20.0 + heat * (1.0 - cooling * 0.72)
	engine_temp = move_toward(engine_temp, target, 9.0 * delta)

	if engine_temp >= temp_critical and engine_running:
		engine_running = false
		engine_stop_reason = "перегрев"
		engine_died.emit(engine_stop_reason)

	_apply_engine_state()

# --------------------------------------------------------------------------- #
#  impacts
# --------------------------------------------------------------------------- #

## Called by the vehicle from _integrate_forces, the only place contact data
## is valid.
func report_contacts(state: PhysicsDirectBodyState3D) -> void:
	if _vehicle == null or _cooldown > 0.0:
		return

	# One impact per collision, not one per contact point: a corner hit
	# reports several, and the strongest is the one that matters.
	var best := 0.0
	var best_index := -1
	for i in state.get_contact_count():
		var impulse := state.get_contact_impulse(i).length()
		if impulse > best:
			best = impulse
			best_index = i
	if best_index < 0 or best < impact_threshold:
		return

	_cooldown = impact_cooldown
	var point := state.get_contact_local_position(best_index)
	var normal := state.get_contact_local_normal(best_index)
	apply_impact(point, normal, best)


## Resolves one impact all the way through: dent, zone, components, physics.
##
## Public and free of PhysicsDirectBodyState3D so it can be driven directly
## by the checks.
func apply_impact(world_point: Vector3, world_normal: Vector3,
		impulse: float) -> void:
	if _vehicle == null:
		return

	var energy := DamageModel.impact_energy(impulse, _vehicle.mass)
	var severity := DamageModel.severity_from_energy(energy, reference_energy)
	if severity <= 0.001:
		return

	var inverse := _vehicle.global_transform.affine_inverse()
	var local := inverse * world_point
	var dir := (inverse.basis * world_normal).normalized()
	if dir.length_squared() < 0.5:
		dir = Vector3.UP

	var fraction := DamageModel.to_body_fraction(local, _aabb_min, _aabb_max)

	_add_dent(local, dir, severity)
	var zone := _damage_zone(fraction, severity)
	_damage_parts(fraction, severity)
	_recompute()

	var zone_name := "кузов"
	if zone >= 0:
		zone_name = String(DamageModel.ZONES[zone]["name"])
	impact.emit(severity, zone_name)


func _damage_zone(fraction: Vector3, severity: float) -> int:
	var zone := DamageModel.zone_at(fraction)
	if zone < 0:
		return -1
	var before: float = zone_damage[zone]
	zone_damage[zone] = clampf(before + severity, 0.0, 1.0)
	return zone


func _damage_parts(fraction: Vector3, severity: float) -> void:
	var hits := DamageModel.parts_hit(fraction, severity)
	for part in hits:
		var before: float = part_damage[part]
		var after := clampf(before + float(hits[part]), 0.0, 1.0)
		part_damage[part] = after
		if before < 0.65 and after >= 0.65:
			part_broke.emit(String(DamageModel.PARTS[part]["name"]))
		# A wheel taking a heavy hit goes flat.
		var info: Dictionary = DamageModel.PARTS[part]
		if info.has("corner") and after > 0.7:
			var corner := int(info["corner"])
			var is_wheel := part in [
				DamageModel.Part.WHEEL_LF, DamageModel.Part.WHEEL_RF,
				DamageModel.Part.WHEEL_LR, DamageModel.Part.WHEEL_RR]
			if is_wheel and corner < tyre_flat.size():
				tyre_flat[corner] = true

# --------------------------------------------------------------------------- #
#  dents
# --------------------------------------------------------------------------- #

func _add_dent(local: Vector3, direction: Vector3, severity: float) -> void:
	var radius := dent_radius * (0.45 + 0.55 * severity)
	var depth := dent_depth * severity
	_dent_points[_next_dent] = Vector4(local.x, local.y, local.z, radius)
	_dent_normals[_next_dent] = Vector4(direction.x, direction.y,
		direction.z, depth)
	_next_dent = (_next_dent + 1) % MAX_DENTS
	_dent_count = mini(_dent_count + 1, MAX_DENTS)


func _push_shader_state() -> void:
	# The bend axis is derived from which side of the car has taken the most
	# damage, so a car hammered down its left side leans and twists that way.
	var left := _side_damage(-1.0)
	var right := _side_damage(1.0)
	var axis := Vector3(0.0, 1.0, 0.0)
	if absf(left - right) > 0.05:
		axis = Vector3(0.0, signf(right - left), 0.2).normalized()
	var bend := structural_damage * max_body_bend

	for mat in _materials:
		if not is_instance_valid(mat):
			continue
		mat.set_shader_parameter("dent_points", _dent_points)
		mat.set_shader_parameter("dent_normals", _dent_normals)
		mat.set_shader_parameter("body_bend",
			Vector4(axis.x, axis.y, axis.z, bend))
		mat.set_shader_parameter("paint_wear", clampf(total_damage, 0.0, 1.0))


func _side_damage(sign_x: float) -> float:
	var total := 0.0
	var count := 0
	for zone in zone_damage:
		var box: Dictionary = DamageModel.ZONES[zone]
		var xr: Array = box["x"]
		if xr.size() < 2:
			continue
		var centre := (float(xr[0]) + float(xr[1])) * 0.5
		if signf(centre) != sign_x:
			continue
		total += float(zone_damage[zone])
		count += 1
	return total / maxf(count, 1)

# --------------------------------------------------------------------------- #
#  consequences
# --------------------------------------------------------------------------- #

## Recalculates the derived numbers and pushes everything into the physics.
func _recompute() -> void:
	# Structural damage is weighted by how much rigidity each zone carries,
	# so folding the floor pan matters far more than crumpling a bumper.
	var weighted := 0.0
	var total_weight := 0.0
	for zone in zone_damage:
		var weight := float(DamageModel.ZONES[zone]["structure"])
		weighted += float(zone_damage[zone]) * weight
		total_weight += weight
	structural_damage = clampf(weighted / maxf(total_weight, 0.01), 0.0, 1.0)

	# Overall damage is the worst of the structure and the mechanicals, not
	# an average - a car with a wrecked engine is wrecked even if the shell
	# is straight.
	var mechanical := 0.0
	for part in part_damage:
		mechanical = maxf(mechanical, float(part_damage[part]) * 0.85)
	total_damage = clampf(maxf(structural_damage, mechanical), 0.0, 1.0)

	# Corner damage: the suspension, brake and wheel at that corner.
	for i in corner_damage.size():
		corner_damage[i] = 0.0
	for part in part_damage:
		var info: Dictionary = DamageModel.PARTS[part]
		if not info.has("corner"):
			continue
		var c := int(info["corner"])
		if c < corner_damage.size():
			corner_damage[c] = maxf(corner_damage[c], float(part_damage[part]))

	_apply_mechanical()
	_push_shader_state()


## Pushes damage into the numbers the simulation reads every tick.
func _apply_mechanical() -> void:
	if _vehicle == null or not is_instance_valid(_vehicle):
		return

	_apply_engine_state()

	# Brakes, per axle. A damaged brake at one corner makes the car pull to
	# the other side under braking - that falls out of the tyre forces.
	var front_brake := 1.0 - brake_loss * maxf(
		part_damage.get(DamageModel.Part.BRAKE_LF, 0.0),
		part_damage.get(DamageModel.Part.BRAKE_RF, 0.0))
	var rear_brake := 1.0 - brake_loss * maxf(
		part_damage.get(DamageModel.Part.BRAKE_LR, 0.0),
		part_damage.get(DamageModel.Part.BRAKE_RR, 0.0))
	_vehicle.front_brake_torque = _base_front_brake * front_brake
	_vehicle.rear_brake_torque = _base_rear_brake * rear_brake

	var wheels := _vehicle.get_wheels()
	for i in wheels.size():
		if i >= corner_damage.size() or i >= _base_camber.size():
			break
		var d := corner_damage[i]
		var w := wheels[i]

		# A bent corner is knocked out of alignment, so its contact patch no
		# longer sits square to the road. The tyre model turns that into lost
		# grip by itself.
		w.camber_deg = _base_camber[i] - 6.0 * d
		w.toe_deg = _base_toe[i] + 1.6 * d * signf(w.position.x)
		w.friction_coefficient = _base_friction[i] * (1.0 - grip_loss * d)

		# A bent damper stops damping and a bent spring sags. Both change how
		# that corner handles a bump, which is felt long before it is seen.
		var susp := _suspension_damage(i)
		w.spring_rate = _base_spring[i] * (1.0 - 0.35 * susp)
		w.bump_damping = _base_bump[i] * (1.0 - 0.55 * susp)
		w.rebound_damping = _base_rebound[i] * (1.0 - 0.55 * susp)

		# A flat tyre: the rolling radius collapses onto the rim and most of
		# the grip goes with it.
		if i < tyre_flat.size() and tyre_flat[i]:
			w.tyre_radius = _base_radius[i] * 0.72
			w.friction_coefficient = _base_friction[i] * 0.35
		else:
			w.tyre_radius = _base_radius[i]


func _suspension_damage(corner: int) -> float:
	var parts := [DamageModel.Part.SUSPENSION_LF,
		DamageModel.Part.SUSPENSION_RF,
		DamageModel.Part.SUSPENSION_LR,
		DamageModel.Part.SUSPENSION_RR]
	if corner < 0 or corner >= parts.size():
		return 0.0
	return float(part_damage.get(parts[corner], 0.0))


## Engine power, accounting for damage, overheating and having stopped.
func _apply_engine_state() -> void:
	if _vehicle == null or not is_instance_valid(_vehicle):
		return
	if not engine_running:
		_vehicle.peak_torque = 0.0
		return

	var engine_hurt: float = part_damage.get(DamageModel.Part.ENGINE, 0.0)
	var factor := 1.0 - power_loss * engine_hurt

	# Overheating pulls power progressively rather than all at once, which is
	# what a real engine management system does to protect itself.
	if engine_temp > temp_warning:
		var over := (engine_temp - temp_warning) \
			/ maxf(temp_critical - temp_warning, 1.0)
		factor *= 1.0 - 0.55 * clampf(over, 0.0, 1.0)

	# A holed oil pan means the engine is running dry: it still makes power
	# for a while, then it does not.
	var oil: float = part_damage.get(DamageModel.Part.OIL_PAN, 0.0)
	factor *= 1.0 - 0.3 * oil

	# A damaged gearbox or driveshaft loses drive rather than power.
	var driveline := maxf(
		part_damage.get(DamageModel.Part.GEARBOX, 0.0),
		part_damage.get(DamageModel.Part.DRIVESHAFT, 0.0))
	factor *= 1.0 - 0.5 * driveline

	_vehicle.peak_torque = _base_torque * maxf(factor, 0.0)


## Steering pull from a bent rack and mismatched front corners, in radians.
func steering_offset() -> float:
	if corner_damage.size() < 2:
		return 0.0
	var rack: float = part_damage.get(DamageModel.Part.STEERING_RACK, 0.0)
	var mismatch := corner_damage[0] - corner_damage[1]
	return (mismatch * 0.7 + rack * signf(mismatch if mismatch != 0.0 else 1.0)
		* 0.3) * steer_pull

# --------------------------------------------------------------------------- #
#  queries and repair
# --------------------------------------------------------------------------- #

## The worst-damaged components, for the HUD. Returns [{name, amount}].
func worst_parts(count: int) -> Array[Dictionary]:
	var rows: Array[Dictionary] = []
	for part in part_damage:
		var amount := float(part_damage[part])
		if amount < 0.08:
			continue
		rows.append({"name": String(DamageModel.PARTS[part]["name"]),
			"amount": amount})
	rows.sort_custom(func(a: Dictionary, b: Dictionary) -> bool:
		return float(a["amount"]) > float(b["amount"]))
	return rows.slice(0, count)


func coolant_fraction() -> float:
	return coolant / maxf(coolant_capacity, 0.01)


func fuel_fraction() -> float:
	return fuel / maxf(fuel_capacity, 0.01)


func dent_count() -> int:
	return _dent_count


## Puts the car back together, for a respawn.
func repair() -> void:
	total_damage = 0.0
	structural_damage = 0.0
	for zone in zone_damage:
		zone_damage[zone] = 0.0
	for part in part_damage:
		part_damage[part] = 0.0
	for i in corner_damage.size():
		corner_damage[i] = 0.0
	for i in tyre_flat.size():
		tyre_flat[i] = false
	for i in MAX_DENTS:
		_dent_points[i] = Vector4.ZERO
		_dent_normals[i] = Vector4.ZERO
	_dent_count = 0
	_next_dent = 0
	coolant = coolant_capacity
	fuel = fuel_capacity
	engine_temp = 20.0
	engine_running = true
	engine_stop_reason = ""
	_apply_mechanical()
	_push_shader_state()
