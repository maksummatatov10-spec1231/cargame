@tool
class_name RayWheel
extends RayCast3D

## A single corner of the vehicle: coil-over suspension + brush/Pacejka style
## tyre contact patch + rotational dynamics of the wheel itself.
##
## The node sits at the top mounting point of the damper. The ray is cast
## straight down over [member spring_length] + [member tyre_radius]; where it
## hits, the contact patch is placed.
##
## References used while building this model:
##   * H. B. Pacejka, "Tyre and Vehicle Dynamics" (Magic Formula, relaxation
##     length, load sensitivity, friction ellipse)
##   * Godot Easy Vehicle Physics (DAShoe1) and Godot Advanced Vehicle
##     (Dechode) for the raycast-suspension layout used by Godot projects
##   * Racer / edy.es notes on combined slip and the friction ellipse

# --------------------------------------------------------------------------- #
#  configuration
# --------------------------------------------------------------------------- #

@export_group("Wheel")
## Visual node that is spun and steered. Rotates around its local X axis.
@export var wheel_visual : Node3D
## Node that follows the suspension travel but never spins (hub, brake, caliper).
@export var hub_visual : Node3D
## Loaded radius of the tyre in metres.
@export var tyre_radius := 0.325
## Tread width in metres, only used for the load/aquaplaning-ish scaling.
@export var tyre_width := 0.245
## Mass of wheel + tyre + hub carrier, drives the rotational inertia.
@export var wheel_mass := 21.0
## Steered corner (front axle).
@export var is_steering := false
## Driven corner (rear axle on this car).
@export var is_driven := true
## Static toe angle in degrees. Positive = toe-in.
@export var toe_deg := 0.0
## Static camber in degrees. Negative = top of the wheel leans inwards.
@export var camber_deg := -0.8

@export_group("Suspension")
## Maximum travel of the damper in metres.
@export var spring_length := 0.16
## Spring rate in N/m. 52 800 N/m gives ~1.85 Hz on the front of a 1495 kg car.
@export var spring_rate := 52800.0
## Damper force in Ns/m while the spring is being compressed.
@export var bump_damping := 3100.0
## Damper force in Ns/m while the spring is extending again.
@export var rebound_damping := 5300.0
## Speed (m/s) above which the damper switches to its digressive high-speed
## slope; keeps big impacts from spiking the force.
@export var damper_knee_speed := 0.18
## Multiplier used for the damper curve past the knee speed.
@export var damper_fast_ratio := 0.42
## Rate of the rubber bump stop that catches the last centimetres of travel.
## Real microcellular bump stops are very stiff, which is what stops the floor
## of the car from hitting the road on a big landing.
@export var bump_stop_rate := 1200000.0
## Length of the progressive bump-stop region in metres.
@export var bump_stop_length := 0.045
## Anti-roll bar rate in N/m applied against the opposite wheel of the axle.
@export var anti_roll_rate := 12000.0

@export_group("Tyre")
## Peak friction coefficient of the tyre on a dry road.
@export var friction_coefficient := 1.55
## Slip ratio at which the longitudinal force peaks.
@export var peak_slip_ratio := 0.115
## Slip angle in degrees at which the lateral force peaks.
@export var peak_slip_angle_deg := 8.5
## Magic-formula shape factor (1.6-1.7 for a road tyre).
@export var mf_shape := 1.62
## Magic-formula curvature factor. Higher = flatter after the peak.
@export var mf_curvature := 0.35
## How much grip is lost as vertical load rises above the static load.
## mu = mu0 * (1 - load_sensitivity * (Fz / Fz_nominal - 1))
@export var load_sensitivity := 0.22
## Vertical load in newtons the tyre was designed around (roughly corner mass).
@export var nominal_load := 3800.0
## Relaxation length in metres. The contact patch needs to roll this far before
## the tyre force is fully built up; this is what keeps the model stable at
## walking pace instead of dividing by a near-zero velocity.
@export var relaxation_length := 0.42
## Rolling resistance coefficient.
@export var rolling_resistance := 0.014
## Radial stiffness of the tyre carcass in N/m. The sidewall is a spring in
## series with the coil-over and absorbs a large part of any sharp impact; a
## 245/35 R19 sits around 260 kN/m.
@export var tyre_rate := 260000.0
## Damping of the tyre carcass in Ns/m. Rubber has strong hysteresis, so a
## squashed tyre gives back noticeably less than it absorbed; without this the
## car would bounce off the ground like a ball.
@export var tyre_damping := 5200.0

