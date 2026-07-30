class_name SettingsMenu
extends Control

## Video settings, built entirely in code.
##
## The frame rate section is the reason this exists. The project shipped with
## `window/vsync/vsync_mode=1` hard-coded and no way to change it, so the
## engine would never present faster than the monitor refreshes - and it also
## shipped `debug/settings/fps/force_fps=0`, which is the *Godot 3* setting
## name and is ignored completely by Godot 4. Between them there was no
## working frame rate control in the project at all.

signal closed

var _fps_value: Label
var _vsync_value: Label
var _refresh_note: Label
var _fps_index := 0


func _ready() -> void:
	# The settings screen has to keep working while the game is paused.
	process_mode = Node.PROCESS_MODE_ALWAYS
	set_anchors_preset(Control.PRESET_FULL_RECT)
	_build()
	_refresh()


func _build() -> void:
	add_child(MenuTheme.make_backdrop(0.96))

	var page := VBoxContainer.new()
	page.set_anchors_preset(Control.PRESET_FULL_RECT)
	page.alignment = BoxContainer.ALIGNMENT_CENTER
	page.add_theme_constant_override("separation", 18)
	add_child(page)

	page.add_child(MenuTheme.make_title("НАСТРОЙКИ", 46))

	var refresh := GameSettings.screen_refresh_rate()
	_refresh_note = MenuTheme.make_subtitle(
		"Частота обновления монитора: %s" %
		("%.0f Гц" % refresh if refresh > 0.0 else "неизвестна"))
	page.add_child(_refresh_note)

	var body := VBoxContainer.new()
	body.alignment = BoxContainer.ALIGNMENT_CENTER
	body.add_theme_constant_override("separation", 16)
	body.custom_minimum_size = Vector2(700.0, 0.0)
	body.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	page.add_child(body)

	# --- frame rate limit ------------------------------------------------ #
	_fps_value = Label.new()
	_fps_value.label_settings = MenuTheme.label_settings(19, MenuTheme.ACCENT)
	_fps_value.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_fps_value.custom_minimum_size = Vector2(200.0, 0.0)

	var fps_row := HBoxContainer.new()
	fps_row.add_theme_constant_override("separation", 8)
	var fps_down := MenuTheme.make_button("<")
	fps_down.custom_minimum_size = Vector2(52.0, 44.0)
	fps_down.pressed.connect(_step_fps.bind(-1))
	var fps_up := MenuTheme.make_button(">")
	fps_up.custom_minimum_size = Vector2(52.0, 44.0)
	fps_up.pressed.connect(_step_fps.bind(1))
	fps_row.add_child(fps_down)
	fps_row.add_child(_fps_value)
	fps_row.add_child(fps_up)

	body.add_child(MenuTheme.make_row("Ограничение FPS", fps_row,
		"«Без ограничения» снимает лимит полностью. Учти: пока включена "
		+ "вертикальная синхронизация, кадры всё равно не пойдут выше "
		+ "частоты монитора — чтобы увидеть больше, поставь синхронизацию "
		+ "в «Выключена»."))

	# --- vsync ------------------------------------------------------------ #
	_vsync_value = Label.new()
	_vsync_value.label_settings = MenuTheme.label_settings(19, MenuTheme.ACCENT)
	_vsync_value.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_vsync_value.custom_minimum_size = Vector2(200.0, 0.0)

	var vs_row := HBoxContainer.new()
	vs_row.add_theme_constant_override("separation", 8)
	var vs_down := MenuTheme.make_button("<")
	vs_down.custom_minimum_size = Vector2(52.0, 44.0)
	vs_down.pressed.connect(_step_vsync.bind(-1))
	var vs_up := MenuTheme.make_button(">")
	vs_up.custom_minimum_size = Vector2(52.0, 44.0)
	vs_up.pressed.connect(_step_vsync.bind(1))
	vs_row.add_child(vs_down)
	vs_row.add_child(_vsync_value)
	vs_row.add_child(vs_up)

	body.add_child(MenuTheme.make_row("Вертикальная синхронизация", vs_row,
		"Выключена — максимум кадров, возможны разрывы. Включена — без "
		+ "разрывов, но не выше частоты монитора. Адаптивная — как "
		+ "включённая, но при просадке рвёт кадр вместо падения вдвое. "
		+ "Тройная буферизация — без разрывов и без жёсткого потолка."))

	# --- fps counter ------------------------------------------------------ #
	var show := CheckBox.new()
	show.text = "Показывать счётчик FPS в игре"
	show.button_pressed = GameSettings.show_fps
	show.add_theme_font_size_override("font_size", 17)
	show.toggled.connect(func(on: bool) -> void: GameSettings.set_show_fps(on))
	body.add_child(MenuTheme.make_row("Счётчик кадров", show, ""))

	# --- vegetation density ----------------------------------------------- #
	var density := HSlider.new()
	density.min_value = 0.2
	density.max_value = 1.5
	density.step = 0.05
	density.value = GameSettings.vegetation_density
	density.custom_minimum_size = Vector2(300.0, 28.0)
	var density_value := Label.new()
	density_value.label_settings = MenuTheme.label_settings(17, MenuTheme.ACCENT)
	density_value.text = "%d%%" % roundi(GameSettings.vegetation_density * 100.0)
	density.value_changed.connect(func(v: float) -> void:
		density_value.text = "%d%%" % roundi(v * 100.0)
		GameSettings.set_vegetation_density(v))
	var density_row := HBoxContainer.new()
	density_row.add_theme_constant_override("separation", 12)
	density_row.add_child(density)
	density_row.add_child(density_value)
	body.add_child(MenuTheme.make_row("Плотность растительности", density_row,
		"Главный рычаг производительности. Растительность — это 4.4 млн "
		+ "треугольников; 50% срезает их вдвое. Применяется при следующем "
		+ "запуске уровня."))

	# --- back -------------------------------------------------------------- #
	var back := MenuTheme.make_button("Назад")
	back.pressed.connect(_close)
	var back_wrap := HBoxContainer.new()
	back_wrap.alignment = BoxContainer.ALIGNMENT_CENTER
	back_wrap.add_child(back)
	page.add_child(back_wrap)
	back.grab_focus()


