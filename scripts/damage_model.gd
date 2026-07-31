class_name DamageModel
extends RefCounted

## The data model behind collision damage: zones, components and materials.
##
## Kept separate from damage.gd so the maths can be tested without a running
## engine, and so the vehicle node stays about applying damage rather than
## about defining what damage is.
##
## THE IDEA
##
## A car is not one damage bar. It is a structure with parts in known places,
## and a hit does different things depending on where it lands and how hard.
## So an impact is resolved in three stages:
##
##   1. Which ZONE was hit - front, left front corner, roof, and so on.
##      Zones are boxes in the body's own space, sized from the real AABB.
##   2. Which COMPONENTS live in that zone, and how exposed each one is.
##      The radiator is behind the front bumper; the gearbox is under the
##      middle of the floor; the fuel tank is behind the rear axle.
##   3. What each damaged component does to the physics. Nothing is
##      scripted as "handling gets worse" - components change the same
##      numbers the tyre model and the drivetrain already read.
##
## ENERGY, NOT IMPULSE ALONE
##
## Impulse (N s) tells you how hard the hit was, but the damage a structure
## takes is closer to the ENERGY it had to absorb, because that is what has
## to go somewhere - into crumpling metal. Energy scales with the square of
## the closing speed, which is why 60 km/h is four times as bad as 30, not
## twice. The model uses both: impulse decides whether anything happened at
## all, energy decides how much.

# --------------------------------------------------------------------------- #
#  zones
# --------------------------------------------------------------------------- #

## The parts of the shell that can be dented independently.
##
## Ordered front to back. The bounds are FRACTIONS of the body's own bounding
## box, so the same layout works for a hatchback, a Defender and a pickup
## without three sets of hand-measured numbers.
##
##   x: -1 = left,  +1 = right
##   y:  0 = floor, 1 = roof
##   z: -1 = nose,  +1 = tail       (Godot's -Z is forward)
enum Zone {
	NOSE, FRONT_LEFT, FRONT_RIGHT,
	LEFT_FRONT_DOOR, RIGHT_FRONT_DOOR,
	LEFT_REAR_DOOR, RIGHT_REAR_DOOR,
	ROOF, WINDSCREEN,
	REAR_LEFT, REAR_RIGHT, TAIL,
	FLOOR,
}

enum Part {
	ENGINE, RADIATOR, INTERCOOLER, OIL_PAN,
	GEARBOX, DIFFERENTIAL, DRIVESHAFT,
	FUEL_TANK, EXHAUST,
	STEERING_RACK,
	SUSPENSION_LF, SUSPENSION_RF, SUSPENSION_LR, SUSPENSION_RR,
	BRAKE_LF, BRAKE_RF, BRAKE_LR, BRAKE_RR,
	WHEEL_LF, WHEEL_RF, WHEEL_LR, WHEEL_RR,
	HEADLIGHT_L, HEADLIGHT_R, TAILLIGHT_L, TAILLIGHT_R,
	WINDSCREEN_GLASS, MIRROR_L, MIRROR_R,
	BODY_SHELL,
}