# --------------------------------------------------------------------------- #
#  runtime state
# --------------------------------------------------------------------------- #

var spin := 0.0                     ## wheel angular velocity, rad/s
var spin_angle := 0.0               ## accumulated angle, used by the visuals
var steer_angle := 0.0              ## current steering angle, radians
var compression := 0.0              ## 0 = fully extended, 1 = bottomed out
var travel := 0.0                   ## metres of compression
var spring_force := 0.0             ## normal load transferred to the chassis, N
var slip_ratio := 0.0
var slip_angle := 0.0
var grounded := false
var surface_normal := Vector3.UP
var contact_point := Vector3.ZERO
var tyre_force := Vector2.ZERO      ## x = longitudinal, y = lateral (N)
var drive_torque := 0.0
var brake_torque := 0.0

var _inertia := 1.0
var _prev_travel := 0.0
var _tyre_deflection := 0.0
var _prev_tyre_deflection := 0.0
var _lag_ratio := 0.0               ## relaxation-filtered slip ratio
var _lag_tan_alpha := 0.0           ## relaxation-filtered tan(slip angle)
var _mf_stiffness := 1.685211       ## B, solved so the peak lands exactly at s = 1

func _ready() -> void:
	_inertia = 0.5 * wheel_mass * tyre_radius * tyre_radius
	enabled = true
	exclude_parent = true
	hit_from_inside = false
	collide_with_areas = false
	target_position = Vector3.DOWN * (spring_length + tyre_radius)
	if Engine.is_editor_hint():
		return
	steer_angle = deg_to_rad(toe_deg)
	rotation.y = steer_angle


## Clears the transient state, so a respawned car does not inherit the slip and
## suspension velocity it had at the moment it was reset.
func reset_state() -> void:
	travel = 0.0
	_prev_travel = 0.0
	_tyre_deflection = 0.0
	_prev_tyre_deflection = 0.0
	_lag_ratio = 0.0
	_lag_tan_alpha = 0.0
	slip_ratio = 0.0
	slip_angle = 0.0
	spring_force = 0.0
	tyre_force = Vector2.ZERO
	drive_torque = 0.0
	brake_torque = 0.0


## Rotational inertia of the wheel (kg m^2).
func get_inertia() -> float:
	return _inertia


## Longitudinal speed of the contact patch in m/s.
func get_rolling_speed() -> float:
	return spin * tyre_radius


# --------------------------------------------------------------------------- #
#  suspension
# --------------------------------------------------------------------------- #

