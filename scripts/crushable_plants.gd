class_name CrushablePlants
extends Node3D

## Makes small plants bend under the wheels instead of blocking them.
##
## Anything shorter than [member max_height] gets no collision at all, so the
## car drives straight through. Instead this node watches where the vehicles
## are and pushes the affected plants over in the shader, then lets them spring
## back up once nothing is on top of them.
##
## How the bending is done
## -----------------------
## The plants are drawn with a single [MultiMeshInstance3D] per species, which
## is what keeps thousands of them affordable. That rules out moving each one
## individually on the CPU: writing 1 500 transforms every frame would cost far
## more than the collision did.
##
## So the crush is a shader effect. The script uploads a small list of "crush
## points" - one per wheel, in world space - and the vertex shader bends any
## vertex near one of them. A plant that is 3 m away costs a couple of distance
## checks and nothing else.
##
## Recovery is handled the same way: each point carries a strength that fades
## after the wheel has moved on, so grass springs back rather than snapping
## upright.

## Maximum number of crush points the shader tracks. Four wheels per vehicle.
const MAX_POINTS := 8

## Plants shorter than this get no collision and are crushable instead.
@export var max_height := 0.5
## How far from a wheel a plant starts to bend, in metres.
@export var crush_radius := 1.4
## How long a flattened plant takes to stand back up, in seconds.
@export var recovery_time := 2.5
## Crush points, packed for the shader: xyz = world position, w = strength.
var _points: PackedVector4Array = PackedVector4Array()
## Where each point was last touched, so it can fade.
var _ages: PackedFloat32Array = PackedFloat32Array()

var _materials: Array[ShaderMaterial] = []
var _vehicles: Array[Vehicle] = []


func _ready() -> void:
	_points.resize(MAX_POINTS)
	_ages.resize(MAX_POINTS)
	for i in MAX_POINTS:
		_points[i] = Vector4.ZERO
		_ages[i] = 999.0


## Registers a material so it receives crush updates. Called by the forest for
## every species that is short enough to be crushable.
func register_material(mat: ShaderMaterial) -> void:
	if mat != null and not _materials.has(mat):
		_materials.append(mat)


func _physics_process(delta: float) -> void:
	_refresh_vehicles()

	for i in MAX_POINTS:
		_ages[i] += delta

	# Put a crush point under every wheel that is on the ground.
	var slot := 0
	for vehicle in _vehicles:
		if not is_instance_valid(vehicle):
			continue
		for wheel in vehicle.get_wheels():
			if slot >= MAX_POINTS:
				break
			if not wheel.grounded:
				continue
			var p: Vector3 = wheel.contact_point
			_points[slot] = Vector4(p.x, p.y, p.z, 1.0)
			_ages[slot] = 0.0
			slot += 1

	# Fade the rest so plants recover behind the car.
	for i in range(slot, MAX_POINTS):
		var strength := 1.0 - clampf(_ages[i] / maxf(recovery_time, 0.01), 0.0, 1.0)
		var p: Vector4 = _points[i]
		_points[i] = Vector4(p.x, p.y, p.z, strength)

	for mat in _materials:
		if is_instance_valid(mat):
			mat.set_shader_parameter("crush_points", _points)
			mat.set_shader_parameter("crush_radius", crush_radius)


## Vehicles come and go when you swap between them, so the list is refreshed
## rather than captured once.
func _refresh_vehicles() -> void:
	_vehicles.clear()
	var root := get_tree().current_scene
	if root == null:
		return
	for child in root.get_children():
		if child is Vehicle:
			_vehicles.append(child)