func _step_fps(direction: int) -> void:
	var options: Array = GameSettings.FPS_OPTIONS
	_fps_index = wrapi(_fps_index + direction, 0, options.size())
	GameSettings.set_max_fps(int(options[_fps_index]))
	_refresh()


func _step_vsync(direction: int) -> void:
	GameSettings.set_vsync(wrapi(GameSettings.vsync + direction, 0, 4))
	_refresh()


func _refresh() -> void:
	var options: Array = GameSettings.FPS_OPTIONS
	_fps_index = maxi(0, options.find(GameSettings.max_fps))
	_fps_value.text = GameSettings.fps_label(GameSettings.max_fps)
	_vsync_value.text = GameSettings.vsync_label(GameSettings.vsync)

	# Say so plainly when the two settings contradict each other, because
	# "I set 240 and still see 75" is otherwise completely baffling.
	var refresh := GameSettings.screen_refresh_rate()
	var capped := GameSettings.vsync == GameSettings.VSync.ON \
		or GameSettings.vsync == GameSettings.VSync.ADAPTIVE
	if capped and refresh > 0.0:
		_refresh_note.text = "Монитор %.0f Гц — синхронизация ограничивает игру этим значением" % refresh
	elif refresh > 0.0:
		_refresh_note.text = "Частота обновления монитора: %.0f Гц" % refresh
	else:
		_refresh_note.text = "Частота обновления монитора: неизвестна"


func _close() -> void:
	closed.emit()
	queue_free()


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		get_viewport().set_input_as_handled()
		_close()
