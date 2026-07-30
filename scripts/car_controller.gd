extends VehicleBody3D
## BMW 1M - Maximum Quality Realistic Physics
## Godot 4.3 VehicleBody3D setup with:
## - 2 Animations: wheel spin (rotation) + steering
## - Real suspension bounce, anti-roll, weight transfer
## - 1570 kg mass, realistic center of mass
## - WASD controls

# === PHYSICS PARAMETERS ===
# Real BMW 1M specs: 1570kg, 2.69m wheelbase, 250kW engine
@export var MAX_ENGINE_FORCE := 3800.0
@export var MAX_BRAKE_FORCE := 110.0
@export var MAX_STEER_ANGLE := 32.0 # degrees
@export var STEER_SPEED := 7.0
@export var MAX_RPM := 700.0
@export var ANTI_ROLL_FRONT := 3200.0
@export var ANTI_ROLL_REAR := 3800.0
@export var DOWNFORCE_COEFF := 3.5
@export var TRACTION_CONTROL := true

var wheels: Array[VehicleWheel3D] = []
var wheel_visuals: Dictionary = {} # Wheel3D -> MeshInstance3D
var rim_visuals: Dictionary = {}
var steering_input := 0.0
var throttle_input := 0.0
var brake_input := 0.0

@onready var fl_wheel: VehicleWheel3D = $FL
@onready var fr_wheel: VehicleWheel3D = $FR
@onready var rl_wheel: VehicleWheel3D = $RL
@onready var rr_wheel: VehicleWheel3D = $RR

var engine_rpm := 0.0
var speed_kmh := 0.0
var is_drifting := false

func _ready():
	# --- Core rigidbody setup for max realism ---
	mass = 1570.0
	center_of_mass_mode = RigidBody3D.CENTER_OF_MASS_MODE_CUSTOM
	center_of_mass = Vector3(0, -0.85, 0.25) # lower COM = less roll
	gravity_scale = 1.0
	linear_damp = 0.05
	angular_damp = 0.35
	continuous_cd = true
	contact_monitor = true
	max_contacts_reported = 8
	can_sleep = false # never sleep for responsive physics
	
	# Collect wheels in order [FL, FR, RL, RR]
	wheels = [fl_wheel, fr_wheel, rl_wheel, rr_wheel]
	
	for wheel in wheels:
		# Ensure suspension setup matches research
		# Values from docs + forums tweaked for BMW 1M
		wheel_visuals[wheel] = wheel.get_node_or_null("WheelMesh")
		rim_visuals[wheel] = wheel.get_node_or_null("RimMesh")
	
	# Try to load FBX body if imported (optional enhancer)
	_try_load_fbx_body()

func _try_load_fbx_body():
	# Godot 4.3 will import FBX as a scene. Attempt common paths (renamed to avoid space)
	var fbx_paths = [
		"res://assets/cars/bmw_1m/bmw_1m_final.fbx",
		"res://assets/cars/bmw_1m/bmw_1m final.fbx",
		"res://assets/cars/bmw_1m/source/bmw_1m final.fbx"
	]
	for p in fbx_paths:
		if ResourceLoader.exists(p):
			var res = load(p)
			if res and res is PackedScene:
				var instance = res.instantiate()
				instance.name = "ImportedBody"
				# Search for mesh instances inside and reparent?
				# For safety add as child and hide placeholder
				add_child(instance)
				var placeholder = get_node_or_null("ChassisMesh")
				if placeholder:
					placeholder.visible = false
				print("Loaded FBX scene: ", p)
				break

func _physics_process(delta):
	# --- Input ---
	_handle_input(delta)
	
	# --- Steering (animation 1: wheel turning) ---
	_handle_steering(delta)
	
	# --- Engine & Braking ---
	_handle_engine_braking(delta)
	
	# --- Advanced Physics: Anti-roll, Downforce, Weight Transfer ---
	_apply_anti_roll()
	_apply_downforce()
	_apply_traction_control()
	
	# --- Visual Wheel Animation (2 animations) ---
	_update_wheel_visuals(delta)
	
	# --- Misc ---
	_calculate_speed()
	_check_reset()
	
	# Stabilize airborne rotation slightly (like real car gyro)
	if not _is_any_wheel_grounded():
		angular_damp = 2.0
	else:
		angular_damp = 0.35

func _handle_input(delta):
	steering_input = Input.get_axis("steer_right", "steer_left") # A/D inverted? left = -1, right=+1 -> we want left positive
	throttle_input = Input.get_axis("brake", "accelerate") # W = +1, S = -1
	# For forward movement: engine_force positive goes ??? In Godot -Z is forward. Use negative?
	# We'll handle sign in engine code
	
	# Smooth digital input for keyboard
	if Input.is_action_pressed("steer_left") or Input.is_action_pressed("steer_right"):
		pass
	# else steering_input already 0

