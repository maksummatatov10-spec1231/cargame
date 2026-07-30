class_name TransformSmoothing
extends Node3D

## Renders a physics body's transform smoothly between physics ticks.
##
## Godot 4.3 only implements physics interpolation for 2D. Checked against the
## engine source: `scene/3d/node_3d.cpp` contains no interpolation code at all,
## and `SceneTree::set_physics_interpolation_enabled` only forwards to the
## canvas renderer. 3D interpolation arrived in 4.4. So enabling the project
## setting would have done nothing here, and the smoothing has to be explicit.
##
## Physics runs at a fixed 120 Hz while the display runs at whatever it runs at
## — 75 Hz in this case. Those do not divide evenly, so some frames show a
## transform one tick old and others two, which reads as a fine judder even
## though the simulation is perfectly smooth.
##
## The fix is standard: record the transform at each physics tick, then in
## `_process` interpolate between the previous and current one by how far
## through the current tick we are. `Engine.get_physics_interpolation_fraction`
## gives exactly that fraction, and it is available in 4.3.
##
## This node is the *visual* parent. The physics body moves, this node follows
## it smoothly, and the meshes hang off this node.

## The body to follow. If unset, the parent is used.
@export var target_path: NodePath
## Smooth the rotation as well as the position. Almost always wanted.
@export var interpolate_rotation := true
## Distance in metres beyond which the node snaps instead of interpolating.
## Stops the car sliding across the map when it is teleported on respawn.
@export var teleport_threshold := 4.0

var _target: Node3D
var _prev := Transform3D.IDENTITY
var _curr := Transform3D.IDENTITY
var _has_prev := false


func _ready() -> void:
	_target = get_node_or_null(target_path) as Node3D
	if _target == null:
		_target = get_parent() as Node3D
	if _target == null:
		push_warning("TransformSmoothing: no target")
		set_process(false)
		set_physics_process(false)
		return

	# This node publishes its own world transform, so it must not inherit the
	# body's - otherwise the motion would be applied twice.
	top_level = true
	_curr = _target.global_transform
	_prev = _curr
	# Physics must be sampled after the body has been integrated.
	process_physics_priority = 100


func _physics_process(_delta: float) -> void:
	if _target == null or not is_instance_valid(_target):
		return
	_prev = _curr
	_curr = _target.global_transform
	_has_prev = true

	# A respawn or teleport moves the body a long way in one tick; interpolating
	# across that would show the car flying between the two points.
	if _prev.origin.distance_to(_curr.origin) > teleport_threshold:
		_prev = _curr


func _process(_delta: float) -> void:
	if not _has_prev:
		return
	var f := Engine.get_physics_interpolation_fraction()
	var result := Transform3D()
	result.origin = _prev.origin.lerp(_curr.origin, f)
	if interpolate_rotation:
		# Quaternion slerp rather than lerping the basis, so the car does not
		# shear while it rotates.
		var a := _prev.basis.get_rotation_quaternion()
		var b := _curr.basis.get_rotation_quaternion()
		result.basis = Basis(a.slerp(b, f))
	else:
		result.basis = _curr.basis
	global_transform = result
