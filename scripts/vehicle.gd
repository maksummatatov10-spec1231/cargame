class_name Vehicle
extends RigidBody3D

## Rear wheel drive vehicle built on top of four [RayWheel] corners.
##
## The chassis is a plain [RigidBody3D]; every force that moves the car comes
## from a tyre contact patch, so slopes, jumps, weight transfer and landings all
## fall out of the simulation instead of being scripted.
##
## Physics loop, once per tick:
##   1. steering (speed sensitive, Ackermann corrected)
##   2. suspension raycasts + anti-roll bars       -> normal load
##   3. engine / gearbox / differential            -> drive torque
##   4. tyre model per corner                      -> contact forces
##   5. wheel spin integration
##   6. forces are applied to the body, plus aero

signal gear_changed(gear: int)

# --------------------------------------------------------------------------- #
#  configuration
# --------------------------------------------------------------------------- #

@export_group("Chassis")
## Kerb mass of the car in kilograms (BMW 1M ~ 1495 kg).
@export var kerb_mass := 1495.0
## Centre of mass offset relative to the body origin, in metres.
@export var centre_of_mass := Vector3(0.0, 0.46, 0.06)
## Share of the car's weight carried by the front axle (BMW 1M is 52/48).
@export_range(0.3, 0.7) var front_weight_bias := 0.523
## Width, height and length of the body in metres, used to build the inertia
## tensor. A pickup is far taller and longer than the coupe, and using the
## coupe's numbers would make it roll and yaw like a much smaller car.
@export var body_extents := Vector3(1.80, 1.42, 4.38)

@export_group("Engine")
## Peak crankshaft torque in Nm (1M: 450 Nm on overboost).
@export var peak_torque := 450.0
## Engine speed of the torque peak.
@export var peak_torque_rpm := 3000.0
## Engine speed of the power peak.
@export var peak_power_rpm := 5900.0
@export var idle_rpm := 850.0
@export var redline_rpm := 7000.0
## Rotational inertia of the crank + flywheel, kg m^2.
@export var engine_inertia := 0.24
## Engine braking coefficient, Nm per rad/s.
@export var engine_braking := 0.055
## Extra torque multiplier while the turbo button (Shift) is held.
@export var boost_multiplier := 1.45
## How quickly the turbo spools up and down, in units per second.
@export var boost_spool_rate := 2.6

@export_group("Drivetrain")
@export var gear_ratios : Array[float] = [4.11, 2.32, 1.54, 1.18, 1.00, 0.85]
@export var reverse_ratio := 3.73
@export var final_drive := 3.15
@export var drivetrain_efficiency := 0.90
## Upshift/downshift points as a fraction of the redline.
@export var upshift_fraction := 0.94
@export var downshift_fraction := 0.42
## Seconds the clutch is open during an automatic shift.
@export var shift_time := 0.22
## How long the brake must be held, at a standstill, to select reverse.
@export var reverse_select_delay := 0.45
## Road speed in m/s at which the clutch is fully engaged. Below this it slips,
## so the engine can idle without dragging the car along; without it a car left
## in gear would creep forever on idle torque. Kept low so pulling away is
## immediate rather than mushy.
@export var clutch_engage_speed := 0.9
## 0 = open diff, 1 = fully locked. A limited slip diff sits in between.
@export_range(0.0, 1.0) var differential_lock := 0.45
## Drive all four wheels instead of just the rear axle.
@export var all_wheel_drive := false
## With AWD, the share of torque sent forward. 0.4 is a typical rear bias.
@export_range(0.0, 1.0) var front_torque_split := 0.4

@export_group("Brakes")
## Peak brake torque at the front axle, Nm.
@export var front_brake_torque := 2400.0
@export var rear_brake_torque := 1500.0
@export var handbrake_torque := 2600.0

@export_group("Steering")
@export var max_steer_deg := 33.0
## Steering angle is scaled down to this fraction at [member steer_speed_falloff].
@export var high_speed_steer_scale := 0.34
@export var steer_speed_falloff := 42.0
## How fast the virtual steering rack moves, in units per second.
@export var steer_rate := 3.2
@export var steer_return_rate := 5.0
## Ackermann factor, 0 = parallel steering, 1 = perfect Ackermann.
@export_range(0.0, 1.0) var ackermann := 0.72