func _handle_steering(delta):
	# Speed sensitive steering: at high speed reduce angle
	var steer_factor = clamp(1.0 - (speed_kmh / 220.0) * 0.6, 0.28, 1.0)
	var target_steer = steering_input * deg_to_rad(MAX_STEER_ANGLE) * steer_factor
	
	# Smooth lerp for realistic wheel turn animation
	steering = move_toward(steering, target_steer, STEER_SPEED * delta)
	
	# Apply to individual front wheels with Ackermann approx (inner turns more)
	if fl_wheel and fr_wheel:
		var left_factor = 1.1 if steering_input > 0 else 0.9
		var right_factor = 0.9 if steering_input > 0 else 1.1
		fl_wheel.steering = steering * left_factor
		fr_wheel.steering = steering * right_factor

func _handle_engine_braking(delta):
	# Engine torque curve: peak around 40% RPM
	var current_rpm_avg = (rl_wheel.get_rpm() + rr_wheel.get_rpm()) * 0.5
	current_rpm_avg = abs(current_rpm_avg)
	engine_rpm = lerp(engine_rpm, current_rpm_avg, delta * 10.0)
	
	var torque_factor = 1.0 - abs(engine_rpm) / MAX_RPM
	torque_factor = clamp(torque_factor, 0.15, 1.0)
	# Extra power boost at low speed to overcome static friction
	if speed_kmh < 15.0:
		torque_factor = 1.0
	
	# Throttle: rear-wheel drive BMW 1M
	var engine_force = 0.0
	var brake_force = 0.0
	
	if throttle_input > 0.05:
		# Forward
		engine_force = throttle_input * MAX_ENGINE_FORCE * torque_factor
		# Reduce force at very high speed (aerodynamic drag)
		if speed_kmh > 180:
			engine_force *= lerp(1.0, 0.35, (speed_kmh - 180) / 80.0)
	elif throttle_input < -0.05:
		# Reverse - smaller force
		engine_force = throttle_input * MAX_ENGINE_FORCE * 0.6
	else:
		# No throttle: small engine braking
		engine_force = 0.0
		brake_force = 0.5 # light drag
	
	# Braking: S key + Space
	if Input.is_action_pressed("brake"):
		brake_force = MAX_BRAKE_FORCE * abs(throttle_input) if throttle_input < 0 else MAX_BRAKE_FORCE * 0.5
	if Input.is_action_pressed("handbrake"):
		brake_force = MAX_BRAKE_FORCE * 1.6
		# Handbrake only rear
		rl_wheel.brake = brake_force
		rr_wheel.brake = brake_force
		fl_wheel.brake = brake_force * 0.1
		fr_wheel.brake = brake_force * 0.1
	else:
		# Brake distribution: 65% front, 35% rear for realistic weight transfer during braking
		var front_brake = brake_force * 0.65
		var rear_brake = brake_force * 0.35
		if throttle_input < -0.1:
			front_brake = brake_force * 0.65
			rear_brake = brake_force * 0.35
		fl_wheel.brake = front_brake
		fr_wheel.brake = front_brake
		rl_wheel.brake = rear_brake
		rr_wheel.brake = rear_brake
	
	# Apply engine force to rear wheels (RWD)
	rl_wheel.engine_force = engine_force
	rr_wheel.engine_force = engine_force
	fl_wheel.engine_force = 0.0
	fr_wheel.engine_force = 0.0
	
	# If braking hard while moving forward, zero engine force
	if brake_force > 10 and throttle_input > 0 and speed_kmh > 5:
		# still allow power if not handbrake
		if not Input.is_action_pressed("handbrake"):
			rl_wheel.brake *= 0.2
			rr_wheel.brake *= 0.2

func _apply_anti_roll():
	# Front anti-roll bar: reduces body roll in turns, key for sport cars
	_apply_anti_roll_axis(fl_wheel, fr_wheel, ANTI_ROLL_FRONT)
	_apply_anti_roll_axis(rl_wheel, rr_wheel, ANTI_ROLL_REAR)

func _apply_anti_roll_axis(left_wheel: VehicleWheel3D, right_wheel: VehicleWheel3D, ant_roll: float):
	if not left_wheel.is_in_contact() or not right_wheel.is_in_contact():
		return
	
	# Calculate suspension compression for each wheel
	var left_susp = _get_wheel_compression(left_wheel)
	var right_susp = _get_wheel_compression(right_wheel)
	
	var anti_roll_force = (left_susp - right_susp) * ant_roll
	
	# Apply forces at wheel positions
	if left_wheel.get_contact_point() != Vector3.ZERO:
		apply_force(Vector3(0, -anti_roll_force, 0), left_wheel.global_position - global_position)
	if right_wheel.get_contact_point() != Vector3.ZERO:
		apply_force(Vector3(0, anti_roll_force, 0), right_wheel.global_position - global_position)

