class_name ChaseCamera
extends Node3D

## Third person chase camera.
##
## The rig deliberately does *not* rigidly copy the car's rotation: the pivot
## chases the car's velocity direction with a spring, which is what makes a
## chase camera readable during slides instead of nauseating. A [SpringArm3D]
## keeps it out of walls and terrain.
##
## Layout, and why it matters:
##
##   ChaseCamera   (top level, follows the car's position, yaws behind it)
##    └ SpringArm3D  (raised to `height`, pitched down, pushes the camera back)
##       └ Camera3D  (identity transform - the arm places it)
##
## The camera is never repositioned by hand. A [SpringArm3D] owns the transform
## of its child and moves it along the arm's -Z, shortening the distance when
## something is in the way. Writing to the camera's own position or rotation
## fights the arm: the camera ends up sitting on the pivot, inside the car,
## which renders the inside of the bodywork over the whole screen. Because the
## arm already points at the pivot, the camera looking straight down its own -Z
## is exactly "looking at the car", so no look_at() call is needed either.

enum Mode { CHASE, HOOD, ORBIT }

@export var target_path : NodePath
## Distance behind the car at a standstill.
@export var base_distance := 6.2
## Extra distance added at [member speed_reference].
@export var speed_distance := 2.1
## Height of the pivot above the car's origin.
@export var height := 1.55
@export var speed_reference := 55.0
## How quickly the rig follows the car's position.
@export var position_smoothing := 14.0
## How quickly the rig yaws to line up behind the car.
@export var rotation_smoothing := 5.0
## Field of view at rest and at [member speed_reference].
@export var base_fov := 68.0
@export var speed_fov := 84.0
## Downward pitch of the arm in degrees, at rest and at speed.
@export var base_pitch := -9.0
@export var speed_pitch := -12.0
## Where the driver's eyes sit for the hood camera.
@export var hood_offset := Vector3(0.0, 1.12, 0.15)

var mode : int = Mode.CHASE

var _target : Node3D
var _vehicle : Vehicle
var _yaw := 0.0
var _orbit := 0.0

@onready var arm : SpringArm3D = $SpringArm3D
@onready var camera : Camera3D = $SpringArm3D/Camera3D


func _ready() -> void:
	# The rig drives its own global transform, so it must not inherit the
	# parent's, otherwise it would be moved twice.
	top_level = true

	if target_path:
		_target = get_node_or_null(target_path)
	if _target == null:
		push_warning("ChaseCamera: target_path does not point at a node")
		return

	_vehicle = _target as Vehicle
	if _vehicle == null:
		_vehicle = _target.get_parent() as Vehicle

	arm.collision_mask = 1
	arm.margin = 0.35
	# The camera is positioned exclusively by the spring arm.
	camera.transform = Transform3D.IDENTITY

	# The arm starts inside the car and sweeps backwards, so without this it
	# immediately hits the car's own collision hulls, collapses to zero length
	# and parks the camera inside the bodywork - which renders as a full screen
	# of car interior.
	_exclude_vehicle_bodies()
	_snap_behind_target()


## Stops the spring arm from colliding with the car it is following.
func _exclude_vehicle_bodies() -> void:
	var root : Node = _vehicle if _vehicle else _target
	if root == null:
		return
	for node in _collect_bodies(root):
		arm.add_excluded_object(node.get_rid())


func _collect_bodies(node: Node) -> Array[CollisionObject3D]:
	var found : Array[CollisionObject3D] = []
	if node is CollisionObject3D:
		found.append(node)
	for child in node.get_children():
		found.append_array(_collect_bodies(child))
	return found


## Places the rig behind the car immediately, so the first frame is already
## framed correctly instead of flying in from the world origin.
func _snap_behind_target() -> void:
	_yaw = _target.global_rotation.y
	_orbit = _yaw
	global_position = _target.global_position
	rotation = Vector3(0.0, _yaw, 0.0)
	arm.position = Vector3(0.0, height, 0.0)
	arm.rotation = Vector3(deg_to_rad(base_pitch), 0.0, 0.0)
	arm.spring_length = base_distance
	camera.fov = base_fov


func _physics_process(delta: float) -> void:
	if _target == null:
		return

	if Input.is_action_just_pressed("toggle_camera"):
		mode = (mode + 1) % Mode.size()
		if mode == Mode.ORBIT:
			_orbit = _yaw

	var velocity := _vehicle.linear_velocity if _vehicle else Vector3.ZERO
	var speed := velocity.length()
	var t := clampf(speed / speed_reference, 0.0, 1.0)

	if mode == Mode.HOOD:
		_update_hood()
		return

	if mode == Mode.ORBIT:
		_orbit = wrapf(_orbit + delta * 0.35, -PI, PI)
		_yaw = _orbit
	else:
		# Blend between where the car is pointing and where it is actually
		# going, so drifts stay in frame.
		var target_yaw := _target.global_rotation.y
		if speed > 4.0:
			var flat := Vector3(velocity.x, 0.0, velocity.z)
			if flat.length() > 0.5:
				var travel_yaw := atan2(-flat.x, -flat.z)
				target_yaw = _lerp_angle(target_yaw, travel_yaw, 0.45)
		_yaw = _lerp_angle(_yaw, target_yaw,
			clampf(rotation_smoothing * delta * (0.5 + t), 0.0, 1.0))

	global_position = global_position.lerp(_target.global_position,
		clampf(position_smoothing * delta, 0.0, 1.0))
	rotation = Vector3(0.0, _yaw, 0.0)

	arm.position = Vector3(0.0, height, 0.0)
	arm.rotation = Vector3(deg_to_rad(lerpf(base_pitch, speed_pitch, t)), 0.0, 0.0)
	arm.spring_length = base_distance + speed_distance * t
	camera.fov = lerpf(base_fov, speed_fov, t * t)
	camera.transform = Transform3D.IDENTITY


## Bumper/hood view: the rig is locked to the car and the arm is collapsed, so
## the spring arm places the camera exactly on the pivot.
func _update_hood() -> void:
	global_transform = _target.global_transform
	arm.position = hood_offset
	arm.rotation = Vector3.ZERO
	arm.spring_length = 0.0
	camera.fov = 75.0
	camera.transform = Transform3D.IDENTITY


func _lerp_angle(from: float, to: float, weight: float) -> float:
	return from + wrapf(to - from, -PI, PI) * clampf(weight, 0.0, 1.0)
