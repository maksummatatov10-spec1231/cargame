class_name VehicleRecovery
extends Node

## Gets a stranded car moving again.
##
## Split out of vehicle.gd, which had grown past a thousand lines. The
## behaviour is unchanged; see update() for why it exists at all.

var _v: Vehicle
var _stuck_timer := 0.0
var _unstick_timer := 0.0
var _flip_timer := 0.0


func _ready() -> void:
	_v = get_parent() as Vehicle
	if _v == null:
		push_warning("VehicleRecovery: parent is not a Vehicle")
		set_process(false)


## Clears the timers, for a respawn.
func reset() -> void:
	_stuck_timer = 0.0
	_unstick_timer = 0.0
	_flip_timer = 0.0


## Gets the car out of the two ways it can be stranded.
##
## Neither of these is a grip problem. First gear puts 15.9 kN at the road,
## which is 1.08 g, and the steepest ground on the whole map is a 0.85 grade -
## even on dirt (effective mu 0.96) there is nothing here the car cannot climb.
## What actually happens is geometric:
##
##  1. **Beaching.** The worst crest on the map rises 1.00 m over the 2.63 m
##     wheelbase. The body sits about 0.2 m above the line between the contact
##     patches, so the floor grounds out and the wheels lift off. No amount of
##     throttle helps because no wheel is touching anything.
##  2. **On its roof.** Nothing in the simulation can ever right it.
##
## The fix is deliberately mild: it only fires when the car is being asked to
## move and genuinely is not, it lifts rather than teleports, and it stops as
## soon as a wheel finds grip again. Driving normally never triggers it.
func update(delta: float, forward_speed: float) -> void:
	if _v == null or not is_instance_valid(_v):
		return
	var upright := _v.global_basis.y.dot(Vector3.UP)

	# --- on its roof --------------------------------------------------- #
	if _v.auto_right and upright < -0.2 and _v.linear_velocity.length() < 2.0:
		_flip_timer += delta
		if _flip_timer > _v.flip_time:
			_right_the_car()
			_flip_timer = 0.0
		return
	_flip_timer = 0.0

	# --- beached ------------------------------------------------------- #
	var wants_to_move := maxf(_v.raw_forward, _v.raw_backward) > 0.2
	var moving := absf(forward_speed) > _v.stuck_speed \
		or _v.linear_velocity.length() > _v.stuck_speed
	var wheels_down := 0
	for w in _v.get_wheels():
		if w.grounded and w.spring_force > 1.0:
			wheels_down += 1

	# Being grounded on three or four wheels and simply not accelerating is a
	# driving problem, not a stuck one, so only a car that has lost most of
	# its contact patches counts.
	if wants_to_move and not moving and wheels_down < 2:
		_stuck_timer += delta
	else:
		_stuck_timer = maxf(0.0, _stuck_timer - delta * 2.0)

	if _stuck_timer > _v.stuck_time and _unstick_timer <= 0.0:
		_unstick_timer = _v.unstick_duration
		_stuck_timer = 0.0

	if _unstick_timer > 0.0:
		_unstick_timer = maxf(0.0, _unstick_timer - delta)
		# Lift the whole car just enough to unload the floor, and push it the
		# way the driver is asking. Applied at the centre of _v.mass so it does
		# not spin the car.
		var lift := Vector3.UP * _v.mass * 9.81 * _v.unstick_lift
		var direction := -_v.global_basis.z if _v.raw_forward > _v.raw_backward else _v.global_basis.z
		# Along the ground, not into it.
		direction = (direction - Vector3.UP * direction.dot(Vector3.UP)).normalized()
		_v.apply_central_force(lift + direction * _v.mass * 2.6)
		# Kill the roll and pitch rates so it settles flat rather than
		# bouncing off at an angle.
		_v.angular_velocity *= 0.86


## Rolls the car back onto its wheels, in place, at rest.
func _right_the_car() -> void:
	var yaw := _v.global_basis.get_euler().y
	var lift := 0.6
	if _v._terrain != null:
		lift = _v._terrain.sample_height(_v.global_position.x, _v.global_position.z) \
			+ 0.8 - _v.global_position.y
		lift = maxf(lift, 0.4)
	var upright := Transform3D(Basis(Vector3.UP, yaw),
		_v.global_position + Vector3.UP * lift)
	PhysicsServer3D.body_set_state(_v.get_rid(),
		PhysicsServer3D.BODY_STATE_TRANSFORM, upright)
	PhysicsServer3D.body_set_state(_v.get_rid(),
		PhysicsServer3D.BODY_STATE_LINEAR_VELOCITY, Vector3.ZERO)
	PhysicsServer3D.body_set_state(_v.get_rid(),
		PhysicsServer3D.BODY_STATE_ANGULAR_VELOCITY, Vector3.ZERO)
	_v.linear_velocity = Vector3.ZERO
	_v.angular_velocity = Vector3.ZERO
	for w in _v.get_wheels():
		w.spin = 0.0
		w.reset_state()