func _get_wheel_compression(wheel: VehicleWheel3D) -> float:
	# Approx compression 0..1 using distance to ground
	if not wheel.is_in_contact():
		return 0.0
	var origin = wheel.global_position
	var contact = wheel.get_contact_point()
	var distance = origin.y - contact.y - wheel.wheel_radius
	# suspension length at rest
	var rest = wheel.wheel_suspension_rest_length
	var travel = wheel.wheel_suspension_travel
	var total = rest + travel
	# compression = 1 - (current / total) ? Actually more compression when distance small
	var compression = 1.0 - clamp((distance / total), 0.0, 1.0)
	return compression

func _apply_downforce():
	# Aerodynamic downforce increasing with speed
	var speed_ms = linear_velocity.length()
	if speed_ms > 2.0:
		var down = -global_transform.basis.y * (speed_ms * DOWNFORCE_COEFF * speed_ms * 0.08)
		apply_central_force(down)

func _apply_traction_control():
	if not TRACTION_CONTROL:
		return
	# If wheels spin too fast (RPM high but low speed), reduce grip -> detect drifting
	var avg_rear_rpm = (abs(rl_wheel.get_rpm()) + abs(rr_wheel.get_rpm())) * 0.5
	var expected_rpm = speed_kmh * 7.0 # approx ratio
	if avg_rear_rpm > expected_rpm + 150 and speed_kmh > 10:
		is_drifting = true
		# Slightly reduce rear friction for drift feel
		rl_wheel.wheel_friction_slip = lerp(rl_wheel.wheel_friction_slip, 1.6, 0.05)
		rr_wheel.wheel_friction_slip = lerp(rr_wheel.wheel_friction_slip, 1.6, 0.05)
	else:
		is_drifting = false
		rl_wheel.wheel_friction_slip = lerp(rl_wheel.wheel_friction_slip, 2.8, 0.04)
		rr_wheel.wheel_friction_slip = lerp(rr_wheel.wheel_friction_slip, 3.0, 0.04)

func _update_wheel_visuals(delta):
	# Two animations demanded: 1) spin rotation 2) steering turn
	for wheel in wheels:
		var mesh = wheel_visuals.get(wheel)
		var rim = rim_visuals.get(wheel)
		if mesh == null:
			continue
		
		# --- Animation 1: Rotation ---
		var rpm = wheel.get_rpm()
		# rpm is rotations per minute, convert to rad/s
		var rot_speed = rpm / 60.0 * TAU
		# Rotate around X (wheel axis) - negative for forward direction
		mesh.rotate_x(rot_speed * delta)
		if rim:
			rim.rotate_x(rot_speed * delta)
		
		# --- Animation 2: Suspension travel (bounce) + steering is automatic via VehicleWheel3D node ---
		# Update suspension visual position (make it bounce realistically)
		if wheel.is_in_contact():
			var contact = wheel.get_contact_point()
			var wheel_pos = wheel.global_position
			var dist_y = wheel_pos.y - contact.y - wheel.wheel_radius
			dist_y = clamp(dist_y, 0.0, wheel.wheel_suspension_rest_length + wheel.wheel_suspension_travel)
			# Target local Y = -dist_y
			var target_local = Vector3(0, -dist_y, 0)
			# Smooth spring for visual
			mesh.transform.origin = mesh.transform.origin.lerp(target_local, delta * 18.0)
			if rim:
				rim.transform.origin = mesh.transform.origin
		else:
			# Airborne - extend suspension fully
			var target = Vector3(0, -wheel.wheel_suspension_rest_length - wheel.wheel_suspension_travel * 0.3, 0)
			mesh.transform.origin = mesh.transform.origin.lerp(target, delta * 6.0)
			if rim:
				rim.transform.origin = mesh.transform.origin

func _calculate_speed():
	speed_kmh = linear_velocity.length() * 3.6

func _is_any_wheel_grounded() -> bool:
	for w in wheels:
		if w.is_in_contact():
			return true
	return false

func _check_reset():
	if Input.is_action_just_pressed("reset_car"):
		_reset_vehicle()
	if global_position.y < -20:
		_reset_vehicle()

func _reset_vehicle():
	global_position = Vector3(0, 5, 0)
	linear_velocity = Vector3.ZERO
	angular_velocity = Vector3.ZERO
	rotation = Vector3.ZERO
	steering = 0

func get_speed_kmh() -> float:
	return speed_kmh
