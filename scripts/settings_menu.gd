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
var _preset_value: Label
var _scale_value: Label
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

	# The list is longer than a 900 px window once every option is present, so
	# it scrolls rather than running off the bottom of the screen.
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(760.0, 460.0)
	scroll.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	page.add_child(scroll)

	var body := VBoxContainer.new()
	body.alignment = BoxContainer.ALIGNMENT_BEGIN
	body.add_theme_constant_override("separation", 16)
	body.custom_minimum_size = Vector2(720.0, 0.0)
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(body)

	# --- graphics preset -------------------------------------------------- #
	_preset_value = Label.new()
	_preset_value.label_settings = MenuTheme.label_settings(19, MenuTheme.ACCENT)
	_preset_value.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_preset_value.custom_minimum_size = Vector2(200.0, 0.0)

	var preset_row := HBoxContainer.new()
	preset_row.add_theme_constant_override("separation", 8)
	var preset_down := MenuTheme.make_button("<")
	preset_down.custom_minimum_size = Vector2(52.0, 44.0)
	preset_down.pressed.connect(_step_preset.bind(-1))
	var preset_up := MenuTheme.make_button(">")
	preset_up.custom_minimum_size = Vector2(52.0, 44.0)
	preset_up.pressed.connect(_step_preset.bind(1))
	preset_row.add_child(preset_down)
	preset_row.add_child(_preset_value)
	preset_row.add_child(preset_up)

	body.add_child(MenuTheme.make_row("Качество графики", preset_row,
		"Низкие — тени 90 м, разрешение 80%, растительность 50%, без SSAO и "
		+ "свечения. Средние — тени 140 м, полное разрешение. Высокие — тени "
		+ "190 м и SSAO. Меняет всё разом; отдельные пункты ниже можно "
		+ "подкрутить после."))

	# --- render scale ------------------------------------------------------ #
	var scale_slider := HSlider.new()
	scale_slider.min_value = 0.5
	scale_slider.max_value = 1.0
	scale_slider.step = 0.05
	scale_slider.value = GameSettings.render_scale
	scale_slider.custom_minimum_size = Vector2(300.0, 28.0)
	_scale_value = Label.new()
	_scale_value.label_settings = MenuTheme.label_settings(17, MenuTheme.ACCENT)
	_scale_value.text = "%d%%" % roundi(GameSettings.render_scale * 100.0)
	scale_slider.value_changed.connect(func(v: float) -> void:
		_scale_value.text = "%d%%" % roundi(v * 100.0)
		GameSettings.set_render_scale(v))
	var scale_row := HBoxContainer.new()
	scale_row.add_theme_constant_override("separation", 12)
	scale_row.add_child(scale_slider)
	scale_row.add_child(_scale_value)
	body.add_child(MenuTheme.make_row("Разрешение 3D", scale_row,
		"Сцена рисуется в этом разрешении и растягивается; интерфейс "
		+ "остаётся чётким. Самый сильный рычаг, если упирается видеокарта: "
		+ "80% — это 64% пикселей."))

	# --- shadow distance --------------------------------------------------- #
	var shadow_slider := HSlider.new()
	shadow_slider.min_value = 40.0
	shadow_slider.max_value = 300.0
	shadow_slider.step = 10.0
	shadow_slider.value = GameSettings.shadow_distance
	shadow_slider.custom_minimum_size = Vector2(300.0, 28.0)
	var shadow_value := Label.new()
	shadow_value.label_settings = MenuTheme.label_settings(17, MenuTheme.ACCENT)
	shadow_value.text = "%d м" % roundi(GameSettings.shadow_distance)
	shadow_slider.value_changed.connect(func(v: float) -> void:
		shadow_value.text = "%d м" % roundi(v)
		GameSettings.set_shadow_distance(v))
	var shadow_row := HBoxContainer.new()
	shadow_row.add_theme_constant_override("separation", 12)
	shadow_row.add_child(shadow_slider)
	shadow_row.add_child(shadow_value)
	body.add_child(MenuTheme.make_row("Дальность теней", shadow_row,
		"Важнее разрешения тени: отрисовка теней игнорирует дальность "
		+ "прорисовки растений, поэтому каждое дерево в этом радиусе "
		+ "рисуется в карту теней заново."))

	# --- ssao and glow ----------------------------------------------------- #
	var ssao_box := CheckBox.new()
	ssao_box.text = "SSAO (затенение в углах)"
	ssao_box.button_pressed = GameSettings.ssao
	ssao_box.add_theme_font_size_override("font_size", 17)
	ssao_box.toggled.connect(func(on: bool) -> void: GameSettings.set_ssao(on))
	body.add_child(MenuTheme.make_row("Затенение", ssao_box,
		"На открытой местности заметно только под машиной и у корней "
		+ "деревьев, а стоит около 1-2 мс на кадр."))

	var glow_box := CheckBox.new()
	glow_box.text = "Свечение ярких мест"
	glow_box.button_pressed = GameSettings.glow
	glow_box.add_theme_font_size_override("font_size", 17)
	glow_box.toggled.connect(func(on: bool) -> void: GameSettings.set_glow(on))
	body.add_child(MenuTheme.make_row("Свечение", glow_box, ""))

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
	# Named show_counter, not show: "show" is a method on CanvasItem and
	# shadowing it raises SHADOWED_VARIABLE_BASE_CLASS.
	var show_counter := CheckBox.new()
	show_counter.text = "Показывать счётчик FPS в игре"
	show_counter.button_pressed = GameSettings.show_fps
	show_counter.add_theme_font_size_override("font_size", 17)
	show_counter.toggled.connect(
		func(on: bool) -> void: GameSettings.set_show_fps(on))
	body.add_child(MenuTheme.make_row("Счётчик кадров", show_counter, ""))

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


func _step_preset(direction: int) -> void:
	GameSettings.set_quality_preset(
		wrapi(GameSettings.quality_preset + direction, 0, 3))
	_refresh()


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
	_preset_value.text = GameSettings.preset_label(GameSettings.quality_preset)
	_scale_value.text = "%d%%" % roundi(GameSettings.render_scale * 100.0)

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
