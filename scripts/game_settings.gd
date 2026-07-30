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
## Overall graphics preset. Everything below is derived from it, so one
## setting moves the whole scene rather than making the player tune ten.
##   0 Низкие, 1 Средние, 2 Высокие
var quality_preset := 1
## Screen-space ambient occlusion. Outdoors, under a directional sun, it is
## only really visible under the car and at the base of trees, and it costs
## roughly 1-2 ms of a 16.7 ms frame at 1600x900.
var ssao := true
## Bloom. A downsample/upsample chain over the whole screen every frame.
var glow := true
## Directional shadow map resolution. The atlas is split into one quadrant
## per cascade (render_forward_clustered.cpp:2420), so 4096 gives each
## cascade 2048x2048.
var shadow_size := 4096
## How far shadows are drawn, in metres. This is the setting that decides how
## much GEOMETRY enters the shadow passes, which costs far more than the map
## resolution does.
var shadow_distance := 190.0
## Screen resolution scale. Below 1.0 the 3D scene renders smaller and is
## upscaled; the UI stays sharp. The single most effective control there is
## when the GPU is fill-rate bound.
var render_scale := 1.0


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
	_apply_to_scene()
	changed.emit()


## Pushes the graphics settings into whatever scene is currently loaded.
##
## Done by walking the tree rather than by holding references, because the
## driving scene comes and goes while this autoload does not.
func _apply_to_scene() -> void:
	var tree := get_tree()
	if tree == null:
		return
	var root := tree.current_scene
	if root == null:
		return

	# Render scale: the 3D scene is rendered smaller and upscaled, the UI is
	# not. This is the strongest single control when the GPU is fill bound.
	var viewport := get_viewport()
	if viewport != null:
		viewport.scaling_3d_scale = render_scale
		viewport.scaling_3d_mode = Viewport.SCALING_3D_MODE_BILINEAR

	RenderingServer.directional_shadow_atlas_set_size(shadow_size, true)

	for node in _find_all(root):
		var env_holder := node as WorldEnvironment
		if env_holder != null and env_holder.environment != null:
			env_holder.environment.ssao_enabled = ssao
			env_holder.environment.glow_enabled = glow
		var light := node as DirectionalLight3D
		if light != null and light.shadow_enabled:
			light.directional_shadow_max_distance = shadow_distance


func _find_all(node: Node) -> Array[Node]:
	var out: Array[Node] = [node]
	for child in node.get_children():
		out.append_array(_find_all(child))
	return out


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


## Applies a whole preset at once.
##
## The numbers are chosen from what was measured in this scene, not from a
## generic template:
##   * shadow_distance dominates, because shadow rendering ignores the
##     per-species cull distance and re-submits every caster inside it.
##   * render_scale is the strongest single lever if the GPU is fill bound.
##   * ssao and glow are full-screen passes worth about 1-2 ms each here.
func set_quality_preset(value: int) -> void:
	quality_preset = clampi(value, 0, 2)
	match quality_preset:
		0:      # Низкие
			ssao = false
			glow = false
			shadow_size = 2048
			shadow_distance = 90.0
			render_scale = 0.8
			vegetation_density = 0.5
		1:      # Средние
			ssao = false
			glow = true
			shadow_size = 3072
			shadow_distance = 140.0
			render_scale = 1.0
			vegetation_density = 1.0
		_:      # Высокие
			ssao = true
			glow = true
			shadow_size = 4096
			shadow_distance = 190.0
			render_scale = 1.0
			vegetation_density = 1.0
	apply()
	save_settings()


func preset_label(value: int) -> String:
	match value:
		0:
			return "Низкие"
		1:
			return "Средние"
		_:
			return "Высокие"


func set_render_scale(value: float) -> void:
	render_scale = clampf(value, 0.5, 1.0)
	apply()
	save_settings()


func set_ssao(value: bool) -> void:
	ssao = value
	apply()
	save_settings()


func set_glow(value: bool) -> void:
	glow = value
	apply()
	save_settings()


func set_shadow_distance(value: float) -> void:
	shadow_distance = clampf(value, 40.0, 300.0)
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
	cfg.set_value("video", "quality_preset", quality_preset)
	cfg.set_value("video", "ssao", ssao)
	cfg.set_value("video", "glow", glow)
	cfg.set_value("video", "shadow_size", shadow_size)
	cfg.set_value("video", "shadow_distance", shadow_distance)
	cfg.set_value("video", "render_scale", render_scale)
	cfg.set_value("world", "vegetation_density", vegetation_density)
	cfg.save(SAVE_PATH)


func load_settings() -> void:
	var cfg := ConfigFile.new()
	if cfg.load(SAVE_PATH) != OK:
		return
	max_fps = int(cfg.get_value("video", "max_fps", max_fps))
	vsync = int(cfg.get_value("video", "vsync", vsync))
	show_fps = bool(cfg.get_value("video", "show_fps", show_fps))
	quality_preset = int(cfg.get_value("video", "quality_preset", quality_preset))
	ssao = bool(cfg.get_value("video", "ssao", ssao))
	glow = bool(cfg.get_value("video", "glow", glow))
	shadow_size = int(cfg.get_value("video", "shadow_size", shadow_size))
	shadow_distance = float(cfg.get_value("video", "shadow_distance", shadow_distance))
	render_scale = float(cfg.get_value("video", "render_scale", render_scale))
	vegetation_density = float(
		cfg.get_value("world", "vegetation_density", vegetation_density))
