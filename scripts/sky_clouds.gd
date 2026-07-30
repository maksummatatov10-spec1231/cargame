class_name SkyClouds
extends Node

## Volumetric cloud sky shader.
##
## Godot's ProceduralSkyMaterial can only do a flat gradient, so the clouds are
## a custom [ShaderMaterial] on the [Sky]. It raymarches a flattened noise
## field, which gives clouds that have depth, catch the sun on the side facing
## it, and drift over time - rather than a scrolling texture.
##
## The shader runs at the sky's own resolution and is only evaluated for pixels
## where the sky is visible, so the cost is modest. `sky_quality` and the
## `MARCH_STEPS` constant trade quality for speed.
##
## Attach this to the WorldEnvironment; it replaces the sky material on ready.

const CLOUD_SHADER := """
shader_type sky;
render_mode use_half_res_pass;

uniform vec3 sky_top : source_color = vec3(0.13, 0.32, 0.68);
uniform vec3 sky_horizon : source_color = vec3(0.66, 0.78, 0.92);
uniform vec3 ground_colour : source_color = vec3(0.19, 0.21, 0.19);
uniform vec3 cloud_bright : source_color = vec3(1.0, 0.98, 0.95);
uniform vec3 cloud_dark : source_color = vec3(0.42, 0.46, 0.55);
uniform vec3 sun_tint : source_color = vec3(1.0, 0.88, 0.72);

uniform float coverage : hint_range(0.0, 1.0) = 0.46;
uniform float density : hint_range(0.0, 4.0) = 1.35;
uniform float cloud_height = 900.0;
uniform float cloud_thickness = 550.0;
uniform float wind_speed = 0.012;
uniform float detail = 1.0;

// Value noise. Cheap, tiles well enough at this scale, and does not need a
// texture to be shipped with the project.
// No sin() anywhere: fract/dot only. A transcendental per hash was a large
// part of why the sky was the most expensive thing on screen.
float hash(vec3 p) {
	p = fract(p * 0.3183099 + vec3(0.71, 0.113, 0.419));
	p *= 17.0;
	return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}

float noise(vec3 x) {
	vec3 i = floor(x);
	vec3 f = fract(x);
	f = f * f * (3.0 - 2.0 * f);
	return mix(
		mix(mix(hash(i + vec3(0,0,0)), hash(i + vec3(1,0,0)), f.x),
			mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
		mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
			mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
}

// Fractal brownian motion: the layered detail that makes clouds look billowy.
// Three octaves instead of five. The two finest ones were below the size of a
// pixel at cloud distance, so they cost a lot and showed almost nothing.
float fbm(vec3 p) {
	float v = 0.0;
	float a = 0.5;
	for (int i = 0; i < 3; i++) {
		v += a * noise(p);
		p = p * 2.02 + vec3(1.7, 0.9, 2.3);
		a *= 0.5;
	}
	return v;
}

// Density of cloud at a point. Shaped so there is a soft base and wispy tops.
float cloud_density(vec3 pos, float t) {
	// Vertical profile first: it is a couple of instructions and rejects most
	// samples outright, so the expensive noise never runs for them.
	float rel = clamp((pos.y - cloud_height) / cloud_thickness, 0.0, 1.0);
	float profile = smoothstep(0.0, 0.22, rel) * (1.0 - smoothstep(0.55, 1.0, rel));
	if (profile < 0.01) {
		return 0.0;
	}

	vec3 p = pos * 0.0016;
	p.xz += vec2(t * wind_speed, t * wind_speed * 0.6);

	float base = fbm(p);
	float shape = base - (1.0 - coverage);
	if (shape <= 0.0) {
		return 0.0;
	}
	// Erosion only matters where there is actually cloud, so it is behind the
	// early out rather than being paid for on every empty sample.
	shape -= fbm(p * 3.4) * 0.22 * detail;

	return clamp(shape, 0.0, 1.0) * profile * density;
}

// Budget, sized for a mid-range GPU.
//
// The first version marched 28 steps and took 4 more towards the sun at each
// one, calling a 5-octave fbm twice per sample: about 1400 noise lookups per
// sky pixel, or ~10 billion heavy operations per frame at 1080p. An RX 580 is
// a 5.8 TFLOPS card, so that alone could not hit 60 fps - it was the single
// biggest cost in the scene.
//
// 8 steps with a single light sample and a cheaper density function keeps the
// same look at about a fifteenth of the cost: 96 noise lookups per pixel
// instead of 1400. `use_half_res_pass` renders the sky at half resolution on
// top of that, which clouds are soft enough not to mind.
const int MARCH_STEPS = 8;
const int LIGHT_STEPS = 1;

void sky() {
	vec3 dir = EYEDIR;
	float t = TIME;

	// --- background gradient -------------------------------------------- //
	float up = clamp(dir.y, -1.0, 1.0);
	vec3 base = mix(sky_horizon, sky_top, pow(clamp(up, 0.0, 1.0), 0.45));
	if (up < 0.0) {
		base = mix(sky_horizon, ground_colour, pow(-up, 0.35));
	}

	vec3 sun_dir = normalize(LIGHT0_DIRECTION);
	float sun_dot = clamp(dot(dir, sun_dir), 0.0, 1.0);

	// Sun disc and the glow around it.
	float disc = smoothstep(0.9995, 0.99985, sun_dot);
	base += sun_tint * disc * 14.0;
	base += sun_tint * pow(sun_dot, 220.0) * 0.9;
	base += sun_tint * pow(sun_dot, 8.0) * 0.10;

	vec3 colour = base;

	// --- clouds ----------------------------------------------------------- //
	// Only march where the ray actually rises into the cloud slab.
	if (dir.y > 0.02) {
		float near_t = cloud_height / dir.y;
		float far_t = (cloud_height + cloud_thickness) / dir.y;
		far_t = min(far_t, near_t + 6000.0);

		float step_size = (far_t - near_t) / float(MARCH_STEPS);
		// Dither the start so banding turns into fine noise.
		float jitter = hash(vec3(FRAGCOORD.xy, t * 0.05));
		float travelled = near_t + step_size * jitter;

		float transmittance = 1.0;
		vec3 scattered = vec3(0.0);

		for (int i = 0; i < MARCH_STEPS; i++) {
			if (transmittance < 0.02) {
				break;
			}
			vec3 sample_pos = dir * travelled;
			float d = cloud_density(sample_pos, t);

			if (d > 0.001) {
				// A short march towards the sun gives the bright tops and dark
				// bases. Two steps is enough for that read; the original four
				// doubled the cost of every lit sample for a difference that
				// is not visible against a bright sky.
				float shadow = 0.0;
				for (int j = 1; j <= LIGHT_STEPS; j++) {
					vec3 lp = sample_pos + sun_dir * float(j) * 140.0;
					shadow += cloud_density(lp, t);
				}
				float light = exp(-shadow * 0.7);

				// Forward scattering: clouds glow when you look towards the sun.
				float phase = 0.5 + 1.4 * pow(sun_dot, 4.0);
				vec3 lit = mix(cloud_dark, cloud_bright, light) * (0.55 + 0.75 * light * phase);
				lit += sun_tint * light * pow(sun_dot, 6.0) * 0.5;

				float absorbed = d * step_size * 0.0016;
				scattered += lit * absorbed * transmittance;
				transmittance *= exp(-absorbed);
			}
			travelled += step_size;
		}

		colour = colour * transmittance + scattered;

		// Fade the clouds out towards the horizon so the slab has no visible
		// edge where it meets the sky.
		float horizon_fade = smoothstep(0.02, 0.22, dir.y);
		colour = mix(base, colour, horizon_fade);
	}

	COLOR = colour;
}
"""

