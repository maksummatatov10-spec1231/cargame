class_name CarModel
extends Node3D

## Instances the imported glTF and hands its parts to the vehicle.
##
## The converter (tools/fbx_to_gltf.py) exports the car as ten nodes whose
## pivots are already in the right place:
##
##   body        chassis shell, interior, lights, glass
##   steering    the steering wheel, pivoted on the column
##   hub_<xx>    brake disc + caliper + upright, follows the suspension
##   wheel_<xx>  rim + tyre, follows the suspension *and* spins
##
## Doing the wiring here rather than in the .tscn means the scene keeps working
## no matter how the importer decides to name or nest things.

const CORNERS := ["lf", "rf", "lr", "rr"]

## Material name fragments that identify cabin surfaces. Taken from the
## asset's own material list, so this is not guesswork: every INT_* material
## plus the glass that is only ever seen from inside.
const INTERIOR_MATERIALS := [
	"int_", "cockpit", "cuciture", "pedali", "pedana", "leather",
	"volante", "cinture", "speakers", "display", "rotelline",
	"door_grid", "velluto", "stoffa", "defrost_interno",
]

@export var model_scene : PackedScene
## Degrees of steering wheel rotation at full lock (about 1.3 turns each way).
@export var steering_wheel_ratio := 470.0

var body_part : Node3D
var steering_part : Node3D

var wheel_parts := {}
var hub_parts := {}

var _steer_angle := 0.0

## Surfaces that belong to the cabin. Hidden in the chase view, shown in the
## bumper/hood view.
##
## Measured on the BMW: 56,411 of its 100,582 triangles are interior - the
## dashboard alone (INT_Plaastica_NERA) is 26,723, more than a quarter of the
## whole car. From behind the car none of it is visible; the Z-buffer throws
## the pixels away but the vertices are still transformed, the draw calls are
## still issued and the surfaces are still sorted every frame. Hiding a
## surface removes all of that, unlike relying on depth rejection.
var _interior: Array[GeometryInstance3D] = []
var _interior_visible := true



func _ready() -> void:
	if model_scene == null:
		push_error("CarModel: no model_scene assigned")
		return
	var root := model_scene.instantiate()
	add_child(root)
	_collect(root)
	_find_interior(root)
	# The wheels are moved out of the imported scene and under the RayWheels.
	# Deferring it keeps Godot from complaining about re-parenting while the
	# tree is still being set up.
	_reparent_parts.call_deferred()


func _collect(node: Node) -> void:
	var id := String(node.name).to_lower()
	if node is Node3D:
		if id.begins_with("body"):
			body_part = node
		elif id.begins_with("steering"):
			steering_part = node
		elif id.begins_with("wheel_"):
			var c := id.substr(6, 2)
			if c in CORNERS:
				wheel_parts[c] = node
		elif id.begins_with("hub_"):
			var c := id.substr(4, 2)
			if c in CORNERS:
				hub_parts[c] = node
	for child in node.get_children():
		_collect(child)


## Collects the cabin surfaces so they can be switched off in the chase view.
func _find_interior(node: Node) -> void:
	var mesh_node := node as MeshInstance3D
	if mesh_node != null and mesh_node.mesh != null:
		var interior := false
		for i in mesh_node.mesh.get_surface_count():
			var mat := mesh_node.mesh.surface_get_material(i)
			if mat == null:
				continue
			var id := String(mat.resource_name).to_lower()
			for token in INTERIOR_MATERIALS:
				if id.contains(token):
					interior = true
					break
			if interior:
				break
		if interior:
			_interior.append(mesh_node)
	for child in node.get_children():
		_find_interior(child)


## Shows or hides the cabin. Called by the camera when the view changes.
func set_interior_visible(shown: bool) -> void:
	if shown == _interior_visible:
		return
	_interior_visible = shown
	for part in _interior:
		if is_instance_valid(part):
			part.visible = shown


## How many surfaces were classified as interior, for the checks.
func interior_surface_count() -> int:
	return _interior.size()


## Moves the wheel and hub meshes under the matching [RayWheel] so the
## suspension travel and the wheel spin drive them directly.
func _reparent_parts() -> void:
	var vehicle := _find_vehicle()
	if vehicle == null:
		return
	var wheels_root := vehicle.get_node_or_null("Wheels")
	if wheels_root == null:
		return

	for corner in CORNERS:
		var ray := wheels_root.get_node_or_null(corner.to_upper()) as RayWheel
		if ray == null:
			continue

		ray.wheel_visual = _attach(ray, wheel_parts.get(corner), "WheelPivot")
		ray.hub_visual = _attach(ray, hub_parts.get(corner), "HubPivot")


## Moves [param mesh] under a fresh pivot parented to [param ray], positioned at
## the bottom of the fully extended damper. Returns the pivot the wheel script
## should animate.
func _attach(ray: RayWheel, mesh: Node3D, pivot_name: String) -> Node3D:
	if mesh == null:
		return null
	var pivot := Node3D.new()
	pivot.name = pivot_name
	pivot.rotation_order = EULER_ORDER_YXZ
	# The glTF pivot is the hub centre in car space; inside the ray it belongs at
	# the bottom of the suspension travel, and update_visuals() lifts it from
	# there by however much the spring is compressed.
	pivot.position = Vector3(0.0, -ray.spring_length, 0.0)
	ray.add_child(pivot)

	var old_parent := mesh.get_parent()
	if old_parent:
		old_parent.remove_child(mesh)
	pivot.add_child(mesh)
	mesh.position = Vector3.ZERO
	mesh.rotation = Vector3.ZERO
	return pivot


## Called by the vehicle so the steering wheel in the cockpit turns with the
## front wheels.
func set_steering(normalised: float, delta: float) -> void:
	if steering_part == null:
		return
	_steer_angle = lerpf(_steer_angle, normalised, clampf(delta * 12.0, 0.0, 1.0))
	steering_part.rotation.z = deg_to_rad(steering_wheel_ratio * 0.5) * _steer_angle


## Walks up the tree to the Vehicle. The model now sits under a smoothing node,
## so the vehicle is a grandparent rather than the direct parent.
func _find_vehicle() -> Vehicle:
	var node := get_parent()
	while node != null:
		if node is Vehicle:
			return node
		node = node.get_parent()
	return null
