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
## Road speed in m/s below which the clutch starts to slip, so the engine can
## idle without dragging the car along. Without this a car left in gear would
## creep forever on the idle torque.
@export var clutch_engage_speed := 2.2
## 0 = open diff, 1 = fully locked. A limited slip diff sits in between.
@export_range(0.0, 1.0) var differential_lock := 0.45

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
var brake_input := 0.0
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
var _engine_speed := 0.0            ## rad/s
var _spawn_transform := Transform3D.IDENTITY
var _wheelbase := 2.63
var _track := 1.49

@onready var _wheel_root : Node3D = $Wheels
@onready var _model : CarModel = $Model


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

	for child in _wheel_root.get_children():
		if child is RayWheel:
			_wheels.append(child)
			if child.is_steering:
				_front.append(child)
			else:
				_rear.append(child)

	if _front.size() == 2 and _rear.size() == 2:
		_wheelbase = absf(_front[0].position.z - _rear[0].position.z)
		_track = absf(_front[0].position.x - _front[1].position.x)

	_engine_speed = idle_rpm * TAU / 60.0
	_spawn_transform = global_transform
	_apply_inertia_tensor()


## A box inertia tensor built from the real dimensions of the car keeps the
## rotational behaviour believable instead of relying on the auto-computed one
## from the convex hulls.
func _apply_inertia_tensor() -> void:
	var w := 1.80   # width
	var h := 1.42   # height
	var l := 4.38   # length
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
	if _model:
		_model.set_steering(steer_input, delta)


func _gather_input(delta: float) -> void:
	throttle = Input.get_action_strength("drive_forward")
	brake_input = Input.get_action_strength("drive_backward")
	handbrake_input = Input.get_action_strength("handbrake")

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


func reset_to_spawn() -> void:
	global_transform = _spawn_transform
	linear_velocity = Vector3.ZERO
	angular_velocity = Vector3.ZERO
	for w in _wheels:
		w.spin = 0.0
	gear = 1
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
	_update_brakes(forward_speed)
	_update_drivetrain(delta, forward_speed)
	_update_tyres(delta)

	for w in _wheels:
		w.update_spin(delta)
		w.apply_forces(self)

	_apply_aerodynamics(vel)
	_update_telemetry()


func _update_steering() -> void:
	# Speed sensitive steering ratio.
	var scale := lerpf(1.0, high_speed_steer_scale,
		clampf(speed_kmh / steer_speed_falloff, 0.0, 1.0))
	var angle := deg_to_rad(max_steer_deg) * steer_input * scale

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
		var engaged := clampf(absf(forward_speed) / maxf(clutch_engage_speed, 0.01), 0.0, 1.0)
		clutch = maxf(engaged, throttle)

	# Reverse is selected by holding brake while basically stopped.
	if gear > 0 and brake_input > 0.5 and forward_speed < 0.6 and throttle < 0.1:
		gear = -1
	elif gear < 0 and throttle > 0.5 and forward_speed > -0.6 and brake_input < 0.1:
		gear = 1

	var ratio := current_ratio()

	# Average driven wheel speed feeds back into the engine through the clutch.
	var driven_spin := 0.0
	for w in _rear:
		driven_spin += w.spin
	driven_spin /= maxf(_rear.size(), 1)

	var idle_speed := idle_rpm * TAU / 60.0
	if absf(ratio) > 0.01 and clutch > 0.99:
		# Clutch locked: engine speed follows the wheels.
		_engine_speed = absf(driven_spin * ratio)
	else:
		# Free revving, blended towards the wheel speed as the clutch bites.
		var free_torque := engine_torque_at(_rpm()) * throttle \
			- engine_braking * maxf(_engine_speed - idle_speed, 0.0)
		_engine_speed += free_torque / engine_inertia * delta
		if clutch > 0.0 and absf(ratio) > 0.01:
			_engine_speed = lerpf(_engine_speed, absf(driven_spin * ratio), clutch)

	_engine_speed = clampf(_engine_speed, idle_rpm * TAU / 60.0, (redline_rpm + 400.0) * TAU / 60.0)
	engine_rpm = _rpm()

	_auto_shift()

	var crank_torque := engine_torque_at(engine_rpm) * throttle
	# Engine braking only exists above idle; at idle the engine is producing just
	# enough torque to keep itself turning, not to drag the car backwards.
	crank_torque -= engine_braking * maxf(_engine_speed - idle_speed, 0.0) \
		* (1.0 - throttle * 0.85)
	var axle_torque := crank_torque * ratio * drivetrain_efficiency * clutch

	_distribute_drive(axle_torque)


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
func _distribute_drive(axle_torque: float) -> void:
	for w in _wheels:
		w.drive_torque = 0.0
	if _rear.is_empty():
		return

	var left := _rear[0]
	var right := _rear[1] if _rear.size() > 1 else _rear[0]
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
	var pedal := brake_input
	# In reverse the "S" key becomes the throttle, and "W" becomes the brake.
	if gear < 0:
		pedal = throttle if forward_speed < -0.4 else 0.0
		throttle = brake_input
	for w in _wheels:
		var base := front_brake_torque if w.is_steering else rear_brake_torque
		w.brake_torque = base * pedal
		if not w.is_steering:
			w.brake_torque = maxf(w.brake_torque, handbrake_torque * handbrake_input)

	# Creep suppression: with no pedals and the car nearly stopped, apply just
	# enough brake to hold it, exactly like leaving an automatic in Drive.
	if throttle < 0.02 and pedal < 0.02 and absf(forward_speed) < 0.4:
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


func get_wheels() -> Array[RayWheel]:
	return _wheels