@export_group("Surfaces")
## Grip, rolling drag and looseness for each terrain surface.
## Index order matches Terrain.Surface: grass, dirt, rock.
@export var surface_grip : Array[float] = [0.72, 0.62, 0.94]
@export var surface_drag : Array[float] = [2.6, 3.4, 1.2]
@export var surface_looseness : Array[float] = [0.55, 1.0, 0.0]

@export_group("Assists")
## Traction control: cuts engine torque when the driven wheels spin up.
## This is what stops a 450 Nm rear-drive car from lighting up its tyres and
## spinning at the slightest provocation. 0 disables it.
@export_range(0.0, 1.0) var traction_control := 0.85
## Slip ratio the traction control aims to hold. Peak grip is near 0.115, so
## sitting just above it keeps the acceleration without the snap.
@export var traction_target_slip := 0.16
## How far past the tyres' grip the predictive limiter is allowed to go.
## 1.0 is exactly at the limit; a little over lets it use peak slip.
@export var traction_headroom := 1.15
## Stability control: trims torque and adds a corrective yaw moment when the
## car rotates faster than the steering asks for.
@export_range(0.0, 1.0) var stability_control := 0.6
## Yaw error in rad/s that the stability control tolerates before it acts.
@export var stability_deadband := 0.18

@export_group("Aerodynamics")
## Drag area, Cd * A in m^2.
@export var drag_area := 0.69
## Downforce coefficient at the front and rear axle (N per (m/s)^2).
@export var front_downforce := 0.28
@export var rear_downforce := 0.42
@export var air_density := 1.2

# --------------------------------------------------------------------------- #
#  runtime state
# --------------------------------------------------------------------------- #

var throttle := 0.0
var boost := 0.0                    ## 0..1, how hard the turbo button is held
var brake_input := 0.0
## Untouched key state: W and S. The pedal mapping below is derived from these
## every tick, so nothing can clobber the values the gear logic depends on.
var raw_forward := 0.0
var raw_backward := 0.0
var handbrake_input := 0.0
var steer_input := 0.0

var engine_rpm := 850.0
var gear := 1                       ## -1 reverse, 0 neutral, 1..n forward
var clutch := 1.0                   ## 1 engaged, 0 fully open
var speed_kmh := 0.0
var wheel_slip := 0.0               ## largest normalised slip, for the HUD

var _wheels : Array[RayWheel] = []
var _front : Array[RayWheel] = []
var _rear : Array[RayWheel] = []
var _steer_position := 0.0
var _shift_timer := 0.0
var _reverse_hold := 0.0
var _tc_cut := 0.0
var _reverse_armed := false
var _brake_was_down := false
var _engine_speed := 0.0            ## rad/s
var _spawn_transform := Transform3D.IDENTITY
var _wheelbase := 2.63
var _track := 1.49

var _terrain : Terrain

@onready var _wheel_root : Node3D = $Wheels
# The visual model now hangs off the smoothing node, so it is looked up rather
# than assumed to be a direct child. Using $Model here threw
# "Node not found: Model" on every spawn.
@onready var _model : Node3D = _find_model()


## Finds the visual model wherever it sits in the vehicle's subtree.
func _find_model() -> Node3D:
	var direct := get_node_or_null("Model")
	if direct != null:
		return direct as Node3D
	var smoothed := get_node_or_null("Smooth/Model")
	if smoothed != null:
		return smoothed as Node3D
	return null


func _ready() -> void:
	mass = kerb_mass
	center_of_mass_mode = RigidBody3D.CENTER_OF_MASS_MODE_CUSTOM
	center_of_mass = centre_of_mass
	# Let the solver run tight; the tyre forces change quickly.
	continuous_cd = true
	max_contacts_reported = 4
	contact_monitor = true
	can_sleep = false
	# Damping is left at zero: everything is done by the tyres and the aero.
	linear_damp = 0.0
	angular_damp = 0.0

	ensure_wheels()

	if _front.size() == 2 and _rear.size() == 2:
		_wheelbase = absf(_front[0].position.z - _rear[0].position.z)
		_track = absf(_front[0].position.x - _front[1].position.x)

	_engine_speed = idle_rpm * TAU / 60.0
	_spawn_transform = global_transform
	_apply_inertia_tensor()

	# The terrain is a sibling in the main scene; found once rather than
	# searched for every tick.
	var found := get_tree().get_nodes_in_group("terrain")
	if not found.is_empty():
		_terrain = found[0] as Terrain

	# Each corner needs to know the share of the car it carries, so it can size
	# its own force limits correctly.
	for w in _wheels:
		var share := 0.5 * (front_weight_bias if w.is_steering else 1.0 - front_weight_bias)
		w.supported_mass = mass * share


