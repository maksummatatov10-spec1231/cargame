extends SpringArm3D
## Third-person follow camera behind BMW
## Ultra-smooth, collision-aware, high quality feel

@export var target: Node3D
@export var follow_speed := 8.0
@export var look_speed := 6.0
@export var mouse_sensitivity := 0.003
@export var min_pitch := -25.0
@export var max_pitch := 15.0
@export var base_distance := 6.0
@export var height_offset := 1.4

@onready var camera: Camera3D = $Camera3D

var yaw := 0.0
var pitch := -12.0
var current_distance := 6.0

func _ready():
	# Auto-find car if not assigned
	if target == null:
		var p = get_parent()
		while p != null:
			if p is VehicleBody3D:
				target = p
				break
			p = p.get_parent()
		if target == null and get_parent() and get_parent().get_parent() is VehicleBody3D:
			target = get_parent().get_parent()
	
	spring_length = base_distance
	if target:
		add_excluded_object(target)
		for child in target.get_children():
			if child is VehicleWheel3D:
				add_excluded_object(child)
			if child is CollisionShape3D:
				add_excluded_object(child)
	add_excluded_object(self)
	# Ensure car itself also excluded from spring cast
	if get_parent():
		add_excluded_object(get_parent())
	
	# High quality camera
	if camera:
		camera.current = true
		camera.fov = 72.0
	# Capture mouse optional - we use auto follow by default
	# Input.set_mouse_mode(Input.MOUSE_MODE_CAPTURED)
	
	# Spring properties for smooth collisions
	collision_mask = 1
	margin = 0.15

func _physics_process(delta):
	if not target:
		return
	
	var speed = 0.0
	if target.has_method("get_speed_kmh"):
		speed = target.get_speed_kmh()
	elif target is VehicleBody3D:
		speed = target.linear_velocity.length() * 3.6
	
	# Dynamic distance based on speed - further at high speed
	var target_dist = base_distance + clamp(speed / 120.0 * 2.5, 0.0, 3.0)
	current_distance = lerp(current_distance, target_dist, delta * 2.0)
	spring_length = current_distance
	
	# Dynamic FOV
	if camera:
		var target_fov = 72.0 + clamp(speed / 180.0 * 18.0, 0, 20)
		camera.fov = lerp(camera.fov, target_fov, delta * 2.5)
	
	# Follow position: lerp to car with prediction
	var car_pos = target.global_position
	var car_vel = Vector3.ZERO
	if target is RigidBody3D:
		car_vel = target.linear_velocity
	
	# Predict slightly ahead in direction of velocity
	var predicted = car_pos + car_vel * 0.12
	predicted.y += height_offset
	
	global_position = global_position.lerp(predicted, delta * follow_speed)
	
	# Rotation: follow car's yaw but smooth
	var car_yaw = target.global_rotation.y
	# Yaw override if user moves mouse - optional
	# For now just follow car yaw + stored offset
	var target_yaw = car_yaw + yaw
	var target_pitch = pitch
	
	# Interpolate rotation
	rotation.y = lerp_angle(rotation.y, target_yaw, delta * look_speed)
	rotation.x = lerp_angle(rotation.x, deg_to_rad(target_pitch), delta * look_speed)
	
	# Make camera look slightly down at car
	if camera:
		camera.look_at(car_pos + Vector3(0, 0.8, 0), Vector3.UP)

func _unhandled_input(event):
	if event is InputEventMouseMotion and Input.is_mouse_button_pressed(MOUSE_BUTTON_RIGHT):
		yaw -= event.relative.x * mouse_sensitivity
		pitch -= event.relative.y * mouse_sensitivity * 50.0
		pitch = clamp(pitch, min_pitch, max_pitch)