## Cloud cover, 0 = clear, 1 = overcast.
@export_range(0.0, 1.0) var coverage := 0.46:
	set(value):
		coverage = value
		_apply()
## How solid the clouds are.
@export_range(0.0, 4.0) var density := 1.35:
	set(value):
		density = value
		_apply()
## Drift speed.
@export var wind_speed := 0.012:
	set(value):
		wind_speed = value
		_apply()

var _material: ShaderMaterial


func _ready() -> void:
	var env_node := get_parent() as WorldEnvironment
	if env_node == null or env_node.environment == null:
		push_warning("SkyClouds expects to be a child of a WorldEnvironment")
		return

	var shader := Shader.new()
	shader.code = CLOUD_SHADER
	_material = ShaderMaterial.new()
	_material.shader = shader

	var sky := Sky.new()
	sky.sky_material = _material
	# The clouds move, so the radiance has to be refreshed rather than baked
	# once - otherwise the lighting would not match the sky.
	sky.process_mode = Sky.PROCESS_MODE_REALTIME
	# A realtime sky only supports 256; anything else is silently overridden
	# and logs a warning on startup.
	sky.radiance_size = Sky.RADIANCE_SIZE_256

	env_node.environment.sky = sky
	env_node.environment.background_mode = Environment.BG_SKY
	_apply()


func _apply() -> void:
	if _material == null:
		return
	_material.set_shader_parameter("coverage", coverage)
	_material.set_shader_parameter("density", density)
	_material.set_shader_parameter("wind_speed", wind_speed)