## name, x range, y range, z range, structural weight
##
## The structural weight is how much of the car's rigidity that zone carries.
## Crushing the nose is survivable; folding the floor pan is not, because
## everything is bolted to it.
const ZONES := {
	Zone.NOSE: {
		"name": "передний бампер",
		"x": [-0.7, 0.7], "y": [0.0, 0.55], "z": [-1.0, -0.75],
		"structure": 0.35,
	},
	Zone.FRONT_LEFT: {
		"name": "левое переднее крыло",
		"x": [-1.0, -0.35], "y": [0.0, 0.7], "z": [-0.95, -0.35],
		"structure": 0.5,
	},
	Zone.FRONT_RIGHT: {
		"name": "правое переднее крыло",
		"x": [0.35, 1.0], "y": [0.0, 0.7], "z": [-0.95, -0.35],
		"structure": 0.5,
	},
	Zone.LEFT_FRONT_DOOR: {
		"name": "левая передняя дверь",
		"x": [-1.0, -0.45], "y": [0.15, 0.85], "z": [-0.4, 0.1],
		"structure": 0.4,
	},
	Zone.RIGHT_FRONT_DOOR: {
		"name": "правая передняя дверь",
		"x": [0.45, 1.0], "y": [0.15, 0.85], "z": [-0.4, 0.1],
		"structure": 0.4,
	},
	Zone.LEFT_REAR_DOOR: {
		"name": "левый порог",
		"x": [-1.0, -0.45], "y": [0.0, 0.6], "z": [0.05, 0.6],
		"structure": 0.6,
	},
	Zone.RIGHT_REAR_DOOR: {
		"name": "правый порог",
		"x": [0.45, 1.0], "y": [0.0, 0.6], "z": [0.05, 0.6],
		"structure": 0.6,
	},
	Zone.ROOF: {
		"name": "крыша",
		"x": [-0.9, 0.9], "y": [0.8, 1.0], "z": [-0.4, 0.5],
		"structure": 0.7,
	},
	Zone.WINDSCREEN: {
		"name": "лобовое стекло",
		"x": [-0.8, 0.8], "y": [0.55, 0.9], "z": [-0.55, -0.15],
		"structure": 0.2,
	},
	Zone.REAR_LEFT: {
		"name": "левое заднее крыло",
		"x": [-1.0, -0.35], "y": [0.0, 0.7], "z": [0.55, 0.95],
		"structure": 0.45,
	},
	Zone.REAR_RIGHT: {
		"name": "правое заднее крыло",
		"x": [0.35, 1.0], "y": [0.0, 0.7], "z": [0.55, 0.95],
		"structure": 0.45,
	},
	Zone.TAIL: {
		"name": "задний бампер",
		"x": [-0.7, 0.7], "y": [0.0, 0.55], "z": [0.8, 1.0],
		"structure": 0.3,
	},
	Zone.FLOOR: {
		"name": "днище",
		"x": [-0.9, 0.9], "y": [0.0, 0.25], "z": [-0.6, 0.6],
		"structure": 1.0,
	},
}

# --------------------------------------------------------------------------- #
#  components
# --------------------------------------------------------------------------- #