## A box inertia tensor built from the real dimensions of the car keeps the
## rotational behaviour believable instead of relying on the auto-computed one
## from the convex hulls.
func _apply_inertia_tensor() -> void:
	var w := body_extents.x
	var h := body_extents.y
	var l := body_extents.z
	var k := mass / 12.0
	# Slightly reduced yaw inertia, real cars concentrate mass between the axles.
	inertia = Vector3(
		k * (h * h + l * l),
		k * (w * w + l * l) * 0.86,
		k * (w * w + h * h))


# --------------------------------------------------------------------------- #
#  input
# --------------------------------------------------------------------------- #

func _process(delta: float) -> void:
	for w in _wheels:
		w.update_visuals(delta)
	if _model != null and _model.has_method("set_steering"):
		_model.set_steering(steer_input, delta)


func _gather_input(delta: float) -> void:
	# Raw key state, never modified afterwards. The reverse pedal swap used to
	# overwrite `throttle` here, which made it impossible to leave reverse: the
	# gear logic tests `throttle > 0.5`, but by the time it ran the value had
	# already been replaced with the brake key. Keeping the raw inputs separate
	# from the mapped pedals is what stops that whole class of bug.
	raw_forward = Input.get_action_strength("drive_forward")
	raw_backward = Input.get_action_strength("drive_backward")
	throttle = raw_forward
	brake_input = raw_backward
	handbrake_input = Input.get_action_strength("handbrake")

	# Turbo: only builds while the throttle is actually open, and spools with a
	# short lag rather than switching on instantly, like real boost pressure.
	var boost_target := Input.get_action_strength("turbo") * maxf(raw_forward, 0.0)
	boost = move_toward(boost, boost_target, boost_spool_rate * delta)

	var target := Input.get_action_strength("steer_left") - Input.get_action_strength("steer_right")
	var rate := steer_rate if absf(target) > 0.01 else steer_return_rate
	# Harder to add steering lock the faster you go, which is what a real rack
	# feels like and stops the car from being twitchy at speed.
	if absf(target) > absf(_steer_position) and signf(target) == signf(_steer_position):
		rate *= clampf(1.0 - speed_kmh / 260.0, 0.35, 1.0)
	_steer_position = move_toward(_steer_position, target, rate * delta)
	steer_input = _steer_position

	if Input.is_action_just_pressed("reset_car"):
		reset_to_spawn()


## Sets the point the car returns to on respawn.
func set_spawn(point: Transform3D) -> void:
	_spawn_transform = point


## Teleports the car back to where it started.
##
## Assigning global_transform on a RigidBody3D only moves the node, and the
## physics server overwrites it again on the next tick. The move has to go
## through the server, which is what PhysicsServer3D.body_set_state does.
func reset_to_spawn() -> void:
	PhysicsServer3D.body_set_state(get_rid(),
		PhysicsServer3D.BODY_STATE_TRANSFORM, _spawn_transform)
	PhysicsServer3D.body_set_state(get_rid(),
		PhysicsServer3D.BODY_STATE_LINEAR_VELOCITY, Vector3.ZERO)
	PhysicsServer3D.body_set_state(get_rid(),
		PhysicsServer3D.BODY_STATE_ANGULAR_VELOCITY, Vector3.ZERO)
	linear_velocity = Vector3.ZERO
	angular_velocity = Vector3.ZERO

	for w in _wheels:
		w.spin = 0.0
		w.reset_state()
	gear = 1
	_shift_timer = 0.0
	_steer_position = 0.0
	boost = 0.0
	_reverse_hold = 0.0
	_reverse_armed = false
	_brake_was_down = false
	raw_forward = 0.0
	raw_backward = 0.0
	_tc_cut = 0.0
	_engine_speed = idle_rpm * TAU / 60.0


