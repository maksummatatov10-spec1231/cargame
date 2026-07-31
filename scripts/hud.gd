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

# Looked up with get_node_or_null and *created if absent*, rather than with
# $Name, which is what caused
#   "Node not found: Fps (relative to /root/Main/HUD)"
# followed by
#   "Invalid assignment of property 'text' ... on a base object of type
#    'null instance'"
# every frame afterwards. @onready var x: Label = $Fps does not fail softly:
# it stores null and then every single write to x.text is an error.
#
# Building the labels in code removes the whole failure mode. There is no
# scene-file node that can go missing, no name to get out of step with the
# script, and the HUD is guaranteed to be complete before anything reads it.
var _speed : Label
var _gear : Label
var _rpm : ProgressBar
var _hint : Label
var _debug : Label
var _fps : Label
var _damage_label : Label


## Points the readout at a different vehicle, used when they are swapped.
func set_vehicle(v: Vehicle) -> void:
	_vehicle = v


func _ready() -> void:
	_bind_widgets()
	if vehicle_path:
		_vehicle = get_node_or_null(vehicle_path) as Vehicle
	_debug.visible = false
	_hint.text = "W / S  газ и тормоз      A / D  руль      Shift  ТУРБО" \
		+ "      Пробел  ручник" \
		+ "\nV  сменить машину      C  камера      R  респавн" \
		+ "      ~  телеметрия      Esc  пауза"
	if GameSettings:
		GameSettings.changed.connect(_on_settings_changed)
		_on_settings_changed()


## Finds each widget, and builds it if the scene does not provide one.
##
## Every lookup is guarded, so a missing node degrades to a freshly created
## one instead of a null that poisons every later frame.
func _bind_widgets() -> void:
	var panel := get_node_or_null("Panel") as Control
	if panel == null:
		panel = Control.new()
		panel.name = "Panel"
		panel.set_anchors_preset(Control.PRESET_BOTTOM_RIGHT)
		panel.offset_left = -300.0
		panel.offset_top = -140.0
		panel.offset_right = -28.0
		panel.offset_bottom = -28.0
		panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
		add_child(panel)

	_speed = _need_label(panel, "Speed", 44)
	_speed.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_gear = _need_label(panel, "Gear", 34)
	_gear.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT

	_rpm = get_node_or_null("Panel/Rpm") as ProgressBar
	if _rpm == null:
		_rpm = ProgressBar.new()
		_rpm.name = "Rpm"
		_rpm.show_percentage = false
		_rpm.offset_top = 102.0
		_rpm.offset_right = 272.0
		_rpm.offset_bottom = 114.0
		panel.add_child(_rpm)

	_hint = _need_label(self, "Hint", 15)
	_hint.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
	_hint.offset_left = 26.0
	_hint.offset_top = -78.0
	_hint.offset_right = 760.0
	_hint.offset_bottom = -20.0

	_debug = _need_label(self, "Debug", 15)
	_debug.offset_left = 26.0
	_debug.offset_top = 22.0
	_debug.offset_right = 760.0
	_debug.offset_bottom = 340.0

	_damage_label = _need_label(self, "Damage", 17)
	_damage_label.set_anchors_preset(Control.PRESET_TOP_LEFT)
	_damage_label.offset_left = 26.0
	_damage_label.offset_top = 18.0
	_damage_label.offset_right = 320.0
	_damage_label.offset_bottom = 44.0
	_damage_label.visible = false

	_fps = _need_label(self, "Fps", 18)
	_fps.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	_fps.offset_left = -240.0
	_fps.offset_top = 18.0
	_fps.offset_right = -22.0
	_fps.offset_bottom = 86.0
	_fps.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_fps.text = "-- fps"


## Returns the named Label, creating it if the scene has not got one.
func _need_label(parent: Node, label_name: String, font_size: int) -> Label:
	var found := parent.get_node_or_null(label_name) as Label
	if found != null:
		return found
	var made := Label.new()
	made.name = label_name
	made.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var settings := LabelSettings.new()
	settings.font_size = font_size
	settings.outline_size = 5
	settings.outline_color = Color(0.0, 0.0, 0.0, 0.75)
	made.label_settings = settings
	parent.add_child(made)
	return made


func _on_settings_changed() -> void:
	if _fps != null:
		_fps.visible = GameSettings.show_fps


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

	_update_damage()

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


## Damage readout. Hidden while the car is intact, so it is not clutter on a
## clean run - it appearing at all is the signal that something happened.
func _update_damage() -> void:
	if _damage_label == null:
		return
	var damage := _vehicle.get_node_or_null("Damage") as VehicleDamage
	if damage == null or damage.total_damage < 0.01:
		_damage_label.visible = false
		return
	_damage_label.visible = true
	var pct := roundi(damage.total_damage * 100.0)
	_damage_label.text = "ПОВРЕЖДЕНИЯ  %d%%   (R — ремонт)" % pct
	if damage.total_damage > 0.6:
		_damage_label.modulate = Color(1.0, 0.42, 0.36)
	elif damage.total_damage > 0.25:
		_damage_label.modulate = Color(1.0, 0.78, 0.36)
	else:
		_damage_label.modulate = Color(0.95, 0.92, 0.7)


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
	var damage := _vehicle.get_node_or_null("Damage") as VehicleDamage
	if damage != null:
		lines.append("damage %.0f%%  corners %s  dents %d" % [
			damage.total_damage * 100.0,
			str(damage.corner_damage.map(
				func(d: float) -> int: return roundi(d * 100.0))),
			damage.dent_count()])
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
