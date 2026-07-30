extends Control

## The title screen: Играть / Настройки / Выход.
##
## This is the project's main scene now; the driving scene is loaded when you
## press Играть. Built in code for the same reason as the rest of the UI - a
## node that is created by the code that uses it cannot be missing.

const GAME_SCENE := "res://scenes/main.tscn"

var _buttons: VBoxContainer


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	set_anchors_preset(Control.PRESET_FULL_RECT)
	# The menu owns the mouse; the game hides it again when it starts.
	Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)
	_build()


func _build() -> void:
	var background := ColorRect.new()
	background.color = MenuTheme.BACKGROUND
	background.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(background)

	# A slow diagonal gradient so the screen is not a flat block of colour.
	var glow := ColorRect.new()
	glow.set_anchors_preset(Control.PRESET_FULL_RECT)
	glow.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var shader := Shader.new()
	shader.code = """
shader_type canvas_item;
void fragment() {
	float d = distance(UV, vec2(0.30, 0.42));
	float glow = smoothstep(0.85, 0.0, d) * 0.30;
	COLOR = vec4(vec3(0.10, 0.15, 0.24) * glow
		+ vec3(0.35, 0.16, 0.04) * glow * 0.5, glow);
}
"""
	var mat := ShaderMaterial.new()
	mat.shader = shader
	glow.material = mat
	add_child(glow)

	var page := VBoxContainer.new()
	page.set_anchors_preset(Control.PRESET_FULL_RECT)
	page.alignment = BoxContainer.ALIGNMENT_CENTER
	page.add_theme_constant_override("separation", 10)
	add_child(page)

	page.add_child(MenuTheme.make_title("CAR GAME", 76))
	page.add_child(MenuTheme.make_subtitle(
		"BMW 1M  ·  Land Rover Defender 110  ·  GHammer"))

	var spacer := Control.new()
	spacer.custom_minimum_size = Vector2(0.0, 34.0)
	page.add_child(spacer)

	_buttons = VBoxContainer.new()
	_buttons.alignment = BoxContainer.ALIGNMENT_CENTER
	_buttons.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	_buttons.add_theme_constant_override("separation", 12)
	page.add_child(_buttons)

	var play := MenuTheme.make_button("Играть")
	play.pressed.connect(_play)
	_buttons.add_child(play)

	var settings := MenuTheme.make_button("Настройки")
	settings.pressed.connect(_open_settings)
	_buttons.add_child(settings)

	var quit := MenuTheme.make_button("Выход")
	quit.pressed.connect(_quit)
	_buttons.add_child(quit)

	var hint := MenuTheme.make_subtitle(
		"В игре: Esc — пауза,  ~ — телеметрия,  V — сменить машину")
	page.add_child(hint)

	play.grab_focus()


func _play() -> void:
	get_tree().paused = false
	get_tree().change_scene_to_file(GAME_SCENE)


func _open_settings() -> void:
	var menu := SettingsMenu.new()
	menu.name = "SettingsMenu"
	add_child(menu)
	_buttons.visible = false
	menu.closed.connect(func() -> void:
		_buttons.visible = true
		if _buttons.get_child_count() > 0:
			(_buttons.get_child(0) as Button).grab_focus())


func _quit() -> void:
	get_tree().quit()