# --------------------------------------------------------------------------- #
#  physics step
# --------------------------------------------------------------------------- #

func _physics_process(delta: float) -> void:
	_gather_input(delta)

	var vel := linear_velocity
	speed_kmh = vel.length() * 3.6
	var forward_speed := vel.dot(-global_basis.z)

	_update_steering()
	_update_suspension(delta)
	_update_surfaces()
	_update_brakes(forward_speed)
	_update_drivetrain(delta, forward_speed)
	_update_tyres(delta)

	_apply_stability_control(forward_speed)

	for w in _wheels:
		w.update_spin(delta)
		w.apply_forces(self)

	_apply_aerodynamics(vel)
	_update_telemetry()


func _update_steering() -> void:
	# Speed sensitive steering ratio. (Named ratio, not scale: "scale" would
	# shadow Node3D.scale.)
	var lock_ratio := lerpf(1.0, high_speed_steer_scale,
		clampf(speed_kmh / steer_speed_falloff, 0.0, 1.0))
	var angle := deg_to_rad(max_steer_deg) * steer_input * lock_ratio

	if absf(angle) < 1e-5 or _front.size() != 2:
		for w in _front:
			w.set_steer(angle)
		return

	# True Ackermann: the inner wheel has to turn through a bigger angle because
	# it follows a tighter radius around the same turn centre.
	var radius := _wheelbase / tan(absf(angle))
	var inner := atan(_wheelbase / maxf(radius - _track * 0.5, 0.1))
	var outer := atan(_wheelbase / (radius + _track * 0.5))
	inner = lerpf(absf(angle), inner, ackermann)
	outer = lerpf(absf(angle), outer, ackermann)

	var turning_left := angle > 0.0
	for w in _front:
		var is_left := w.position.x < 0.0
		var mag := inner if is_left == turning_left else outer
		w.set_steer(signf(angle) * mag)


func _update_suspension(delta: float) -> void:
	# Anti-roll bars need the travel of the opposite wheel; using last tick's
	# value keeps both sides symmetric instead of favouring whichever is
	# evaluated first.
	var previous : Array[float] = []
	for w in _wheels:
		previous.append(w.travel)
	for i in _wheels.size():
		var opposite := _opposite_index(i)
		_wheels[i].update_suspension(delta, previous[opposite])


func _opposite_index(i: int) -> int:
	var w := _wheels[i]
	for j in _wheels.size():
		if j == i:
			continue
		var o := _wheels[j]
		if is_equal_approx(signf(o.position.z), signf(w.position.z)):
			return j
	return i


## Tells every wheel what it is driving on, so grip and the particle effects
## follow the ground rather than being the same everywhere.
func _update_surfaces() -> void:
	if _terrain == null:
		return
	for w in _wheels:
		if not w.grounded:
			continue
		var s := _terrain.sample_surface(w.contact_point.x, w.contact_point.z)
		w.surface_type = s
		w.surface_grip = surface_grip[s] if s < surface_grip.size() else 1.0
		w.surface_drag = surface_drag[s] if s < surface_drag.size() else 1.0
		w.surface_looseness = surface_looseness[s] if s < surface_looseness.size() else 0.0


func _update_tyres(delta: float) -> void:
	# Velocity of the chassis at each contact patch: the body's linear velocity
	# plus the tangential velocity from its rotation. This is what makes weight
	# transfer, yaw damping and slides emerge instead of being faked.
	for w in _wheels:
		var arm := w.contact_point - global_position
		w.update_tyre(delta, linear_velocity + angular_velocity.cross(arm))


# --------------------------------------------------------------------------- #
#  engine, gearbox, differential
# --------------------------------------------------------------------------- #

