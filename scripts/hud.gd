extends Control

## Speed / rpm / gear readout plus an optional physics telemetry panel
## (toggled with the tilde key) that shows what every corner is doing.

@export var vehicle_path : NodePath

var _vehicle : Vehicle
var _show_debug := false

@onready var _speed : Label = $Panel/Speed
@onready var _gear : Label = $Panel/Gear
@onready var _rpm : ProgressBar = $Panel/Rpm
@onready var _hint : Label = $Hint
@onready var _debug : Label = $Debug


func _ready() -> void:
	_vehicle = get_node_or_null(vehicle_path) as Vehicle
	_debug.visible = false
	_hint.text = "W / S  throttle & brake      A / D  steer      Space  handbrake" \
		+ "\nC  camera      R  respawn      ~  telemetry"


func _process(_delta: float) -> void:
	if Input.is_action_just_pressed("toggle_debug"):
		_show_debug = not _show_debug
		_debug.visible = _show_debug
	if _vehicle == null:
		return

	_speed.text = "%3d km/h" % roundi(_vehicle.speed_kmh)
	_rpm.value = clampf(_vehicle.engine_rpm / _vehicle.redline_rpm * 100.0, 0.0, 100.0)
	var g := _vehicle.gear
	_gear.text = "R" if g < 0 else ("N" if g == 0 else str(g))

	if _show_debug:
		_debug.text = _build_telemetry()


func _build_telemetry() -> String:
	var lines := PackedStringArray()
	lines.append("rpm %5.0f   gear %s   clutch %.2f" % [
		_vehicle.engine_rpm,
		("R" if _vehicle.gear < 0 else str(_vehicle.gear)),
		_vehicle.clutch])
	var v := _vehicle.linear_velocity
	lines.append("speed %6.2f m/s   height %5.2f m" % [v.length(), _vehicle.global_position.y])
	lines.append("")
	lines.append("corner  load(N)  travel  slipR   slipA   Fx      Fy")
	for w in _vehicle.get_wheels():
		lines.append("%-6s %8.0f  %5.3f  %+6.3f  %+5.1f  %+7.0f %+7.0f" % [
			w.name,
			w.spring_force,
			w.travel,
			w.slip_ratio,
			rad_to_deg(w.slip_angle),
			w.tyre_force.x,
			w.tyre_force.y])
	var total := 0.0
	for w in _vehicle.get_wheels():
		total += w.spring_force
	lines.append("")
	lines.append("total normal load %.0f N   (static %.0f N)" % [total, _vehicle.mass * 9.81])
	return "\n".join(lines)