## Casts the ray and works out the spring/damper force for this step.
## [param opposite_travel] is the travel of the other wheel on the same axle and
## drives the anti-roll bar. Returns this wheel's travel in metres.
func update_suspension(delta: float, opposite_travel: float) -> float:
	force_raycast_update()
	grounded = is_colliding()

	var raw_travel := 0.0
	var overlap := 0.0
	if grounded:
		contact_point = get_collision_point()
		surface_normal = get_collision_normal()
		# The ray origin is the top of the damper, so the length of the spring is
		# the distance to the contact minus the radius of the tyre.
		var length := global_position.distance_to(contact_point) - tyre_radius
		raw_travel = spring_length - length
		# Anything past full bump is taken up by the tyre squashing; the ray can
		# only report so much, so the excess becomes carcass deflection.
		overlap = maxf(0.0, raw_travel - spring_length)
		raw_travel = clampf(raw_travel, 0.0, spring_length)
	else:
		contact_point = to_global(target_position)
		surface_normal = global_basis.y

	_prev_tyre_deflection = _tyre_deflection
	_tyre_deflection = overlap
	travel = raw_travel
	compression = travel / maxf(spring_length, 0.0001)

	var travel_speed := (travel - _prev_travel) / delta
	_prev_travel = travel

	if not grounded:
		spring_force = 0.0
		return 0.0

	# Linear coil rate plus a progressive rubber bump stop for the last part of
	# the travel, so hard landings are absorbed instead of punching through.
	var force := spring_rate * travel
	var into_stop := travel - (spring_length - bump_stop_length)
	if into_stop > 0.0:
		var t := into_stop / maxf(bump_stop_length, 0.0001)
		force += bump_stop_rate * into_stop * t

	# Digressive damper: full rate up to the knee speed, reduced slope past it.
	var damp_rate := bump_damping if travel_speed > 0.0 else rebound_damping
	var v := absf(travel_speed)
	var damp_force := 0.0
	if v <= damper_knee_speed:
		damp_force = damp_rate * v
	else:
		damp_force = damp_rate * damper_knee_speed \
			+ damp_rate * damper_fast_ratio * (v - damper_knee_speed)
	force += signf(travel_speed) * damp_force

	# Anti-roll bar couples the two wheels of the axle.
	force += anti_roll_rate * (travel - opposite_travel)

	# The tyre carcass is a stiff spring in series with the damper. On a hard
	# landing it carries the load once the coil is fully compressed, which is
	# what keeps the impact finite instead of letting the body punch through.
	if _tyre_deflection > 0.0:
		var tyre_speed := (_tyre_deflection - _prev_tyre_deflection) / delta
		force += tyre_rate * _tyre_deflection
		# Damped in both directions: the carcass resists being squashed and,
		# thanks to hysteresis, also holds back as it springs out again.
		force += tyre_damping * tyre_speed
		force = maxf(force, 0.0)

	spring_force = maxf(force, 0.0)
	return travel


# --------------------------------------------------------------------------- #
#  tyre
# --------------------------------------------------------------------------- #

## Normalised magic formula. Returns mu/mu_peak for a normalised slip of 1.0 at
## the peak, so the caller only has to supply peak slip values it understands.
func _magic_formula(s: float) -> float:
	var bs := _mf_stiffness * s
	return sin(mf_shape * atan(bs - mf_curvature * (bs - atan(bs))))


## Builds the contact-patch forces. [param contact_velocity] is the velocity of
## the chassis at the contact point in global space.
func update_tyre(delta: float, contact_velocity: Vector3) -> void:
	if not grounded or spring_force <= 0.0:
		tyre_force = Vector2.ZERO
		slip_ratio = 0.0
		slip_angle = 0.0
		_lag_ratio = 0.0
		_lag_tan_alpha = 0.0
		return

	# Build a frame that lies in the contact plane, so slopes are handled
	# correctly instead of using the flat world plane.
	var wheel_right := global_basis.x
	var forward := surface_normal.cross(wheel_right)
	if forward.length_squared() < 1e-8:
		tyre_force = Vector2.ZERO
		return
	forward = forward.normalized()
	var right := forward.cross(surface_normal).normalized()

	var v_long := contact_velocity.dot(forward)
	var v_lat := contact_velocity.dot(right)

	# --- transient slip (relaxation length) ------------------------------- #
	# Instead of dividing by |v_long|, which explodes near standstill, the slip
	# quantities are integrated as first order lags with the relaxation length
	# of the carcass. This is the standard Pacejka transient model and it makes
	# the tyre behave sensibly from 0 km/h upwards.
	var speed := maxf(absf(v_long), 0.35)
	var lag_gain := speed / maxf(relaxation_length, 0.01)

	var target_ratio := (spin * tyre_radius - v_long) / speed
	var target_tan_alpha := -v_lat / speed

	var alpha_blend := clampf(lag_gain * delta, 0.0, 1.0)
	_lag_ratio = lerpf(_lag_ratio, target_ratio, alpha_blend)
	_lag_tan_alpha = lerpf(_lag_tan_alpha, target_tan_alpha, alpha_blend)

	slip_ratio = _lag_ratio
	slip_angle = atan(_lag_tan_alpha)

	# --- combined slip ----------------------------------------------------- #
	var peak_tan := tan(deg_to_rad(peak_slip_angle_deg))
	var nx := _lag_ratio / peak_slip_ratio
	var ny := _lag_tan_alpha / peak_tan
	var combined := sqrt(nx * nx + ny * ny)

	if combined < 1e-5:
		tyre_force = Vector2.ZERO
		return

	# Load sensitivity: a tyre carrying twice the load does not make twice the
	# grip, which is what produces load transfer understeer/oversteer.
	var load_ratio := spring_force / maxf(nominal_load, 1.0)
	var mu := friction_coefficient * (1.0 - load_sensitivity * (load_ratio - 1.0))
	mu = clampf(mu, 0.35 * friction_coefficient, 1.35 * friction_coefficient)
	# Camber thrust: a leaning tyre loses a little of its footprint.
	mu *= cos(deg_to_rad(camber_deg)) * 0.02 + 0.98

	var total := mu * spring_force * _magic_formula(combined)

	# Friction ellipse: the direction of the resultant follows the direction of
	# the combined slip vector, so throttle eats into cornering grip.
	var fx := total * nx / combined
	var fy := total * ny / combined

	# The tyre can never push harder than the wheel is being driven or braked,
	# otherwise a locked wheel would generate force out of nothing.
	var max_from_spin := absf(spin * tyre_radius - v_long) * _inertia \
		/ maxf(tyre_radius * tyre_radius * delta, 1e-6)
	var torque_limit := (absf(drive_torque) + absf(brake_torque)) / tyre_radius
	var fx_limit := maxf(max_from_spin, torque_limit) + mu * spring_force * 0.05
	fx = clampf(fx, -fx_limit, fx_limit)

	# Rolling resistance always opposes the direction of travel.
	if absf(v_long) > 0.05:
		var crr := rolling_resistance * (1.0 + 0.0006 * v_long * v_long)
		fx -= signf(v_long) * crr * spring_force

	tyre_force = Vector2(fx, fy)


