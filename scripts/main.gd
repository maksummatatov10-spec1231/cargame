extends Node3D

@onready var car: VehicleBody3D = $BMW_1M
@onready var speed_label: Label = $UI/Panel/VBox/SpeedLabel

func _ready():
	print("CarGame Main Loaded - BMW 1M Stage 1")
	print("Ground 200x200, car spawn 4.2m high for suspension test")

func _process(_delta):
	if car and car.has_method("get_speed_kmh"):
		var s = car.get_speed_kmh()
		if speed_label:
			speed_label.text = "%d km/h" % int(s)
			if s > 120:
				speed_label.modulate = Color(1, 0.3, 0.2)
			elif s > 60:
				speed_label.modulate = Color(1, 0.9, 0.2)
			else:
				speed_label.modulate = Color(0.95, 0.95, 0.95)
