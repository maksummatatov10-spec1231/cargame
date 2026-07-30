class_name MenuTheme
extends RefCounted

## Shared look for the menus, built in code.
##
## Everything in the UI is constructed by script rather than laid out in a
## .tscn. That is a deliberate response to the "Node not found: Fps" bug: a
## scene file and a script that reference each other by name can drift apart,
## and when they do, @onready silently stores null and every later write to
## .text raises "Invalid assignment ... on a base object of type 'null
## instance'". Nodes that are created by the same code that uses them cannot
## be missing.

const BACKGROUND := Color(0.055, 0.067, 0.086)
const ACCENT := Color(0.98, 0.52, 0.16)
const TEXT := Color(0.90, 0.92, 0.95)
const DIM := Color(0.58, 0.62, 0.69)


static func label_settings(size: int, colour: Color = TEXT) -> LabelSettings:
	var s := LabelSettings.new()
	s.font_size = size
	s.font_color = colour
	s.outline_size = 4
	s.outline_color = Color(0.0, 0.0, 0.0, 0.7)
	return s


static func make_title(text: String, size := 64) -> Label:
	var l := Label.new()
	l.text = text
	l.label_settings = label_settings(size, TEXT)
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	return l


static func make_subtitle(text: String) -> Label:
	var l := Label.new()
	l.text = text
	l.label_settings = label_settings(16, DIM)
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	return l


static func _panel(colour: Color, border: Color, width: int) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = colour
	box.border_color = border
	box.set_border_width_all(width)
	box.set_corner_radius_all(6)
	box.content_margin_left = 26.0
	box.content_margin_right = 26.0
	box.content_margin_top = 14.0
	box.content_margin_bottom = 14.0
	return box


## A button that reads clearly at a glance and reacts to focus, so the menu is
## usable with the keyboard as well as the mouse.
static func make_button(text: String) -> Button:
	var b := Button.new()
	b.text = text
	b.custom_minimum_size = Vector2(360.0, 56.0)
	b.focus_mode = Control.FOCUS_ALL

	b.add_theme_font_size_override("font_size", 22)
	b.add_theme_color_override("font_color", TEXT)
	b.add_theme_color_override("font_hover_color", Color.WHITE)
	b.add_theme_color_override("font_focus_color", Color.WHITE)
	b.add_theme_color_override("font_pressed_color", ACCENT)

	b.add_theme_stylebox_override("normal",
		_panel(Color(0.12, 0.14, 0.18, 0.94), Color(0.24, 0.27, 0.33), 2))
	b.add_theme_stylebox_override("hover",
		_panel(Color(0.18, 0.21, 0.27, 0.98), ACCENT, 2))
	b.add_theme_stylebox_override("focus",
		_panel(Color(0.16, 0.19, 0.24, 0.98), ACCENT, 2))
	b.add_theme_stylebox_override("pressed",
		_panel(Color(0.09, 0.10, 0.13, 1.0), ACCENT, 2))
	return b


## A full-screen dimmed backdrop, so a menu over the game stays readable.
static func make_backdrop(alpha: float) -> ColorRect:
	var r := ColorRect.new()
	r.color = Color(BACKGROUND.r, BACKGROUND.g, BACKGROUND.b, alpha)
	r.set_anchors_preset(Control.PRESET_FULL_RECT)
	r.mouse_filter = Control.MOUSE_FILTER_STOP
	return r


## One labelled row of a settings page: caption on the left, control on the
## right, plus an explanation underneath.
static func make_row(caption: String, control: Control,
		explanation: String) -> VBoxContainer:
	# Named row, not wrap: "wrap" is a global function in GDScript and
	# shadowing it raises SHADOWED_GLOBAL_IDENTIFIER.
	var row := VBoxContainer.new()
	row.add_theme_constant_override("separation", 2)

	var line := HBoxContainer.new()
	line.add_theme_constant_override("separation", 18)

	var name_label := Label.new()
	name_label.text = caption
	name_label.label_settings = label_settings(19, TEXT)
	name_label.custom_minimum_size = Vector2(240.0, 0.0)
	name_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	line.add_child(name_label)

	control.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	line.add_child(control)
	row.add_child(line)

	if explanation != "":
		var note := Label.new()
		note.text = explanation
		note.label_settings = label_settings(13, DIM)
		note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		note.custom_minimum_size = Vector2(600.0, 0.0)
		row.add_child(note)

	return row
