@tool
class_name Terrain
extends StaticBody3D

## Procedural hilly terrain with per-surface driving properties.
##
## The heightfield is generated from layered value noise, the visible mesh is
## built from it, and the collision uses [HeightMapShape3D] - which samples the
## exact same array, so what you see is precisely what the wheels hit. A
## trimesh would also work but is far slower to query, and the wheel raycasts
## hit the ground four times per physics tick at 120 Hz.
##
## Each point also gets a surface type derived from slope and height:
##
##   grass  gentle ground              good grip
##   dirt   worn tracks and clearings  less grip, throws up dust
##   rock   steep slopes and peaks     hard, grippy, no particles
##
## The wheels query [method sample_surface] so grip, rolling resistance and the
## particle effects all change with what is under the car.

enum Surface { GRASS, DIRT, ROCK }

## Triplanar-ish terrain shader.
##
## Blends three surfaces from the vertex colour and breaks each one up with
## procedural detail, so a 400 m field of grass does not read as flat paint.
## Rock is blended in by slope as well, which keeps cliff faces stony even
## where the classifier called them grass.
const TERRAIN_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley, specular_schlick_ggx;

uniform vec3 grass_colour : source_color;
uniform vec3 grass_colour_dry : source_color;
uniform vec3 dirt_colour : source_color;
uniform vec3 rock_colour : source_color;
uniform float detail_scale = 0.35;

varying vec3 world_pos;
varying vec3 world_normal;
varying vec3 blend;

float hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float noise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	float a = hash(i);
	float b = hash(i + vec2(1.0, 0.0));
	float c = hash(i + vec2(0.0, 1.0));
	float d = hash(i + vec2(1.0, 1.0));
	return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

float fbm(vec2 p) {
	float v = 0.0;
	float a = 0.5;
	for (int i = 0; i < 4; i++) {
		v += a * noise(p);
		p *= 2.03;
		a *= 0.5;
	}
	return v;
}

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	world_normal = normalize((MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz);
	blend = COLOR.rgb;
}