## Torque curve of a turbocharged straight six: builds fast, flat plateau, then
## falls away towards the limiter.
func engine_torque_at(rpm: float) -> float:
	if rpm < idle_rpm * 0.4:
		return 0.0
	var t := 0.0
	if rpm < peak_torque_rpm:
		var x := clampf((rpm - idle_rpm * 0.5) / maxf(peak_torque_rpm - idle_rpm * 0.5, 1.0), 0.0, 1.0)
		t = peak_torque * (0.42 + 0.58 * sin(x * PI * 0.5))
	elif rpm < peak_power_rpm:
		t = peak_torque
	else:
		var x := clampf((rpm - peak_power_rpm) / maxf(redline_rpm - peak_power_rpm, 1.0), 0.0, 1.0)
		t = peak_torque * (1.0 - 0.34 * x)
	if rpm > redline_rpm:
		t *= clampf(1.0 - (rpm - redline_rpm) / 260.0, 0.0, 1.0)
	return t


func current_ratio() -> float:
	if gear == 0:
		return 0.0
	if gear < 0:
		return -reverse_ratio * final_drive
	return gear_ratios[mini(gear - 1, gear_ratios.size() - 1)] * final_drive


func _update_drivetrain(delta: float, forward_speed: float) -> void:
	if _shift_timer > 0.0:
		_shift_timer = maxf(0.0, _shift_timer - delta)

	# The clutch is fully open during a shift, and slips towards open as the car
	# comes to a stop, which is what lets the engine idle at a standstill.
	if _shift_timer > 0.0:
		clutch = 0.0
	else:
		# Engaged by road speed, and pressing the throttle bites it immediately -
		# that is what a driver does with their left foot when pulling away.
		var engaged := clampf(absf(forward_speed) / maxf(clutch_engage_speed, 0.01), 0.0, 1.0)
		clutch = clampf(maxf(engaged, throttle * 1.6), 0.0, 1.0)

	_update_gear_selection(forward_speed, delta)

	var ratio := current_ratio()

	# Average driven wheel speed feeds back into the engine through the clutch.
	var driven_spin := 0.0
	var feedback := _driven()
	for w in feedback:
		driven_spin += w.spin
	driven_spin /= maxf(feedback.size(), 1)

	var idle_speed := idle_rpm * TAU / 60.0
	if absf(ratio) > 0.01 and clutch > 0.99:
		# Clutch locked: engine speed follows the wheels.
		_engine_speed = absf(driven_spin * ratio)
	else:
		# Free revving, blended towards the wheel speed as the clutch bites.
		var free_torque := engine_torque_at(_rpm()) * throttle \
			* (1.0 + (boost_multiplier - 1.0) * boost) \
			- engine_braking * maxf(_engine_speed - idle_speed, 0.0)
		_engine_speed += free_torque / engine_inertia * delta
		if clutch > 0.0 and absf(ratio) > 0.01:
			_engine_speed = lerpf(_engine_speed, absf(driven_spin * ratio), clutch)

	_engine_speed = clampf(_engine_speed, idle_rpm * TAU / 60.0, (redline_rpm + 400.0) * TAU / 60.0)
	engine_rpm = _rpm()

	_auto_shift()

	var crank_torque := engine_torque_at(engine_rpm) * throttle
	crank_torque *= 1.0 + (boost_multiplier - 1.0) * boost
	crank_torque *= _traction_control_factor(ratio)
	# Engine braking only exists above idle; at idle the engine is producing just
	# enough torque to keep itself turning, not to drag the car backwards.
	crank_torque -= engine_braking * maxf(_engine_speed - idle_speed, 0.0) \
		* (1.0 - throttle * 0.85)
	var axle_torque := crank_torque * ratio * drivetrain_efficiency * clutch

	_distribute_drive(axle_torque)


