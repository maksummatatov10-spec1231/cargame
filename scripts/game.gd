class_name Game
extends Node3D

## Top level controller: spawns the chosen vehicle on the terrain and lets you
## swap between them.
##
## The car is no longer placed in the scene file at a fixed height. The ground
## is procedural now, so the spawn point is queried from the terrain and the
## vehicle is dropped a short way above whatever is actually there.

const VEHICLES := [
	{"name": "BMW 1M", "scene": "res://scenes/car.tscn", "drop": 0.55},
	{"name": "Defender 110", "scene": "res://scenes/defender.tscn", "drop": 0.6},
	{"name": "GHammer pickup", "scene": "res://scenes/pickup.tscn", "drop": 0.6},
]

## Which vehicle to start in.
@export_range(0, 2) var start_vehicle := 0
## Where on the terrain to spawn, in metres.
@export var spawn_xz := Vector2(0.0, 8.0)

var current_index := 0
var vehicle: Vehicle

var _terrain: Terrain
var _camera: ChaseCamera
var _hud: Control
var _pause: PauseMenu


func _ready() -> void:
	# The pause menu is created here rather than placed in the scene, so it
	# cannot go missing and cannot be forgotten when the scene is rebuilt.
	_pause = PauseMenu.new()
	_pause.name = "PauseMenu"
	add_child(_pause)

	# The menus turn the cursor on and nothing ever turned it back off, so it
	# sat in the middle of the screen for the whole drive. Hidden while
	# driving, restored by the pause menu.
	Input.set_mouse_mode(Input.MOUSE_MODE_HIDDEN)

	var found := get_tree().get_nodes_in_group("terrain")
	if not found.is_empty():
		_terrain = found[0] as Terrain
	_camera = get_node_or_null("ChaseCamera") as ChaseCamera
	_hud = get_node_or_null("HUD") as Control

	current_index = start_vehicle
	_spawn(current_index)


func _unhandled_input(event: InputEvent) -> void:
	# Esc opens and closes the pause menu. Checked first and marked handled so
	# it cannot also be read as a driving input on the same frame.
	if event.is_action_pressed("ui_cancel"):
		get_viewport().set_input_as_handled()
		if _pause != null:
			_pause.toggle()
		return
	if _pause != null and _pause.is_open():
		return
	if event.is_action_pressed("switch_vehicle"):
		_spawn((current_index + 1) % VEHICLES.size())


## Replaces the current vehicle with another, keeping the camera and HUD
## pointed at whatever is now being driven.
func _spawn(index: int) -> void:
	var entry: Dictionary = VEHICLES[index]
	var packed := load(String(entry["scene"])) as PackedScene
	if packed == null:
		push_error("Game: cannot load %s" % entry["scene"])
		return

	if vehicle != null and is_instance_valid(vehicle):
		vehicle.queue_free()
		# Take it out of the tree immediately so the camera never sees a
		# freed node between now and the end of the frame.
		remove_child(vehicle)

	current_index = index
	vehicle = packed.instantiate() as Vehicle
	vehicle.name = "Vehicle"
	add_child(vehicle)

	var point := spawn_transform(float(entry["drop"]))
	vehicle.global_transform = point
	vehicle.set_spawn(point)

	if _camera:
		_camera.set_target(vehicle.get_node_or_null("CameraTarget"))
	if _hud and _hud.has_method("set_vehicle"):
		_hud.set_vehicle(vehicle)

	print("Game: driving the %s" % entry["name"])


## Spawn transform on the terrain surface, lifted by [param drop] metres so the
## car falls the last short distance onto its springs.
func spawn_transform(drop: float) -> Transform3D:
	var height := 0.0
	if _terrain:
		height = _terrain.sample_height(spawn_xz.x, spawn_xz.y)
	return Transform3D(Basis.IDENTITY,
		Vector3(spawn_xz.x, height + drop, spawn_xz.y))
