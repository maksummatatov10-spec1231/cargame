# CarGame - BMW 1M Realistic Physics - Stage 1

Godot 4.3 проект, максимально реалистичная физика машины.

## Что сделано (Этап 1)

### Физика MAX качества
- **VehicleBody3D** с массой 1570кг (реальный BMW 1M)
- Центр масс занижен до -0.85м для анти-опрокидывания
- Подвеска: front stiffness 58, rear 68, travel 0.36-0.38м, damping 4.6-6.0, max_force 18-20k N
- 120 Гц физика, 16 итераций солвера
- Анти-крен балки (anti-roll) реализованы через forces
- Даунфорс аэродинамика
- RWD привод, brake distribution 65/35

### 2 Анимации
1. **Поворот колес**: VehicleWheel3D.steering + Ackermann + плавный lerp
2. **Вращение колес**: mesh.rotate_x(rpm/60*TAU*delta) + синхронизация с контактом земли
3. Бонус: вертикальная анимация подвески - колеса телепортируются к земле + lerp

### Сцена
- Плоская местность 200x200 метров, Box коллизия + Plane mesh
- Машина спавнится в воздухе 4.2м и падает с реалистичным отскоком подвески
- Солнце DirectionalLight с каскадными тенями, Procedural Sky, Fog, Glow
- Камера 3-го лица сзади на SpringArm (collision aware, dynamic FOV от скорости)

### Управление
- WASD: W gas, S brake/reverse, A/D steer
- SPACE: handbrake (только зад)
- R: reset в воздух для теста подвески
- RMB + mouse: orbit камеры

## Запуск
1. Открыть проект в Godot 4.3 (Forward+)
2. Запустить scenes/main.tscn (назначена main)
3. Модель BMW из `ac-bmw-1m-free.zip` лежит в `assets/cars/bmw_1m/` - Godot сам импортит FBX, но используется fallback Box для макс совместимости

## Файлы
- `scenes/bmw_1m.tscn` - машина + 4 VehicleWheel3D + визуал колес
- `scenes/main.tscn` - 200x200 земля + солнце + небо
- `scripts/car_controller.gd` - вся физика, torque curve, анти-крен, анимации
- `scripts/follow_camera.gd` - third-person камера
- `DOCS_RESEARCH.md` - полное исследование по инету

## Источники реализованы
- Center of mass low (docs)
- Friction slip tuning
- Anti-roll via compression difference
- Engine force = throttle * max * (1 - rpm/max_rpm)
- Wheel mesh orientation fix (Blender rotation problem)

Stage 1 готов к расширению на леса/горы/город.
