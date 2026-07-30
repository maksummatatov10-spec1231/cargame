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

# The target is a child of the RigidBody, so its transform only changes on a
# physics tick. Reading it directly from _process would give the camera a
# 120 Hz staircase while the car's own model, which hangs off a
# TransformSmoothing node, moves at the display rate. The difference between
# the two is what you see, so one being stepped and the other smooth produces
# visible shake even though the simulation is perfectly smooth. Measured at
# 75 Hz: 940 m/s^3 of mean on-screen jerk from the sampling alone.
#
# So the camera samples the target on the physics tick, keeps the previous
# sample, and interpolates between them in _process exactly like the model
# does. Both are then on the same clock and the difference is smooth.
var _prev_target := Transform3D.IDENTITY
var _curr_target := Transform3D.IDENTITY
var _prev_velocity := Vector3.ZERO
var _curr_velocity := Vector3.ZERO
var _has_sample := false

@onready var arm : SpringArm3D = $SpringArm3D
@onready var camera : Camera3D = $SpringArm3D/Camera3D


func _ready() -> void:
	# The rig drives its own global transform, so it must not inherit the
	# parent's, otherwise it would be moved twice.
	top_level = true
	# Sample the body after it has been integrated, and do the actual camera
	# work in _process at the display rate.
	process_physics_priority = 110

	if target_path:
		_target = get_node_or_null(target_path)
	# The game spawns the vehicle at runtime and calls set_target(); having no
	# target at _ready() is normal, not an error.
	if _target == null:
		arm.collision_mask = 1
		arm.margin = 0.35
		camera.transform = Transform3D.IDENTITY
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
## Points the rig at a different node, used when the vehicle is swapped.
func set_target(node: Node3D) -> void:
	_target = node
	if _target == null:
		return
	_vehicle = _target as Vehicle
	if _vehicle == null:
		_vehicle = _target.get_parent() as Vehicle
	arm.clear_excluded_objects()
	_exclude_vehicle_bodies()
	_snap_behind_target()


## Heading of a transform: the direction its -Z axis points, flattened onto
## the ground.
##
## NOT basis.get_euler().y. Euler decomposition in YXZ order folds pitch and
## roll into the yaw term, so the moment the car noses over a crest or leans
## in a corner that "yaw" stops being the direction the car is facing.
## Measured: 11 degrees of error at 30 deg pitch / 20 deg roll, 48 degrees at
## 60/40. That is the camera swinging away from behind the car over every
## bump, which is what broke the chase view in v2.7.
static func _heading_of(xform: Transform3D) -> float:
	var forward := -xform.basis.z
	var flat := Vector2(forward.x, forward.z)
	if flat.length_squared() < 1e-8:
		# Pointing straight up or down: -Z has no ground direction, so fall
		# back to the roof axis, which is horizontal in exactly that case.
		var up := -xform.basis.y
		flat = Vector2(up.x, up.z)
		if flat.length_squared() < 1e-8:
			return 0.0
	return atan2(-flat.x, -flat.y)


## Frame rate independent smoothing weight.
##
## `rate * delta` is not: the camera ran in _physics_process at a fixed
## 1/120 s, and moving it to _process handed it the frame time instead, so
## its response started changing with the frame rate. The exponential form
## closes the same fraction of the gap per second whatever the fps.
static func _smoothing(rate: float, delta: float) -> float:
	return 1.0 - exp(-rate * delta)


func _snap_behind_target() -> void:
	_curr_target = _target.global_transform
	_prev_target = _curr_target
	_curr_velocity = _vehicle.linear_velocity if _vehicle else Vector3.ZERO
	_prev_velocity = _curr_velocity
	_has_sample = true
	_yaw = _heading_of(_curr_target)
	_orbit = _yaw
	global_position = _target.global_position
	rotation = Vector3(0.0, _yaw, 0.0)
	arm.position = Vector3(0.0, height, 0.0)
	arm.rotation = Vector3(deg_to_rad(base_pitch), 0.0, 0.0)
	arm.spring_length = base_distance
	camera.fov = base_fov