## Chooses between drive and reverse.
##
## The old rule was "holding the brake below 0.6 m/s selects reverse", which is
## exactly what a player does when braking to a standstill: the car silently
## dropped into reverse at ~2 km/h, and from then on W was the brake and S was
## the throttle, so it would not go forwards again. That is the "drove for
## 50 seconds, stopped, now it will not move" bug.
##
## Reverse now needs a *fresh* press of the brake made while already stopped.
## Arming on release alone was not enough: braking from speed to a halt and
## simply keeping the pedal down still slid into reverse, because the pedal had
## been released at some point earlier in the lap. So the press that stopped
## the car is remembered and explicitly disqualified.
func _update_gear_selection(forward_speed: float, delta: float) -> void:
	var stationary := absf(forward_speed) < 0.25 and speed_kmh < 1.5
	# Raw keys, deliberately: `throttle` and `brake_input` have already been
	# swapped for reverse by this point, so testing them here would mean the
	# car could never be asked to go forward again.
	var braking := raw_backward > 0.5
	var press_started := braking and not _brake_was_down
	_brake_was_down = braking

	if gear > 0:
		# Reverse needs a brake press that BEGINS while the car is already
		# stopped. Arming merely on release was not enough: a driver who has
		# not touched the brake yet is armed by default, so the very press that
		# brings the car to a halt would latch reverse - which is the "drove,
		# stopped, cannot go forward" bug. Tying it to the start of the press
		# closes that, because the press that stops the car began while moving.
		if press_started:
			_reverse_armed = stationary
			_reverse_hold = 0.0
		elif not braking:
			_reverse_armed = false
			_reverse_hold = 0.0

		if stationary and _reverse_armed and braking and raw_forward < 0.05:
			_reverse_hold += delta
			if _reverse_hold >= reverse_select_delay:
				gear = -1
				_reverse_hold = 0.0
				_reverse_armed = false
				gear_changed.emit(gear)
		else:
			_reverse_hold = 0.0
	elif gear < 0:
		# Coming back out of reverse is immediate; the car must not get stuck.
		# This reads raw_forward so that pressing W always works, whatever the
		# pedal mapping has done to `throttle`.
		_reverse_armed = false
		_reverse_hold = 0.0
		if stationary and raw_forward > 0.5 and raw_backward < 0.05:
			gear = 1
			gear_changed.emit(gear)


func _rpm() -> float:
	return _engine_speed * 60.0 / TAU


func _auto_shift() -> void:
	if _shift_timer > 0.0 or gear < 0:
		return
	if gear > 0 and engine_rpm > redline_rpm * upshift_fraction and gear < gear_ratios.size():
		gear += 1
		_shift_timer = shift_time
		gear_changed.emit(gear)
	elif gear > 1 and engine_rpm < redline_rpm * downshift_fraction:
		gear -= 1
		_shift_timer = shift_time * 0.6
		gear_changed.emit(gear)


## Splits the axle torque between the two driven wheels. An open differential
## sends equal torque to both, which means a lifted wheel spins up and the car
## goes nowhere; the LSD term biases torque back towards the slower wheel.
## Limits engine torque to what the driven tyres can actually put down.
##
## Reacting to wheelspin after it happens is too late: by the time the slip
## ratio has climbed, the friction ellipse has already eaten the rear tyres'
## cornering force and the car is sideways. So this works out, up front, how
## much torque the contact patches can take, and never asks for more.
##
##     F_max  = mu * Fz            per driven wheel
##     T_max  = F_max * radius     torque that force can absorb
##     crank  = T_max / ratio      what the engine may send
##
## A small headroom factor lets the tyres run slightly past peak slip, which is
## where they make the most grip, without tipping into a slide. On top of that
## a reactive term trims the torque if slip still creeps up (bumps, kerbs).
func _traction_control_factor(ratio: float) -> float:
	var driven_wheels := _driven()
	if traction_control <= 0.0 or driven_wheels.is_empty():
		return 1.0

	var delta := get_physics_process_delta_time()

	# --- reactive part: catch slip that got through anyway --------------- #
	var worst := 0.0
	for w in driven_wheels:
		if w.grounded:
			worst = maxf(worst, w.slip_ratio)
	if worst <= traction_target_slip:
		_tc_cut = maxf(0.0, _tc_cut - 4.0 * delta)
	else:
		var excess := (worst - traction_target_slip) / 0.25
		_tc_cut = clampf(maxf(_tc_cut, excess), 0.0, 1.0)
	var factor := 1.0 - _tc_cut * traction_control

	# --- predictive part: never exceed the available grip ---------------- #
	if absf(ratio) > 0.01:
		# An open-ish differential can only push as hard as its *weaker* wheel,
		# so the capacity is set by the least loaded tyre, not the total. In a
		# corner the inside rear unloads to a third of its static weight, and
		# sizing torque off the sum is what let the car light up its tyres and
		# swap ends.
		var weakest := INF
		var driven := 0
		for w in driven_wheels:
			if w.grounded:
				weakest = minf(weakest, w.grip_limit_force())
				driven += 1
		if driven > 0 and is_finite(weakest):
			# The locking effect lets the loaded wheel take a share of the
			# slack; a fully open diff gets nothing extra.
			var capacity := weakest * driven * lerpf(1.0, 1.35, differential_lock)

			# Cornering uses up part of the friction circle, so less of it is
			# left for driving out. This is the term that actually tames
			# power-on oversteer instead of waiting for the slide to start.
			var lateral_use := 0.0
			for w in driven_wheels:
				lateral_use = maxf(lateral_use,
					absf(w.slip_angle) / deg_to_rad(maxf(w.peak_slip_angle_deg, 1.0)))
			var remaining := sqrt(maxf(0.0, 1.0 - minf(lateral_use, 1.0) ** 2))
			capacity *= lerpf(1.0, maxf(remaining, 0.25), traction_control)

			var allowed := capacity * traction_headroom * driven_wheels[0].tyre_radius \
				/ (absf(ratio) * drivetrain_efficiency)
			var demand := engine_torque_at(engine_rpm) \
				* (1.0 + (boost_multiplier - 1.0) * boost)
			if demand > allowed:
				factor = minf(factor, lerpf(1.0, allowed / demand, traction_control))

	return clampf(factor, 0.0, 1.0)


