class_name ChaseCamera
extends Node3D

enum Mode { CHASE, HOOD, ORBIT }

## Third person chase camera.
##
## The rig deliberately does *not* rigidly copy the car's rotation: the pivot
## chases the car's velocity direction with a spring, which is what makes a
## chase camera readable during slides instead of nauseating. A SpringArm3D
## keeps it out of walls and terrain.

@export var target_path : NodePath
## Distance behind the car at a standstill.
@export var base_distance := 6.2
## Extra distance added at [member speed_reference].
@export var speed_distance := 2.1
@export var height := 2.15
@export var speed_reference := 55.0
## How quickly the rig follows the car's position.
@export var position_smoothing := 14.0
## How quickly the rig yaws to line up behind the car.
@export var rotation_smoothing := 5.0
## Field of view at rest and at [member speed_reference].
@export var base_fov := 68.0
@export var speed_fov := 84.0
## Extra pitch applied when the camera is looking down at the car.
@export var look_ahead := 3.4

var mode : int = Mode.CHASE

var _target : Node3D
var _vehicle : Vehicle
var _yaw := 0.0
var _orbit := 0.0

@onready var arm : SpringArm3D = $SpringArm3D
@onready var camera : Camera3D = $SpringArm3D/Camera3D


func _ready() -> void:
	if target_path:
		_target = get_node_or_null(target_path)
	if _target:
		_vehicle = _target as Vehicle
		if _vehicle == null:
			_vehicle = _target.get_parent() as Vehicle
		global_position = _target.global_position
		_yaw = _target.global_rotation.y
	arm.spring_length = base_distance
	arm.collision_mask = 1
	arm.margin = 0.35
	top_level = true


func _physics_process(delta: float) -> void:
	if _target == null:
		return

	if Input.is_action_just_pressed("toggle_camera"):
		mode = (mode + 1) % Mode.size()

	var speed := 0.0
	var velocity := Vector3.ZERO
	if _vehicle:
		velocity = _vehicle.linear_velocity
		speed = velocity.length()

	var t := clampf(speed / speed_reference, 0.0, 1.0)
	var car_yaw := _target.global_rotation.y

	match mode:
		Mode.HOOD:
			_apply_hood()
			return
		Mode.ORBIT:
			_orbit += delta * 0.35
			_yaw = _orbit
		_:
			# Blend between the car's heading and the direction it is actually
			# travelling, so drifts stay in frame.
			var target_yaw := car_yaw
			if speed > 4.0:
				var flat := Vector3(velocity.x, 0.0, velocity.z)
				if flat.length() > 0.5:
					var travel_yaw := atan2(-flat.x, -flat.z)
					target_yaw = _lerp_angle(car_yaw, travel_yaw, 0.45)
			_yaw = _lerp_angle(_yaw, target_yaw,
				clampf(rotation_smoothing * delta * (0.5 + t), 0.0, 1.0))

	var focus := _target.global_position
	global_position = global_position.lerp(focus,
		clampf(position_smoothing * delta, 0.0, 1.0))
	rotation.y = _yaw

	arm.spring_length = base_distance + speed_distance * t
	arm.position.y = height
	arm.rotation.x = deg_to_rad(-9.0 - 3.0 * t)
	camera.fov = lerpf(base_fov, speed_fov, t * t)
	camera.position = Vector3.ZERO
	camera.rotation = Vector3.ZERO
	camera.look_at_from_position(
		camera.global_position,
		focus + Vector3.UP * 0.55 + _target.global_basis.z * -look_ahead * t,
		Vector3.UP)


func _apply_hood() -> void:
	global_transform = _target.global_transform
	arm.spring_length = 0.0
	arm.position = Vector3(0.0, 0.32, -0.35)
	arm.rotation = Vector3.ZERO
	camera.fov = 75.0
	camera.rotation = Vector3.ZERO


func _lerp_angle(from: float, to: float, weight: float) -> float:
	return from + wrapf(to - from, -PI, PI) * clampf(weight, 0.0, 1.0)