## Where each component sits, how well it is protected, and what breaking it
## costs.
##
##   pos       : position in body fractions, same axes as the zones
##   shield    : 0 = fully exposed, 1 = deep inside the structure. A hit's
##               severity is multiplied by (1 - shield) before it reaches the
##               part, so a radiator behind a bumper still suffers but an
##               engine behind a radiator suffers less.
##   fragility : how readily it breaks once reached. Glass is fragile, a
##               cast-iron block is not.
const PARTS := {
	Part.ENGINE: {
		"name": "двигатель", "pos": [0.0, 0.35, -0.75],
		"shield": 0.55, "fragility": 0.7,
	},
	Part.RADIATOR: {
		"name": "радиатор", "pos": [0.0, 0.3, -0.95],
		"shield": 0.1, "fragility": 1.4,
	},
	Part.INTERCOOLER: {
		"name": "интеркулер", "pos": [0.0, 0.18, -0.98],
		"shield": 0.05, "fragility": 1.5,
	},
	Part.OIL_PAN: {
		"name": "поддон картера", "pos": [0.0, 0.06, -0.6],
		"shield": 0.25, "fragility": 1.1,
	},
	Part.GEARBOX: {
		"name": "коробка передач", "pos": [0.0, 0.12, -0.25],
		"shield": 0.6, "fragility": 0.6,
	},
	Part.DIFFERENTIAL: {
		"name": "дифференциал", "pos": [0.0, 0.1, 0.85],
		"shield": 0.5, "fragility": 0.6,
	},
	Part.DRIVESHAFT: {
		"name": "карданный вал", "pos": [0.0, 0.08, 0.3],
		"shield": 0.45, "fragility": 0.8,
	},
	Part.FUEL_TANK: {
		# Sits just behind the rear axle, which is why a rear-end impact is
		# the one that reaches it. Shield lowered from 0.4 after measuring:
		# a centre rear hit at severity 0.8 was only doing 6% damage, which
		# made rear impacts feel consequence-free.
		"name": "топливный бак", "pos": [0.0, 0.2, 0.78],
		"shield": 0.2, "fragility": 1.0,
	},
	Part.EXHAUST: {
		"name": "выхлопная система", "pos": [0.0, 0.05, 0.5],
		"shield": 0.15, "fragility": 1.2,
	},
	Part.STEERING_RACK: {
		"name": "рулевая рейка", "pos": [0.0, 0.15, -0.7],
		"shield": 0.45, "fragility": 0.9,
	},
	Part.SUSPENSION_LF: {
		"name": "подвеска перед. лев.", "pos": [-0.8, 0.2, -0.8],
		"shield": 0.2, "fragility": 1.0, "corner": 0,
	},
	Part.SUSPENSION_RF: {
		"name": "подвеска перед. прав.", "pos": [0.8, 0.2, -0.8],
		"shield": 0.2, "fragility": 1.0, "corner": 1,
	},
	Part.SUSPENSION_LR: {
		"name": "подвеска задн. лев.", "pos": [-0.8, 0.2, 0.8],
		"shield": 0.2, "fragility": 1.0, "corner": 2,
	},
	Part.SUSPENSION_RR: {
		"name": "подвеска задн. прав.", "pos": [0.8, 0.2, 0.8],
		"shield": 0.2, "fragility": 1.0, "corner": 3,
	},
	Part.BRAKE_LF: {
		"name": "тормоз перед. лев.", "pos": [-0.85, 0.15, -0.8],
		"shield": 0.3, "fragility": 0.9, "corner": 0,
	},
	Part.BRAKE_RF: {
		"name": "тормоз перед. прав.", "pos": [0.85, 0.15, -0.8],
		"shield": 0.3, "fragility": 0.9, "corner": 1,
	},
	Part.BRAKE_LR: {
		"name": "тормоз задн. лев.", "pos": [-0.85, 0.15, 0.8],
		"shield": 0.3, "fragility": 0.9, "corner": 2,
	},
	Part.BRAKE_RR: {
		"name": "тормоз задн. прав.", "pos": [0.85, 0.15, 0.8],
		"shield": 0.3, "fragility": 0.9, "corner": 3,
	},
	Part.WHEEL_LF: {
		"name": "колесо перед. лев.", "pos": [-0.95, 0.15, -0.85],
		"shield": 0.0, "fragility": 1.1, "corner": 0,
	},
	Part.WHEEL_RF: {
		"name": "колесо перед. прав.", "pos": [0.95, 0.15, -0.85],
		"shield": 0.0, "fragility": 1.1, "corner": 1,
	},
	Part.WHEEL_LR: {
		"name": "колесо задн. лев.", "pos": [-0.95, 0.15, 0.85],
		"shield": 0.0, "fragility": 1.1, "corner": 2,
	},
	Part.WHEEL_RR: {
		"name": "колесо задн. прав.", "pos": [0.95, 0.15, 0.85],
		"shield": 0.0, "fragility": 1.1, "corner": 3,
	},
	Part.HEADLIGHT_L: {
		"name": "левая фара", "pos": [-0.6, 0.4, -0.95],
		"shield": 0.05, "fragility": 2.0,
	},
	Part.HEADLIGHT_R: {
		"name": "правая фара", "pos": [0.6, 0.4, -0.95],
		"shield": 0.05, "fragility": 2.0,
	},
	Part.TAILLIGHT_L: {
		"name": "левый фонарь", "pos": [-0.6, 0.45, 0.95],
		"shield": 0.05, "fragility": 2.0,
	},
	Part.TAILLIGHT_R: {
		"name": "правый фонарь", "pos": [0.6, 0.45, 0.95],
		"shield": 0.05, "fragility": 2.0,
	},
	Part.WINDSCREEN_GLASS: {
		"name": "лобовое стекло", "pos": [0.0, 0.78, -0.3],
		"shield": 0.05, "fragility": 1.8,
	},
	Part.MIRROR_L: {
		"name": "левое зеркало", "pos": [-1.0, 0.65, -0.3],
		"shield": 0.0, "fragility": 2.5,
	},
	Part.MIRROR_R: {
		"name": "правое зеркало", "pos": [1.0, 0.65, -0.3],
		"shield": 0.0, "fragility": 2.5,
	},
	Part.BODY_SHELL: {
		# Reach is measured from a single point, so a shell centred at
		# mid-height was out of range of a roof strike. Raised so rolling the
		# car actually deforms it.
		"name": "кузов", "pos": [0.0, 0.62, 0.0],
		"shield": 0.15, "fragility": 0.8,
	},
}

## How far from a part an impact can be and still reach it, in body fractions.
const PART_REACH := 0.55

# --------------------------------------------------------------------------- #
#  resolving an impact
# --------------------------------------------------------------------------- #

