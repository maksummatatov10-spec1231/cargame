extends Control

## Speed / rpm / gear readout plus an optional physics telemetry panel
## (toggled with the tilde key) that shows what every corner is doing.

# Frame rate is averaged over a short window. The instantaneous value jumps
# around by tens of frames from one frame to the next, which makes it useless
# for judging whether a change actually helped.
const FPS_WINDOW := 0.5

@export var vehicle_path : NodePath

var _vehicle : Vehicle
var _show_debug := false
var _frames := 0
var _elapsed := 0.0
var _worst_frame := 0.0
var _fps_value := 0.0
var _one_percent_low := 0.0

@onready var _speed : Label = $Panel/Speed
@onready var _gear : Label = $Panel/Gear
@onready var _rpm : ProgressBar = $Panel/Rpm
@onready var _hint : Label = $Hint
@onready var _debug : Label = $Debug
@onready var _fps : Label = $Fps


## Points the readout at a different vehicle, used when they are swapped.
func set_vehicle(v: Vehicle) -> void:
	_vehicle = v


func _ready() -> void:
	if vehicle_path:
		_vehicle = get_node_or_null(vehicle_path) as Vehicle
	_debug.visible = false
	_hint.text = "W / S  throttle & brake      A / D  steer      Shift  TURBO" \
		+ "      Space  handbrake" \
		+ "\nV  swap vehicle      C  camera      R  respawn      ~  telemetry"


func _process(delta: float) -> void:
	_update_fps(delta)
	if Input.is_action_just_pressed("toggle_debug"):
		_show_debug = not _show_debug
		_debug.visible = _show_debug
	if _vehicle == null or not is_instance_valid(_vehicle):
		return

	_speed.text = "%3d km/h" % roundi(_vehicle.speed_kmh)
	# The gear readout turns orange while the turbo is spooled up.
	if _vehicle.boost > 0.05:
		_gear.modulate = Color(1.0, 0.62, 0.25).lerp(
			Color(1.0, 0.35, 0.1), _vehicle.boost)
	else:
		_gear.modulate = Color.WHITE
	_rpm.value = clampf(_vehicle.engine_rpm / _vehicle.redline_rpm * 100.0, 0.0, 100.0)
	var g := _vehicle.gear
	_gear.text = "R" if g < 0 else ("N" if g == 0 else str(g))

	if _show_debug:
		_debug.text = _build_telemetry()


## Averaged frame rate plus the worst frame in the window.
##
## The worst frame is the number that matters on a weak machine: an average of
## 60 with a 90 ms spike every second feels far worse than a steady 40, and
## only the spike tells you something is hitching rather than simply being
## heavy.
func _update_fps(delta: float) -> void:
	_frames += 1
	_elapsed += delta
	_worst_frame = maxf(_worst_frame, delta)
	if _elapsed < FPS_WINDOW:
		return
	_fps_value = _frames / _elapsed
	_one_percent_low = 1.0 / maxf(_worst_frame, 0.0001)
	_frames = 0
	_elapsed = 0.0
	_worst_frame = 0.0

	_fps.text = "%d fps\n%.1f ms   low %d" % [
		roundi(_fps_value), 1000.0 / maxf(_fps_value, 0.001),
		roundi(_one_percent_low)]
	# Green above 50, amber down to 30, red below - so you can tell at a glance
	# without reading the number.
	if _fps_value >= 50.0:
		_fps.modulate = Color(0.55, 1.0, 0.55)
	elif _fps_value >= 30.0:
		_fps.modulate = Color(1.0, 0.85, 0.4)
	else:
		_fps.modulate = Color(1.0, 0.45, 0.4)


func _build_telemetry() -> String:
	var lines := PackedStringArray()
	# Where the time is actually going. On a slow machine the split between
	# these three tells you whether to cut draw calls, triangles or physics.
	lines.append("fps %.1f   frame %.2f ms   worst %.1f ms" % [
		_fps_value, 1000.0 / maxf(_fps_value, 0.001),
		1000.0 / maxf(_one_percent_low, 0.001)])
	lines.append("draw calls %d   tris %d   video mem %.0f MB" % [
		Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
		Performance.get_monitor(Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME),
		Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / 1048576.0])
	lines.append("process %.2f ms   physics %.2f ms" % [
		Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0,
		Performance.get_monitor(Performance.TIME_PHYSICS_PROCESS) * 1000.0])
	lines.append("")
	lines.append("boost %.2f" % _vehicle.boost)
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
