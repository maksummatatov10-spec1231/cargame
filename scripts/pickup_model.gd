class_name PickupModel
extends Node3D

## Loads the pickup's parts and wires them to the physics corners.
##
## The BMW ships as a single glTF whose nodes are re-parented at runtime
## (see [CarModel]). The pickup is exported as one glTF per part instead,
## because the source FBX is 490 k polygons across 52 meshes and splitting it
## during conversion is what allows each part to be decimated at its own rate:
## the body tolerates a coarse cell, the wheels need a fine one because they
## are round and spin close to the camera.
##
## Parts loaded:
##   body                chassis, bed, cab, lights
##   wheel_<corner>      tyre and rim, spins and steers
##   hub_<corner>        spring and axle, follows the travel but does not spin

const CORNERS := ["lf", "rf", "lr", "rr"]

## Folder holding the converted parts.
@export var asset_dir := "res://assets/pickup/"
## Paint colour for the body.
@export var paint := Color(0.34, 0.37, 0.42)

var body_part: MeshInstance3D


func _ready() -> void:
	_load_body()
	_attach_wheels()


func _load_body() -> void:
	var mesh := _load_mesh("body")
	if mesh == null:
		push_error("PickupModel: body mesh missing from %s" % asset_dir)
		return
	body_part = MeshInstance3D.new()
	body_part.name = "Body"
	body_part.mesh = mesh
	body_part.material_override = _paint_material()
	body_part.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	add_child(body_part)


func _attach_wheels() -> void:
	var vehicle := _find_vehicle()
	if vehicle == null:
		return
	if vehicle.has_method("ensure_wheels"):
		vehicle.ensure_wheels()
	var wheels_root := vehicle.get_node_or_null("Wheels")
	if wheels_root == null:
		return

	for corner in CORNERS:
		var ray := wheels_root.get_node_or_null(corner.to_upper()) as RayWheel
		if ray == null:
			continue
		ray.wheel_visual = _attach(ray, "wheel_" + corner, "WheelPivot", true)
		ray.hub_visual = _attach(ray, "hub_" + corner, "HubPivot", false)


## Builds a pivot under the ray and hangs the part off it, positioned at the
## bottom of the fully extended damper. update_visuals() lifts it from there.
func _attach(ray: RayWheel, part: String, pivot_name: String,
		is_wheel: bool) -> Node3D:
	var mesh := _load_mesh(part)
	if mesh == null:
		return null

	var pivot := Node3D.new()
	pivot.name = pivot_name
	pivot.rotation_order = EULER_ORDER_YXZ
	pivot.position = Vector3(0.0, -ray.spring_length, 0.0)
	ray.add_child(pivot)

	var inst := MeshInstance3D.new()
	inst.name = part
	inst.mesh = mesh
	inst.material_override = _rubber_material() if is_wheel else _metal_material()
	inst.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	pivot.add_child(inst)
	return pivot


func _load_mesh(part: String) -> Mesh:
	var path := asset_dir + part + ".gltf"
	if not ResourceLoader.exists(path):
		return null
	var packed := load(path) as PackedScene
	if packed == null:
		return null
	return _find_mesh(packed.instantiate())


func _find_mesh(node: Node) -> Mesh:
	if node is MeshInstance3D:
		return (node as MeshInstance3D).mesh
	for child in node.get_children():
		var m := _find_mesh(child)
		if m != null:
			return m
	return null


func _paint_material() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = paint
	mat.metallic = 0.45
	mat.metallic_specular = 0.6
	mat.roughness = 0.32
	mat.rim_enabled = true
	mat.rim = 0.25
	return mat


func _rubber_material() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.065, 0.065, 0.07)
	mat.metallic = 0.0
	mat.roughness = 0.92
	return mat


func _metal_material() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.30, 0.31, 0.33)
	mat.metallic = 0.8
	mat.roughness = 0.42
	return mat


## Matches CarModel's interface so the vehicle can drive either body.
func set_steering(_normalised: float, _delta: float) -> void:
	pass


## Walks up the tree to the Vehicle. The model now sits under a smoothing node,
## so the vehicle is a grandparent rather than the direct parent.
func _find_vehicle() -> Vehicle:
	var node := get_parent()
	while node != null:
		if node is Vehicle:
			return node
		node = node.get_parent()
	return null