## Converts a world impact into normalised body coordinates.
##
## x and z run -1..1 across the body's bounding box, y runs 0..1 from floor
## to roof. Working in fractions rather than metres is what lets one layout
## serve a coupe, a 4x4 and a pickup.
static func to_body_fraction(local: Vector3, aabb_min: Vector3,
		aabb_max: Vector3) -> Vector3:
	var size := aabb_max - aabb_min
	var fx := 0.0
	var fy := 0.0
	var fz := 0.0
	if absf(size.x) > 0.001:
		fx = (local.x - aabb_min.x) / size.x * 2.0 - 1.0
	if absf(size.y) > 0.001:
		fy = (local.y - aabb_min.y) / size.y
	if absf(size.z) > 0.001:
		fz = (local.z - aabb_min.z) / size.z * 2.0 - 1.0
	return Vector3(clampf(fx, -1.0, 1.0), clampf(fy, 0.0, 1.0),
		clampf(fz, -1.0, 1.0))


## Which zone contains a point, or -1 if none does.
##
## Zones overlap deliberately - a hit on the front corner should register on
## both the wing and the bumper - so this returns the best match by distance
## to the zone centre rather than the first that contains the point.
static func zone_at(fraction: Vector3) -> int:
	var best := -1
	var best_distance := INF
	for zone in ZONES:
		var box: Dictionary = ZONES[zone]
		var xr: Array = box["x"]
		var yr: Array = box["y"]
		var zr: Array = box["z"]
		# Malformed table entry: report it rather than indexing past the end.
		if xr.size() < 2 or yr.size() < 2 or zr.size() < 2:
			push_warning("DamageModel: zone %d has a bad range" % zone)
			continue
		if fraction.x < xr[0] or fraction.x > xr[1]:
			continue
		if fraction.y < yr[0] or fraction.y > yr[1]:
			continue
		if fraction.z < zr[0] or fraction.z > zr[1]:
			continue
		var centre := Vector3((xr[0] + xr[1]) * 0.5, (yr[0] + yr[1]) * 0.5,
			(zr[0] + zr[1]) * 0.5)
		var d := fraction.distance_to(centre)
		if d < best_distance:
			best_distance = d
			best = zone
	return best


## Which components an impact reaches, and how hard, as {part: 0..1}.
##
## Severity falls off with distance and is cut by the part's shield, so the
## same nose impact wrecks the radiator, hurts the engine and never touches
## the fuel tank.
static func parts_hit(fraction: Vector3, severity: float) -> Dictionary:
	var out := {}
	for part in PARTS:
		var info: Dictionary = PARTS[part]
		var pos: Array = info["pos"]
		if pos.size() < 3:
			push_warning("DamageModel: part %d has a bad position" % part)
			continue
		var at := Vector3(pos[0], pos[1], pos[2])
		var distance := fraction.distance_to(at)
		if distance > PART_REACH:
			continue
		# Linear falloff to the edge of reach, then squared so the centre of
		# an impact matters much more than its fringe.
		var reach := 1.0 - distance / PART_REACH
		var amount := reach * reach * severity
		amount *= 1.0 - float(info["shield"])
		amount *= float(info["fragility"])
		if amount > 0.001:
			out[part] = clampf(amount, 0.0, 1.0)
	return out


## True when a part is one of the four road wheels.
##
## This is a named function rather than an inline `part in [...]` because of
## a real parse error that reached the user:
##
##     Parse Error: Cannot infer the type of "is_wheel" variable because the
##     value doesn't have a set type.
##
## `in` compiles to Variant::OP_IN, and gdscript_analyzer.cpp:2956 gives a
## binary operation a VARIANT result type whenever either operand is a
## Variant - which the loop variable of `for x in some_dictionary` always is.
## `:=` then has nothing hard to infer from and the whole script fails to
## compile. A typed `-> bool` function has a set type by construction.
static func is_wheel(part: int) -> bool:
	return part == Part.WHEEL_LF or part == Part.WHEEL_RF \
		or part == Part.WHEEL_LR or part == Part.WHEEL_RR


## Energy of an impact in joules, from the impulse and the vehicle mass.
##
## An impulse J applied to mass m is a velocity change of J/m, and the energy
## that change represents is 0.5 * m * (J/m)^2 = J^2 / (2m). This is why a
## crash at twice the speed is four times as damaging: the energy the
## structure has to absorb goes as the square.
static func impact_energy(impulse: float, mass: float) -> float:
	if mass <= 0.001:
		return 0.0
	return impulse * impulse / (2.0 * mass)


## Severity 0..1 from the energy absorbed, against a reference.
##
## Uses a square root, because the DEPTH of a dent grows roughly with the
## square root of the energy that made it - crumple zones are designed to
## absorb progressively more force as they collapse further.
static func severity_from_energy(energy: float, reference: float) -> float:
	if reference <= 0.001:
		return 0.0
	return clampf(sqrt(energy / reference), 0.0, 1.0)
