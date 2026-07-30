extends Node

## Global settings: frame rate limit, vsync, and where they are stored.
##
## Registered as an autoload (see project.godot), so it exists before any
## scene loads and survives every scene change. That matters because the
## settings menu lives in the main menu scene while the thing being configured
## is the engine itself.
##
## Everything is applied through Engine.max_fps and
## DisplayServer.window_set_vsync_mode. Note the interaction between the two,
## because it is the reason the game felt locked to a low number:
##
##   * With vsync ON the compositor will not present faster than the display
##     refreshes, so the frame rate is capped at the refresh rate no matter
##     what Engine.max_fps says. On a 75 Hz monitor that is 75.
##   * Engine.max_fps = 0 means unlimited, which is only actually unlimited if
##     vsync is off as well.
##
## The old project.godot shipped `window/vsync/vsync_mode=1` (enabled) with no
## way to change it, and `debug/settings/fps/force_fps=0`, which is a Godot 3
## setting name that Godot 4 ignores entirely - so there was no working fps
## control in the project at all.

signal changed

enum VSync {
	OFF = 0,        ## tear freely, lowest latency, highest frame rate
	ON = 1,         ## never tear, capped at the refresh rate
	ADAPTIVE = 2,   ## vsync, but tears instead of halving if a frame is late
	MAILBOX = 3,    ## triple buffered: no tearing and no hard cap
}

const SAVE_PATH := "user://settings.cfg"

## Frame rate options offered in the menu. 0 means no limit.
const FPS_OPTIONS := [0, 30, 60, 75, 90, 120, 144, 165, 240]

## 0 = unlimited.
var max_fps := 0
var vsync: int = VSync.OFF
## Draw the fps counter in game.
var show_fps := true
## Master multiplier on how much vegetation is drawn, for weak machines.
var vegetation_density := 1.0
## Shadow quality: 0 off, 1 low, 2 medium, 3 high.
var shadow_quality := 2


func _ready() -> void:
	# Must keep running while the tree is paused, otherwise the pause menu
	# could not change a setting.
	process_mode = Node.PROCESS_MODE_ALWAYS
	load_settings()
	apply()


## Pushes the current values into the engine. Safe to call at any time.
func apply() -> void:
	Engine.max_fps = max_fps
	DisplayServer.window_set_vsync_mode(vsync as DisplayServer.VSyncMode)
	changed.emit()


func set_max_fps(value: int) -> void:
	max_fps = maxi(0, value)
	apply()
	save_settings()


func set_vsync(value: int) -> void:
	vsync = clampi(value, 0, 3)
	apply()
	save_settings()


func set_show_fps(value: bool) -> void:
	show_fps = value
	apply()
	save_settings()


func set_vegetation_density(value: float) -> void:
	vegetation_density = clampf(value, 0.1, 2.0)
	apply()
	save_settings()


func set_shadow_quality(value: int) -> void:
	shadow_quality = clampi(value, 0, 3)
	apply()
	save_settings()


## Human readable label for the current limit, used by the menu.
func fps_label(value: int) -> String:
	return "Без ограничения" if value == 0 else "%d FPS" % value


func vsync_label(value: int) -> String:
	match value:
		VSync.OFF:
			return "Выключена"
		VSync.ON:
			return "Включена"
		VSync.ADAPTIVE:
			return "Адаптивная"
		_:
			return "Тройная буферизация"


## The refresh rate of the screen the window is on, or 0 if it cannot be read.
## Shown in the menu so the numbers on offer mean something.
func screen_refresh_rate() -> float:
	var hz := DisplayServer.screen_get_refresh_rate(
		DisplayServer.window_get_current_screen())
	return hz if hz > 0.0 else 0.0


func save_settings() -> void:
	var cfg := ConfigFile.new()
	cfg.set_value("video", "max_fps", max_fps)
	cfg.set_value("video", "vsync", vsync)
	cfg.set_value("video", "show_fps", show_fps)
	cfg.set_value("video", "shadow_quality", shadow_quality)
	cfg.set_value("world", "vegetation_density", vegetation_density)
	cfg.save(SAVE_PATH)


func load_settings() -> void:
	var cfg := ConfigFile.new()
	if cfg.load(SAVE_PATH) != OK:
		return
	max_fps = int(cfg.get_value("video", "max_fps", max_fps))
	vsync = int(cfg.get_value("video", "vsync", vsync))
	show_fps = bool(cfg.get_value("video", "show_fps", show_fps))
	shadow_quality = int(cfg.get_value("video", "shadow_quality", shadow_quality))
	vegetation_density = float(
		cfg.get_value("world", "vegetation_density", vegetation_density))