## Integrates the wheel's own rotation from the drive, brake and road torques.
func update_spin(delta: float) -> void:
	var road_torque := -tyre_force.x * tyre_radius
	var net := drive_torque + road_torque

	if brake_torque > 0.0:
		# Solve the brake implicitly: work out the spin the wheel would have
		# without the brake, then remove up to that much so it can lock but
		# never spin backwards because of braking alone.
		var free_spin := spin + net / _inertia * delta
		var brake_delta := brake_torque / _inertia * delta
		if absf(free_spin) <= brake_delta:
			spin = 0.0
		else:
			spin = free_spin - signf(free_spin) * brake_delta
	else:
		spin += net / _inertia * delta

	if not grounded:
		# Windage and bearing drag while airborne.
		spin -= spin * clampf(1.2 * delta, 0.0, 1.0)

	spin = clampf(spin, -400.0, 400.0)


## Applies the contact-patch forces to the chassis.
func apply_forces(body: RigidBody3D) -> void:
	if not grounded:
		return
	var offset := contact_point - body.global_position
	if spring_force > 0.0:
		body.apply_force(surface_normal * spring_force, offset)

	var wheel_right := global_basis.x
	var forward := surface_normal.cross(wheel_right)
	if forward.length_squared() < 1e-8:
		return
	forward = forward.normalized()
	var right := forward.cross(surface_normal).normalized()
	body.apply_force(forward * tyre_force.x + right * tyre_force.y, offset)


# --------------------------------------------------------------------------- #
#  visuals — animation 1: rolling, animation 2: steering
# --------------------------------------------------------------------------- #

## Sets the steering angle of this corner in radians (already Ackermann
## corrected by the vehicle).
func set_steer(angle: float) -> void:
	steer_angle = angle + deg_to_rad(toe_deg)
	rotation.y = steer_angle


## Drives the wheel meshes. Called every rendered frame so the animation stays
## smooth even though the physics runs at a fixed tick rate.
func update_visuals(delta: float) -> void:
	if wheel_visual == null and hub_visual == null:
		return
	spin_angle = wrapf(spin_angle + spin * delta, -TAU, TAU)
	# The wheel meshes are children of this RayCast3D, which is already yawed by
	# set_steer(), so the steering animation comes for free and only the
	# suspension lift, the camber and the rolling spin are applied here.
	var lift := -(spring_length - travel)
	var camber := deg_to_rad(camber_deg) * signf(position.x)
	if hub_visual:
		hub_visual.position.y = lift
		hub_visual.rotation = Vector3(0.0, 0.0, camber)
	if wheel_visual:
		wheel_visual.position.y = lift
		wheel_visual.rotation = Vector3(spin_angle, 0.0, camber)
