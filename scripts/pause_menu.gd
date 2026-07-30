class_name PauseMenu
extends CanvasLayer

## Esc menu: продолжить / настройки / в главное меню / выход.
##
## A CanvasLayer rather than a plain Control so it always draws on top of the
## HUD, and PROCESS_MODE_ALWAYS on everything inside it so the menu keeps
## responding while `get_tree().paused` is true. Forgetting that is the usual
## way a pause menu ends up frozen along with the game.

const MAIN_MENU := "res://scenes/main_menu.tscn"

var _root: Control
var _buttons: VBoxContainer
var _settings: SettingsMenu


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	layer = 100
	_build()
	close()


func _build() -> void:
	_root = Control.new()
	_root.name = "Root"
	_root.process_mode = Node.PROCESS_MODE_ALWAYS
	_root.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(_root)

	_root.add_child(MenuTheme.make_backdrop(0.82))

	var page := VBoxContainer.new()
	page.set_anchors_preset(Control.PRESET_FULL_RECT)
	page.alignment = BoxContainer.ALIGNMENT_CENTER
	page.add_theme_constant_override("separation", 14)
	_root.add_child(page)

	page.add_child(MenuTheme.make_title("ПАУЗА", 52))

	_buttons = VBoxContainer.new()
	_buttons.alignment = BoxContainer.ALIGNMENT_CENTER
	_buttons.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	_buttons.add_theme_constant_override("separation", 12)
	page.add_child(_buttons)

	var resume := MenuTheme.make_button("Продолжить")
	resume.pressed.connect(close)
	_buttons.add_child(resume)

	var settings := MenuTheme.make_button("Настройки")
	settings.pressed.connect(_open_settings)
	_buttons.add_child(settings)

	var to_menu := MenuTheme.make_button("В главное меню")
	to_menu.pressed.connect(_to_main_menu)
	_buttons.add_child(to_menu)

	var quit := MenuTheme.make_button("Выйти из игры")
	quit.pressed.connect(_quit)
	_buttons.add_child(quit)


func is_open() -> bool:
	return _root.visible


func open() -> void:
	_root.visible = true
	get_tree().paused = true
	Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)
	if _buttons.get_child_count() > 0:
		(_buttons.get_child(0) as Button).grab_focus()


func close() -> void:
	if _settings != null and is_instance_valid(_settings):
		_settings.queue_free()
		_settings = null
	_buttons.visible = true
	_root.visible = false
	get_tree().paused = false


func toggle() -> void:
	if is_open():
		close()
	else:
		open()


func _open_settings() -> void:
	_settings = SettingsMenu.new()
	_settings.name = "SettingsMenu"
	_root.add_child(_settings)
	_buttons.visible = false
	_settings.closed.connect(func() -> void:
		_settings = null
		_buttons.visible = true
		if _buttons.get_child_count() > 0:
			(_buttons.get_child(0) as Button).grab_focus())


func _to_main_menu() -> void:
	# Unpause before changing scene, otherwise the menu loads into a paused
	# tree and nothing in it animates.
	get_tree().paused = false
	get_tree().change_scene_to_file(MAIN_MENU)


func _quit() -> void:
	get_tree().paused = false
	get_tree().quit()
