# Исследование физики авто в Godot 4.3 - Этап 1 BMW 1M

## Источники (обыск интернета)
- Godot Docs VehicleBody3D / VehicleWheel3D: `rokojori.com/en/labs/godot/docs/4.4/vehiclewheel3d-class`, `github.com/godotengine/godot/.../vehicle_body_3d.h`
- Reddit / Godot Forum: Fixing friction, anti-roll bars, center of mass low, wheel placement
- MoonBench blog: throttling, torque curve, RPM-based force
- Godot Forum beta VehicleBody3D: steering lerp, engine force per wheel

## Ключевые выводы для МАКСИМАЛЬНОЙ реалистичности

### 1. Center of Mass - самая важная вещь
> "The origin point of your VehicleBody3D will determine the center of gravity... To make the vehicle more grounded, the origin point is usually kept low, moving the CollisionShape3D and MeshInstance3D upwards."

Решение: `center_of_mass_mode = CUSTOM`, `center_of_mass = Vector3(0, -0.85, 0.25)` - чуть назад и очень низко.

### 2. Колеса
- VehicleWheel3D ноды должны стоять в точке, где колесо при ПОЛНОМ сжатии подвески (bottom out)
- `rest_length` опускает колесо в позицию покоя
- `radius` должен совпадать с визуальным мешем (0.34м для BMW)
- `friction_slip`: 1.0 = норм, старт с 3.0 и подстраивать. Задние чуть больше для RWD стабильности.
- `roll_influence`: 0.1-0.4, чем меньше - тем меньше крен, но слишком мало дает занос.

Настройки для BMW 1M:
- mass = 1570 kg (реальный вес)
- Front: rest 0.32, travel 0.36, stiffness 58, max_force 18000, damping 4.6/5.6, friction 2.9, roll 0.25
- Rear: rest 0.34, travel 0.38, stiffness 68, max_force 20000, damping 4.9/6.0, friction 3.1, roll 0.32
- Эти значения дают:
  - Падение с 4.2м: мягкий отскок подвески 2-3 колебания
  - При повороте на скорости - легкий крен, но не переворот

### 3. Две анимации

#### Анимация поворота (Steering)
- Передние колеса `use_as_steering = true`
- VehicleWheel3D.steering автоматически вращает ноду колеса вокруг Y
- Добавили Ackermann: внутреннее колесо поворачивает на 10% больше
- Плавный lerp `steering = move_toward(steering, target, steer_speed * delta)`

#### Анимация вращения (Spin)
- Задние колеса RWD с `engine_force`
- Вращение меша: `rpm / 60 * TAU * delta` вокруг оси X (ось колеса)
- direction -1 для forward
- Также визуальная подвеска: mesh.position.y = lerp к `-distance_to_ground`

Источники по проблеме вращения: Reddit "Wheel rotation problem with built-in vehicle nodes" - важно сбросить rotation объекта в Blender, иначе ориентация ломается. Мы фиксим поворотом меша на 90° вокруг Z: Transform (0,-1,0, 1,0,0, 0,0,1)

### 4. Анти-крен (Anti-roll bar)
Формула из форума:
```
force = (left_compression - right_compression) * anti_roll_stiffness
apply_force at wheel positions
```
Реализовали `_apply_anti_roll_axis` + `_get_wheel_compression` через raycast distance.

### 5. Downforce и прочее
- На скорости >2m/s применяем центральную силу вниз: `speed² * coeff`
- Angular damp повышается в воздухе до 2.0 для стабильности полета (гиро эффект)
- Traction control: если RPM задних >> expected_rpm, снижаем friction до 1.6 для дрифта, иначе возвращаем 3.0

### 6. Управление WASD
- InputMap: W=accelerate, S=brake/reverse, A/D=steer
- Brake распределение 65% фронт / 35% зад
- Handbrake SPACE = 160% на задние только
- Engine torque curve: `factor = 1 - rpm/max_rpm`, клип 0.15..1.0, буст на низких скоростях

### 7. Графика
- ProceduralSkyMaterial с top/horizon цветами, exposure 1.08, glow, fog
- DirectionalLight sun 14m высоко, shadow cascades 3, split 0.1/0.22/0.5, max dist 160
- Ground PlaneMesh 200x200 32 subdivs, StandardMaterial roughness 0.92
- Car body: StandardMaterial metallic 0.35, roughness 0.28, clearcoat 1.0 для BMW блеска

### 8. Сцена падения
- BMW заспавнен на y=4.2m, mass 1570kg падает с g=9.8, 120Hz physics ticks, 16 solver iterations
- Подвеска с travel 0.36m поглощает удар => реалистичный bounce 2-3 раза

## Что дальше (Stage 2+)
- Импорт FBX как отдельный кузов поверх Box (скрипт _try_load_fbx_body)
- Лес/горы/город - добавить HeightMap terrain + city meshes
- Звуки мотора, частиц дыма, следов шин
- Коробка передач