## Damps the yaw rate when the car rotates faster than the driver asked for.
##
## Modelled as a brake on the outer front wheel plus a small direct yaw moment,
## which is how a real ESC behaves. It only fights genuine oversteer: the
## deadband means normal cornering is untouched.
func _apply_stability_control(forward_speed: float) -> void:
	if stability_control <= 0.0 or absf(forward_speed) < 3.0:
		return

	# What the steering geometry says the yaw rate should be.
	var steer_angle := deg_to_rad(max_steer_deg) * steer_input \
		* lerpf(1.0, high_speed_steer_scale,
			clampf(speed_kmh / steer_speed_falloff, 0.0, 1.0))
	var target_yaw := forward_speed * tan(steer_angle) / maxf(_wheelbase, 0.1)
	# Never ask for more than the tyres could deliver anyway.
	var grip_limit := 1.4 * 9.81 / maxf(absf(forward_speed), 1.0)
	target_yaw = clampf(target_yaw, -grip_limit, grip_limit)

	var actual_yaw := angular_velocity.dot(global_basis.y)
	var error := actual_yaw - target_yaw
	if absf(error) < stability_deadband:
		return

	var correction := (absf(error) - stability_deadband) * signf(error)
	# Brake the wheel on the outside of the slide to straighten the car.
	var brake := clampf(absf(correction) * 2.2, 0.0, 1.0) * stability_control
	for w in _front:
		var outer := signf(w.position.x) != signf(correction)
		if outer:
			w.brake_torque = maxf(w.brake_torque, front_brake_torque * brake * 0.55)

	# A gentle direct moment as well, so it responds immediately.
	var moment := -correction * mass * 0.55 * stability_control
	apply_torque(global_basis.y * moment)


func _distribute_drive(axle_torque: float) -> void:
	for w in _wheels:
		w.drive_torque = 0.0
	if _rear.is_empty():
		return

	# Tell the driven wheels how much of the driveline they have to spin up with
	# them. Without this the wheels are ~30x too light in first gear and the
	# whole car judders instead of accelerating.
	var ratio := current_ratio()
	var reflected := engine_inertia * ratio * ratio * drivetrain_efficiency
	for w in _wheels:
		w.driveline_inertia = 0.0
	var driven_wheels := _driven()
	for w in driven_wheels:
		w.driveline_inertia = reflected * clutch / maxf(driven_wheels.size(), 1)

	# Four wheel drive splits the torque between the axles first.
	if all_wheel_drive and _front.size() == 2:
		_split_axle(_front, axle_torque * front_torque_split)
		_split_axle(_rear, axle_torque * (1.0 - front_torque_split))
		return

	_split_axle(_rear, axle_torque)


## Wheels that currently receive engine torque.
func _driven() -> Array[RayWheel]:
	if all_wheel_drive:
		return _wheels
	return _rear