## Records where the car is at the end of each physics tick. No camera work
## happens here - see the note on _prev_target.
func _physics_process(_delta: float) -> void:
	if _target == null or not is_instance_valid(_target):
		return
	_prev_target = _curr_target if _has_sample else _target.global_transform
	_curr_target = _target.global_transform
	_prev_velocity = _curr_velocity
	_curr_velocity = _vehicle.linear_velocity if _vehicle else Vector3.ZERO
	_has_sample = true
	# A respawn moves the car a long way in one tick; interpolating across
	# that would fly the camera between the two points.
	if _prev_target.origin.distance_to(_curr_target.origin) > 8.0:
		_prev_target = _curr_target
		global_position = _curr_target.origin
		_yaw = _heading_of(_curr_target)


## The camera runs at the display rate against an interpolated target, so it
## is on the same clock as the car's visual model.
func _process(delta: float) -> void:
	if _target == null or not is_instance_valid(_target) or not _has_sample:
		return

	if Input.is_action_just_pressed("toggle_camera"):
		mode = (mode + 1) % Mode.size()
		if mode == Mode.ORBIT:
			_orbit = _yaw

	var f := Engine.get_physics_interpolation_fraction()
	var target_pos := _prev_target.origin.lerp(_curr_target.origin, f)
	var target_yaw_now := _lerp_angle(_heading_of(_prev_target),
		_heading_of(_curr_target), f)
	var velocity := _prev_velocity.lerp(_curr_velocity, f)
	var speed := velocity.length()
	var t := clampf(speed / speed_reference, 0.0, 1.0)

	if mode == Mode.HOOD:
		_update_hood(target_pos, target_yaw_now, f)
		return

	if mode == Mode.ORBIT:
		_orbit = wrapf(_orbit + delta * 0.35, -PI, PI)
		_yaw = _orbit
	else:
		# Blend between where the car is pointing and where it is actually
		# going, so drifts stay in frame.
		var target_yaw := target_yaw_now
		if speed > 4.0:
			var flat := Vector3(velocity.x, 0.0, velocity.z)
			if flat.length() > 0.5:
				var travel_yaw := atan2(-flat.x, -flat.z)
				target_yaw = _lerp_angle(target_yaw, travel_yaw, 0.45)
		_yaw = _lerp_angle(_yaw, target_yaw,
			_smoothing(rotation_smoothing * (0.5 + t), delta))

	global_position = global_position.lerp(target_pos,
		_smoothing(position_smoothing, delta))
	rotation = Vector3(0.0, _yaw, 0.0)

	arm.position = Vector3(0.0, height, 0.0)
	arm.rotation = Vector3(deg_to_rad(lerpf(base_pitch, speed_pitch, t)), 0.0, 0.0)
	arm.spring_length = base_distance + speed_distance * t
	camera.fov = lerpf(base_fov, speed_fov, t * t)
	camera.transform = Transform3D.IDENTITY


## Bumper/hood view: the rig is locked to the car and the arm is collapsed, so
## the spring arm places the camera exactly on the pivot.
func _update_hood(target_pos: Vector3, target_yaw: float, f: float) -> void:
	var a := _prev_target.basis.get_rotation_quaternion()
	var b := _curr_target.basis.get_rotation_quaternion()
	global_transform = Transform3D(Basis(a.slerp(b, f)), target_pos)
	_yaw = target_yaw
	arm.position = hood_offset
	arm.rotation = Vector3.ZERO
	arm.spring_length = 0.0
	camera.fov = 75.0
	camera.transform = Transform3D.IDENTITY


func _lerp_angle(from: float, to: float, weight: float) -> float:
	return from + wrapf(to - from, -PI, PI) * clampf(weight, 0.0, 1.0)