void fragment() {
	// Steep ground is rock regardless of what the classifier said.
	float slope = 1.0 - clamp(world_normal.y, 0.0, 1.0);
	float rock_amount = clamp(blend.b + smoothstep(0.25, 0.55, slope), 0.0, 1.0);
	float dirt_amount = clamp(blend.r * (1.0 - rock_amount), 0.0, 1.0);
	float grass_amount = clamp(1.0 - rock_amount - dirt_amount, 0.0, 1.0);

	vec2 uv_big = world_pos.xz * detail_scale;
	vec2 uv_fine = world_pos.xz * detail_scale * 6.0;

	float n_big = fbm(uv_big);
	float n_fine = fbm(uv_fine);

	// Grass: two tones mixed by the coarse noise, speckled by the fine one.
	vec3 grass = mix(grass_colour, grass_colour_dry, n_big);
	grass *= 0.82 + 0.36 * n_fine;

	vec3 dirt = dirt_colour * (0.78 + 0.44 * n_fine);
	// Rock gets stratified banding from the height, like sedimentary layers.
	float band = 0.5 + 0.5 * sin(world_pos.y * 1.7 + n_big * 3.0);
	vec3 rock = rock_colour * (0.72 + 0.3 * band) * (0.85 + 0.3 * n_fine);

	vec3 albedo = grass * grass_amount + dirt * dirt_amount + rock * rock_amount;
	ALBEDO = albedo;

	// Grass is matte, rock a little sharper, wet dirt slightly glossy.
	ROUGHNESS = mix(0.93, 0.72, rock_amount) - 0.06 * dirt_amount;
	SPECULAR = 0.18 + 0.15 * rock_amount;

	// Cheap normal detail so the sun catches the ground texture.
	float e = 0.35;
	float hx = fbm(uv_fine + vec2(e, 0.0)) - fbm(uv_fine - vec2(e, 0.0));
	float hz = fbm(uv_fine + vec2(0.0, e)) - fbm(uv_fine - vec2(0.0, e));
	NORMAL_MAP = normalize(vec3(-hx, -hz, 1.0) * vec3(1.0, 1.0, 2.2)) * 0.5 + 0.5;
	NORMAL_MAP_DEPTH = 0.55 + 0.5 * grass_amount;
}
"""


## Physical size of the terrain in metres.
@export var size := 400.0
## Number of heightfield samples along each edge. 257 gives a 1.56 m grid over
## 400 m, which is fine enough for a car and cheap enough to collide against.
@export var resolution := 257
## Peak height of the hills in metres.
@export var height_scale := 34.0
## Size of the largest noise feature, in metres.
@export var feature_size := 190.0
## Reproducible layout.
@export var noise_seed := 20260730
## Radius around the origin kept flat, so the car has somewhere to spawn.
@export var flat_radius := 26.0
## How far beyond [member flat_radius] the ground blends into the hills.
@export var flat_falloff := 34.0

## Tick this in the editor to rebuild after changing anything above.
## The terrain is a @tool script, so the map is visible and editable in the
## editor rather than only existing once the game is running.
@export var rebuild := false:
	set(value):
		rebuild = false
		if is_inside_tree():
			build()

var heights := PackedFloat32Array()
var surfaces := PackedByteArray()

var _cell := 1.0
var _half := 0.0


func _ready() -> void:
	build()


## Generates the heightfield, collision and mesh. Safe to call repeatedly:
## the previously generated children are removed first.
func build() -> void:
	collision_layer = 1
	collision_mask = 1
	for child in get_children():
		if child.name in ["TerrainCollision", "TerrainMesh"]:
			remove_child(child)
			child.queue_free()
	_generate()
	_build_collision()
	_build_mesh()


# --------------------------------------------------------------------------- #
#  heightfield
# --------------------------------------------------------------------------- #

func _hash(x: int, y: int, salt: int) -> float:
	var n := x * 374761393 + y * 668265263 + (noise_seed + salt) * 1274126177
	n = (n ^ (n >> 13)) * 1274126177
	return float((n ^ (n >> 16)) & 0xFFFF) / 65535.0


## Smooth value noise: bilinear interpolation with a smoothstep fade, which is
## enough for terrain and avoids depending on FastNoiseLite's exact output.
func _value_noise(x: float, y: float, salt: int) -> float:
	var xi := floori(x)
	var yi := floori(y)
	var xf := x - xi
	var yf := y - yi
	xf = xf * xf * (3.0 - 2.0 * xf)
	yf = yf * yf * (3.0 - 2.0 * yf)
	var a := _hash(xi, yi, salt)
	var b := _hash(xi + 1, yi, salt)
	var c := _hash(xi, yi + 1, salt)
	var d := _hash(xi + 1, yi + 1, salt)
	return lerpf(lerpf(a, b, xf), lerpf(c, d, xf), yf)


## Fractal noise. Ridged octaves give the hills sharper crests than plain fbm,
## which reads much more like real terrain from a car.
func _fractal(wx: float, wz: float) -> float:
	var total := 0.0
	var amplitude := 1.0
	var frequency := 1.0 / feature_size
	var norm := 0.0
	for octave in 6:
		var v := _value_noise(wx * frequency, wz * frequency, octave * 17)
		if octave >= 2:
			# ridged: fold the noise so peaks form ridgelines
			v = 1.0 - absf(v * 2.0 - 1.0)
			v *= v
		total += v * amplitude
		norm += amplitude
		amplitude *= 0.5
		frequency *= 2.07          # not exactly 2, to avoid grid artefacts
	return total / maxf(norm, 0.001)


func _generate() -> void:
	_cell = size / float(resolution - 1)
	_half = size * 0.5
	heights.resize(resolution * resolution)
	surfaces.resize(resolution * resolution)

	for z in resolution:
		for x in resolution:
			var wx := x * _cell - _half
			var wz := z * _cell - _half
			var h := _fractal(wx, wz) * height_scale

			# Flatten the middle so the car spawns on level ground, easing out
			# rather than cutting a crater.
			var dist := sqrt(wx * wx + wz * wz)
			if dist < flat_radius + flat_falloff:
				var t := clampf((dist - flat_radius) / maxf(flat_falloff, 0.01), 0.0, 1.0)
				h *= t * t * (3.0 - 2.0 * t)

			heights[z * resolution + x] = h

	_classify_surfaces()


## Works out what each point is made of, from slope and altitude.
func _classify_surfaces() -> void:
	for z in resolution:
		for x in resolution:
			var i := z * resolution + x
			var slope := _slope_at(x, z)
			var h := heights[i]
			var s := Surface.GRASS
			if slope > 0.62:
				s = Surface.ROCK
			elif h > height_scale * 0.72:
				s = Surface.ROCK
			elif slope > 0.34:
				s = Surface.DIRT
			else:
				# Scattered dirt clearings, so the ground is not uniform grass.
				var patch := _value_noise(x * _cell / 55.0, z * _cell / 55.0, 909)
				if patch > 0.68:
					s = Surface.DIRT
			surfaces[i] = s


func _slope_at(x: int, z: int) -> float:
	var x0 := maxi(x - 1, 0)
	var x1 := mini(x + 1, resolution - 1)
	var z0 := maxi(z - 1, 0)
	var z1 := mini(z + 1, resolution - 1)
	var dx := (heights[z * resolution + x1] - heights[z * resolution + x0]) \
		/ maxf((x1 - x0) * _cell, 0.001)
	var dz := (heights[z1 * resolution + x] - heights[z0 * resolution + x]) \
		/ maxf((z1 - z0) * _cell, 0.001)
	return sqrt(dx * dx + dz * dz)


# --------------------------------------------------------------------------- #
#  sampling (used by the wheels and by anything being placed on the ground)
# --------------------------------------------------------------------------- #

## Terrain height at a world position, bilinearly interpolated so it matches
## the collision shape rather than snapping to the grid.
func sample_height(wx: float, wz: float) -> float:
	var fx := clampf((wx + _half) / _cell, 0.0, resolution - 1.001)
	var fz := clampf((wz + _half) / _cell, 0.0, resolution - 1.001)
	var x0 := int(fx)
	var z0 := int(fz)
	var tx := fx - x0
	var tz := fz - z0
	var x1 := mini(x0 + 1, resolution - 1)
	var z1 := mini(z0 + 1, resolution - 1)
	var h00 := heights[z0 * resolution + x0]
	var h10 := heights[z0 * resolution + x1]
	var h01 := heights[z1 * resolution + x0]
	var h11 := heights[z1 * resolution + x1]
	return lerpf(lerpf(h00, h10, tx), lerpf(h01, h11, tx), tz)


## Surface type at a world position.
func sample_surface(wx: float, wz: float) -> int:
	var x := int(round(clampf((wx + _half) / _cell, 0.0, resolution - 1.0)))
	var z := int(round(clampf((wz + _half) / _cell, 0.0, resolution - 1.0)))
	return surfaces[z * resolution + x]


## Surface normal at a world position, for placing props flat on the ground.
func sample_normal(wx: float, wz: float) -> Vector3:
	var e := _cell
	var hl := sample_height(wx - e, wz)
	var hr := sample_height(wx + e, wz)
	var hd := sample_height(wx, wz - e)
	var hu := sample_height(wx, wz + e)
	return Vector3(hl - hr, 2.0 * e, hd - hu).normalized()


## Steepness at a world position, 0 = flat.
func sample_slope(wx: float, wz: float) -> float:
	return 1.0 - sample_normal(wx, wz).dot(Vector3.UP)


# --------------------------------------------------------------------------- #
#  collision and mesh
# --------------------------------------------------------------------------- #

func _build_collision() -> void:
	var shape := HeightMapShape3D.new()
	shape.map_width = resolution
	shape.map_depth = resolution
	shape.map_data = heights

	var col := CollisionShape3D.new()
	col.shape = shape
	# HeightMapShape3D is a unit grid centred on the origin, so it has to be
	# scaled up to the real cell size. Getting this wrong is the classic way to
	# end up with collision that does not line up with the visible ground.
	col.scale = Vector3(_cell, 1.0, _cell)
	col.name = "TerrainCollision"
	add_child(col)
	# Owned by the scene so it is visible and selectable in the editor.
	if Engine.is_editor_hint() and get_tree() != null:
		col.owner = get_tree().edited_scene_root

	var phys := PhysicsMaterial.new()
	phys.friction = 1.0
	phys.rough = true
	phys.bounce = 0.0
	physics_material_override = phys


func _build_mesh() -> void:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	# SurfaceTool only includes a channel if it was set before the *first*
	# vertex: set_color() bails out with an error once `first` is false and the
	# COLOR bit is not already in the format. Miss that and every vertex colour
	# is silently dropped, COLOR arrives at the shader as white, and the blend
	# reads 1.0 rock everywhere - which is the flat grey ground.
	st.set_color(Color.WHITE)
	st.set_normal(Vector3.UP)
	st.set_uv(Vector2.ZERO)

	# Vertex colours carry the surface blend, so one material can paint grass,
	# dirt and rock without needing a splat texture set.
	for z in resolution:
		for x in resolution:
			var wx := x * _cell - _half
			var wz := z * _cell - _half
			var h := heights[z * resolution + x]
			var n := sample_normal(wx, wz)
			st.set_normal(n)
			st.set_uv(Vector2(x, z) * 0.5)
			st.set_color(_surface_colour(x, z))
			st.add_vertex(Vector3(wx, h, wz))

	for z in resolution - 1:
		for x in resolution - 1:
			var i := z * resolution + x
			st.add_index(i)
			st.add_index(i + resolution)
			st.add_index(i + 1)
			st.add_index(i + 1)
			st.add_index(i + resolution)
			st.add_index(i + resolution + 1)

	st.generate_tangents()
	var mesh := MeshInstance3D.new()
	mesh.name = "TerrainMesh"
	mesh.mesh = st.commit()
	mesh.material_override = _make_material()
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	mesh.gi_mode = GeometryInstance3D.GI_MODE_STATIC
	add_child(mesh)
	if Engine.is_editor_hint() and get_tree() != null:
		mesh.owner = get_tree().edited_scene_root


## Encodes the surface mix into a vertex colour: red = dirt, green = grass,
## blue = rock. Averaged over the neighbours so the transitions are soft.
func _surface_colour(x: int, z: int) -> Color:
	var grass := 0.0
	var dirt := 0.0
	var rock := 0.0
	for dz in range(-1, 2):
		for dx in range(-1, 2):
			var sx := clampi(x + dx, 0, resolution - 1)
			var sz := clampi(z + dz, 0, resolution - 1)
			match surfaces[sz * resolution + sx]:
				Surface.GRASS: grass += 1.0
				Surface.DIRT: dirt += 1.0
				_: rock += 1.0
	var total := grass + dirt + rock
	return Color(dirt / total, grass / total, rock / total, 1.0)


func _make_material() -> ShaderMaterial:
	var shader := Shader.new()
	shader.code = TERRAIN_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = shader
	mat.set_shader_parameter("grass_colour", Color(0.26, 0.38, 0.16))
	mat.set_shader_parameter("grass_colour_dry", Color(0.44, 0.47, 0.22))
	mat.set_shader_parameter("dirt_colour", Color(0.34, 0.26, 0.17))
	mat.set_shader_parameter("rock_colour", Color(0.36, 0.35, 0.34))
	mat.set_shader_parameter("detail_scale", 0.35)
	return mat