## Shares an axle's torque between its two wheels through the differential.
func _split_axle(axle: Array[RayWheel], axle_torque: float) -> void:
	if axle.is_empty():
		return
	var left := axle[0]
	var right := axle[1] if axle.size() > 1 else axle[0]
	var half := axle_torque * 0.5

	var diff_spin := left.spin - right.spin
	# Torque biasing proportional to the speed difference across the diff.
	var bias := clampf(diff_spin * 0.06, -1.0, 1.0) * differential_lock * absf(half)
	left.drive_torque = half - bias
	right.drive_torque = half + bias

	# Lifted wheels cannot take torque, hand it to the other side.
	if not left.grounded and right.grounded:
		right.drive_torque = axle_torque * (0.5 + 0.5 * differential_lock)
		left.drive_torque = axle_torque * (0.5 - 0.5 * differential_lock)
	elif not right.grounded and left.grounded:
		left.drive_torque = axle_torque * (0.5 + 0.5 * differential_lock)
		right.drive_torque = axle_torque * (0.5 - 0.5 * differential_lock)


func _update_brakes(forward_speed: float) -> void:
	# Map the two keys onto throttle and brake for the gear we are in. This
	# rewrites `throttle`/`brake_input`, so it must only ever read the raw key
	# state - never its own previous output.
	var pedal := 0.0
	if gear < 0:
		# Reverse: S drives, W brakes.
		throttle = raw_backward
		pedal = raw_forward if forward_speed < -0.4 else 0.0
	else:
		throttle = raw_forward
		pedal = raw_backward
	brake_input = raw_backward
	for w in _wheels:
		var base := front_brake_torque if w.is_steering else rear_brake_torque
		w.brake_torque = base * pedal
		if not w.is_steering:
			w.brake_torque = maxf(w.brake_torque, handbrake_torque * handbrake_input)

	# Creep suppression: with no pedals and the car nearly stopped, apply just
	# enough brake to hold it, exactly like leaving an automatic in Drive.
	if raw_forward < 0.02 and raw_backward < 0.02 and absf(forward_speed) < 0.4:
		var hold := 900.0 * (1.0 - absf(forward_speed) / 0.4)
		for w in _wheels:
			w.brake_torque = maxf(w.brake_torque, hold)


func _apply_aerodynamics(vel: Vector3) -> void:
	var speed := vel.length()
	if speed < 0.5:
		return
	var q := 0.5 * air_density * speed * speed
	apply_central_force(-vel.normalized() * q * drag_area)

	# Downforce is applied at the axles so it also settles the car in pitch.
	var down := -global_basis.y
	var front_pos := global_basis * Vector3(0.0, 0.3, -_wheelbase * 0.5)
	var rear_pos := global_basis * Vector3(0.0, 0.3, _wheelbase * 0.5)
	apply_force(down * front_downforce * speed * speed, front_pos)
	apply_force(down * rear_downforce * speed * speed, rear_pos)


func _update_telemetry() -> void:
	var worst := 0.0
	for w in _wheels:
		var s := Vector2(w.slip_ratio / maxf(w.peak_slip_ratio, 0.01),
			w.slip_angle / deg_to_rad(maxf(w.peak_slip_angle_deg, 0.1))).length()
		worst = maxf(worst, s)
	wheel_slip = worst


## Collects the wheel nodes. Safe to call repeatedly and safe to call before
## this node's own _ready().
##
## Godot readies children before their parent, so TyreMarks, GroundParticles
## and ExhaustSmoke all ran their _ready() while _wheels was still empty. They
## built zero emitters, then indexed into those empty arrays on the first
## physics frame - "Out of bounds get index '0' (on base: 'Array')", in two
## places at once. Any child that needs the wheels calls this first.
func ensure_wheels() -> void:
	if not _wheels.is_empty():
		return
	var root := _wheel_root
	if root == null:
		root = get_node_or_null("Wheels") as Node3D
	if root == null:
		return
	for child in root.get_children():
		if child is RayWheel:
			_wheels.append(child)
			if child.is_steering:
				_front.append(child)
			else:
				_rear.append(child)


func get_wheels() -> Array[RayWheel]:
	ensure_wheels()
	return _wheels
