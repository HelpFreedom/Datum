# Справочник API (генерируется)

Файл собирается из кода: `.venv/bin/python -m datum.tools.apiref --write`.
Руками не правится — тест сверяет его с кодом. Подпись — без аннотаций;
длины в мм, углы в градусах. Тело — `Solid`, неизменяемое:
`a + b` — объединение, `a - b` — вычитание, `a ^ b` — пересечение (не `&`).
Пакетные `union([…])`, `diff(a, […])` быстрее цепочки операторов.

Цилиндры и отверстия задаются ДИАМЕТРОМ и границами «от — до», а не высотой;
`box` — границами по осям, а не размером и центром. Профили — shapely
(`from shapely.geometry import box as sbox, Polygon`), выдавливаются
`extrude` / `slab` / `cup`.

## datum.geom

### Тела

- `box(x0, x1, y0, y1, z0, z1, *, name='')` — Параллелепипед по границам.
- `CIRC_N` = 72 — сегментов на полный оборот у цилиндров, конусов и шаров по умолчанию (`n=`)
- `cone_z(d0, d1, z0, z1, x=0.0, y=0.0, n=72, *, name='')` — Усечённый конус вдоль Z: диаметр d0 снизу, d1 сверху.
- `cyl_x(d, x0, x1, y=0.0, z=0.0, n=72, *, name='')` — Цилиндр вдоль X: диаметр d, от x0 до x1, ось через (y, z).
- `cyl_y(d, y0, y1, x=0.0, z=0.0, n=72, *, name='')` — Цилиндр вдоль Y: диаметр d, от y0 до y1, ось через (x, z).
- `cyl_z(d, z0, z1, x=0.0, y=0.0, n=72, *, name='')` — Цилиндр вдоль Z: диаметр d, от z0 до z1, центр в (x, y).
- `empty(name='')` — Пустое тело — нейтральный элемент объединения.
- `extrude(geom, z0, z1, *, name='', twist_deg=0.0, scale_top=(1.0, 1.0))` — Профиль в плоскости XY, выдавленный от z0 до z1.
- `prism_xy(pts, z0, z1, *, name='')` — Профиль [(x, y)], выдавленный по Z от z0 до z1.
- `prism_xz(pts, y0, y1, *, name='')` — Профиль [(x, z)], выдавленный по Y от y0 до y1.
- `prism_yz(pts, x0, x1, *, name='')` — Профиль [(y, z)], выдавленный по X от x0 до x1.
- `revolve_z(pts_rz, cx=0.0, cy=0.0, n=72, *, degrees=360.0, name='')` — Тело вращения вокруг вертикальной оси через (cx, cy). Профиль [(r, z)], r ≥ 0.
- `rounded_slab(geom, z0, z1, r_top=0.0, r_bot=0.0, steps=6, *, name='')` — Выдавливание профиля со скруглением верхней и нижней кромки.
- `sphere(d, x=0.0, y=0.0, z=0.0, n=72, *, name='')` — Шар диаметром d с центром в (x, y, z).

### Плоские профили (shapely)

- `cs(geom)` — shapely (Multi)Polygon → CrossSection. Дыры по чётности контуров.
- `ring_poly(d_out, d_in, *, cx=0.0, cy=0.0, n=72)` — Кольцо (шайба) в плане.
- `rings(geom)` — Разложить shapely (Multi)Polygon на кольца с правильной ориентацией.
- `rrect_poly(x0, y0, x1, y1, r, *, quad_segs=24)` — Скруглённый прямоугольник в плане.
- `slot_poly(x0, y0, x1, y1, width, *, quad_segs=24)` — Паз: отрезок, «раздутый» на половину ширины (скруглённые торцы).
- `teardrop_poly(d, *, cx=0.0, cy=0.0, nose='+y', n=72)` — Капля: окружность с носиком под 45°, печатаемая без поддержек лёжа.

### Элементы для печати

- `chamfer_corners(geom, d)` — Срезать выпуклые углы плоского профиля фаской: у прямого угла катет d.
- `cup(geom, z0, z1, wall, floor, *, name='')` — Открытый сверху стакан по профилю: стенки `wall` по контуру, дно `floor`.
- `hole_z(d, z0, z1, x=0.0, y=0.0, *, cbore=None, csk=None, n=72, name='')` — РЕЗЕЦ вертикального отверстия диаметром d от z0 до z1 (вычитается `diff`).
- `knurl(d, z0, z1, x=0.0, y=0.0, *, pitch=1.5, depth=None, angle=30.0, name='')` — Ручка с ромбической накаткой: диаметр d по вершинам, от z0 до z1.
- `round_corners(geom, r, *, convex=True, concave=True, quad_segs=16)` — Скруглить углы плоского профиля радиусом r — вертикальные рёбра выдавленного.
- `slab(geom, z0, z1, *, chamfer_bot=0.0, chamfer_top=0.0, fillet_bot=0.0, fillet_top=0.0, name='')` — Плоский профиль, выдавленный от z0 до z1, с фаской или скруглением кромок.
- `thread(d, pitch, z0, z1, x=0.0, y=0.0, *, clearance, internal=False, left=False, chamfer=True, n=90, name='')` — Метрическая резьба d×pitch от z0 до z1: винт или РЕЗЕЦ гайки.

### Надписи

- `text_poly(s, size, *, font='DejaVu Sans', halign='center', valign='center', spacing=0.0, line_gap=0.4, kerning=True)` — Надпись плоским профилем: высота ЗАГЛАВНОЙ буквы `size` мм.
- `text_z(s, size, z0, z1, x=0.0, y=0.0, *, font='DejaVu Sans', angle=0.0, halign='center', valign='center', spacing=0.0, kerning=True, name='')` — Надпись телом: профиль `text_poly`, повёрнутый на `angle`° вокруг (x, y) и выдавленный от z0 до z1. Прибавить к детали — выпуклая, вычесть — вдавленная. На боковую грань — `rotate` готового тела.

### Булевы операции

- `compose(parts, *, name='')` — Сложить несколько тел в одно БЕЗ булевой операции.
- `diff(a, parts, *, name='')` — Вычитание списка тел из a одной пакетной операцией.
- `hull(s)` — Выпуклая оболочка тела.
- `intersect(parts, *, name='')` — Пересечение. None пропускаются; пустое тело среди входов — пустой ответ.
- `split_by_plane(s, normal, offset)` — Разрезать тело плоскостью. Возвращает (над плоскостью, под плоскостью).
- `union(parts, *, name='')` — Объединение. None пропускаются, пустые тела ничего не добавляют.

### Перенос, поворот, размещение

- `align(s, which, to)` — Сдвинуть тело так, чтобы его точка `which` (как у `anchor`) встала в `to` — точку (x, y, z) или точку габарита другого тела (`anchor(основа, "+Z")`).
- `anchor(s, which='')` — Точка габарита тела по словам: «+Z» — центр верха, «-Z» — центр низа, «+X-Y» — середина ребра, «+X-Y+Z» — угол; пусто — центр габарита.
- `array_linear(s, n, dx=0.0, dy=0.0, dz=0.0)` — Линейный массив копий (включая исходную).
- `array_polar(s, n, *, axis='z', c=(0.0, 0.0, 0.0), degrees=360.0)` — Круговой массив копий вокруг оси через точку c.
- `centre_on(s, base, axes='xy')` — Выровнять центр габарита тела по центру другого тела на осях `axes`.
- `frame(origin=(0.0, 0.0, 0.0), *, rx=0.0, ry=0.0, rz=0.0)` — Локальная система координат как матрица 4×4.
- `mirror(s, normal)` — Зеркало относительно плоскости через начало координат с данной нормалью.
- `move(s, dx=0.0, dy=0.0, dz=0.0)` — Перенос на (dx, dy, dz), мм.
- `place(s, m)` — Поставить тело по матрице 4×4 из `frame`.
- `rot_about(s, axis, deg, c)` — Поворот вокруг оси ('x'|'y'|'z'), проходящей через точку c.
- `rotate(s, rx=0.0, ry=0.0, rz=0.0)` — Поворот вокруг осей, проходящих через начало координат (градусы).
- `scale(s, sx=1.0, sy=None, sz=None)` — Масштаб относительно начала координат; sy, sz по умолчанию равны sx.
- `stack(s, base, side='+Z', gap=0.0)` — Приставить тело к стороне `side` другого тела, по центру этой стороны.
- `upright(s, up)` — Повернуть деталь так, чтобы ось `up` (+Z, -Z, +X, -X, +Y, -Y) смотрела вверх.

### Измерения

- `area(x)` — Площадь поверхности, мм².
- `bbox(x)` — Габарит: ((xmin, ymin, zmin), (xmax, ymax, zmax)).
- `centre(x)` — Центр габаритного ящика (не центр масс).
- `com(x)` — Центр масс при однородной плотности — через trimesh.
- `contains(x, pts)` — Точки внутри тела. Через trimesh: у manifold3d нет массового теста.
- `extents(x)` — Размеры габарита по осям (dx, dy, dz).
- `inter_vol(a, b)` — Объём пересечения. Основной измеритель интерференции во всём корпусе.
- `man(x)` — Manifold тела (или сам Manifold) — для прямых вызовов manifold3d.
- `min_gap(a, b, search=2.0)` — Минимальный зазор. search ограничивает поиск — без него медленно.
- `occupancy(x, plane, const, u_range, v_range, step=1.0)` — Растр «внутри/снаружи» на сетке. Возвращает (маска, оси u, оси v).
- `occupancy_ascii(x, plane, const, u_range, v_range, step=1.0, fill='#', void='.')` — Карта занятости текстом — компактное «зрение» для агента.
- `ray_hit(x, origin, direction, max_dist=50.0, skip_eps=1e-06)` — Первое попадание луча: (расстояние_мм, точка, нормаль) или None.
- `ray_probe(x, origin, direction, max_dist=50.0)` — Расстояние в мм от точки до первой поверхности вдоль направления.
- `section_xy(x, z)` — Сечение горизонтальной плоскостью на высоте z.
- `section_xz(x, y)` — Сечение вертикальной плоскостью y = const. Координаты результата — (x, z).
- `section_yz(x, xv)` — Сечение вертикальной плоскостью x = const. Координаты результата — (y, z).
- `thickness_at(x, point, normal, max_dist=50.0)` — Толщина стенки в точке: луч внутрь тела вдоль −normal до выхода наружу.
- `vol(x)` — Объём, мм³.

### По скану: тень, выравнивание

- `align_long_axis(solid)` — Повернуть вокруг Z так, чтобы длинная сторона предмета легла вдоль X.
- `fit_extents(solid, size)` — Растянуть по осям до намеренного габарита. Возвращает тело и коэффициенты.
- `footprint(solid, z0=None, z1=None, *, clearance=0.0, simplify=0.0, keep_holes=False)` — Тень слоя предмета от `z0` до `z1` на плоскость XY.
- `min_square_angle(geom, resolution_deg=0.05)` — Поворот в плане, при котором описанный квадрат наименьший.

### Карман по вставке

- `insertion_pocket(solid, insert, *, clearance=0.0, layer=0.25, travel=None, name='')` — Карман, в который предмет входит по `insert` (направление ВСТАВКИ).

### Тело

- `Journal` — журнал операций тела: кортеж `OpRecord`, от первой к последней
- `OpRecord` — Одна операция в истории тела.
- `Solid` — Тело. Неизменяемое: любая операция возвращает новое.

### Топология и страж

- `analyze(man, *, sliver_mm3=0.01)` — Разобрать Manifold.
- `checkpoint(solid, expect=None, *, mode='checkpoint')` — Проверить тело в контрольной точке.
- `Expect` — Чего мы ждём от тела в контрольной точке.
- `GuardError` — Отказ стража. Несёт всё нужное для красной карточки в интерфейсе.
- `GuardMode` — Режим стража.
- `locate_failure(solid, expect)` — Найти операцию, на которой сломались инварианты.
- `topology_report(obj, *, sliver_mm3=0.01)` — Удобная обёртка: принимает и Manifold, и Trimesh, возвращает словарь.
- `TopologyReport` — Разбор тела на составляющие. Объёмы в мм³.

### Обмен с trimesh

- `from_trimesh(mesh)` — Trimesh → Manifold. Ошибку не прячем: `status()` расскажет о проблеме.
- `to_trimesh(man, *, process=False)` — Manifold → Trimesh.
- `vertices_faces(man)` — Вершины (float64, N×3) и треугольники (int64, M×3) из Manifold.

### Методы Solid

- `тело.name` — имя тела — им называется деталь в сообщениях проверок
- `тело.renamed(name)` — то же тело под другим именем
- `тело.digest()` — Отпечаток ГЕОМЕТРИИ. Форсирует расчёт — звать в контрольных точках.
- `тело.manifold` — Manifold из manifold3d — для того, чего в datum.geom нет
- `тело.to_trimesh()` — сетка trimesh — для анализа, не для построения

## datum.build — для скрипта детали

- `part(name, *, color='#9aa4b2', print_up='', expect=None, zones=None, printed=True, print=None)` — Объявить деталь. Возвращает исходную функцию — её можно звать и напрямую.
- `mechanism(name)` — Объявить механизм. Функция без аргументов возвращает `Mechanism`.
- `P.ИМЯ` — значение параметра проекта (`P.WALL`); только внутри сборки, чтение записывается в зависимости детали
- `bed()` — Стол принтера проекта (X, Y, Z), мм — `printer.bed` из datum.toml.
- `bed_centre()` — Центр стола в плане, мм — общий сдвиг для деталей узла, построенного вокруг нуля.
- `centre_on_bed(solid, dx=0.0, dy=0.0, *, drop=True)` — Поставить тело в центр стола (+ сдвиг dx, dy) и опустить на стол.
- `draft()` — Черновая ли это сборка: форма и пропорции за секунды, точность — потом.
- `imported(name, *, root=None)` — Чужая модель как `Solid`.
- `project_root()` — Корень проекта текущей сборки.

Служебное (скрипту не нужно): `BuildResult`, `ImportedError`, `MECHANISMS`, `MechanismDef`, `NoParamsError`, `PARTS`, `PartDef`, `active_params`, `build_part`, `clear_mechanisms`, `clear_parts`, `imported_names`, `use_params`, `use_project`.

## datum.sim — механизм для симуляции

Объявляется под `@mechanism`; что и зачем — `references/simulation.md`.

- `revolute(axis, origin, range, *, drive=None, follows=None, mesh_efficiency=1.0, friction=None, capacity=None)` — Поворот вокруг `axis` через точку `origin` (мм); `range` — [от, до] в градусах.
- `prismatic(axis, range, *, drive=None, follows=None, mesh_efficiency=1.0, friction=None, capacity=None)` — Сдвиг вдоль `axis`; `range` — [от, до] в мм. Остальное — как у `revolute`.
- `drive(limit, speed, *, accel=None, ramp_s=0.25, efficiency=1.0, back_efficiency=None, backlash=0.0, rotor_inertia=0.0, name='')` — Привод: предел момента (Н·м; у ползуна — силы, Н) и скорость (°/с; мм/с).
- `Mechanism` — Механизм: base="clamped" (основание закреплено) или "resting" (стоит на столе).
- `Mechanism.link(self, name, *, parent=None, joint=None)` — Звено: первое — основание (без родителя и сустава), остальные — с суставом к родителю.
- `Mechanism.payload(self, name, *, link, mass_g, at)` — Сосредоточенный груз на звене (банка в захвате, гиря на рычаге).
- `Mechanism.scenario(self, name, poses, *, hold_s=0.3, payloads=(), speed_scale=1.0)` — Сценарий: позы по порядку ({звено: положение}); первая — откуда начать.
- `Link.add(self, solid, *, part=None, mass_g=None, label='', frame=None)` — Тело звена.

## Чужие названия → наши

Ошибка импорта такого имени сама подскажет замену; здесь — всё сразу.

- `bolt` → резьба — thread(d, pitch, z0, z1, clearance=…)
- `center` → поставить по центру — centre_on(тело, база), на стол — centre_on_bed(тело)
- `center_on` → по центру другого тела — centre_on(тело, база)
- `chamfer` → фаска: кромок плиты — slab(профиль, z0, z1, chamfer_bot=c), вертикальных углов — chamfer_corners(профиль, c)
- `circular_pattern` → копии по кругу — array_polar(тело, n, axis='z', c=…)
- `combine` → объединение — union([a, b]) или a + b
- `convex_hull` → выпуклая оболочка — hull(тело)
- `counterbore` → отверстие с цековкой — hole_z(d, z0, z1, x, y, cbore=(D, глубина))
- `countersink` → отверстие с зенковкой — hole_z(d, z0, z1, x, y, csk=D)
- `cube` → параллелепипед — box(x0, x1, y0, y1, z0, z1): границы, а не размер
- `cuboid` → параллелепипед — box(x0, x1, y0, y1, z0, z1): границы, а не размер
- `cut` → вычитание — diff(a, [b, c]) или a - b
- `cylinder` → цилиндр — cyl_z(d, z0, z1, x, y) и cyl_x / cyl_y: d — ДИАМЕТР, а вдоль оси — от и до, а не высота
- `difference` → вычитание — diff(a, [b, c]) или a - b
- `emboss` → выпуклая надпись — union с text_z(…) на поверхности
- `engrave` → гравировка — diff(тело, [text_z(…)]) ниже поверхности
- `fillet` → скругление: кромок плиты — slab(профиль, z0, z1, fillet_top=r), вертикальных углов — round_corners(профиль, r)
- `fuse` → объединение — union([a, b]) или a + b
- `helix` → резьба — thread(d, pitch, z0, z1, clearance=…)
- `hole` → отверстие — резец hole_z(d, z0, z1, x, y), вычитается diff
- `hollow` → стакан со стенками и дном — cup(профиль, z0, z1, wall, floor)
- `intersection` → пересечение — intersect([a, b]) или a ^ b
- `knurling` → накатка — knurl(d, z0, z1, x, y, pitch=…)
- `linear_extrude` → выдавливание — extrude(профиль, z0, z1); с фаской или скруглением кромок — slab
- `linear_pattern` → ряд копий — array_linear(тело, n, dx, dy, dz)
- `minkowski` → общей суммы Минковского нет; карман под предмет по вставке — insertion_pocket(предмет, insert)
- `offset` → смещение профиля — shapely: профиль.buffer(d, join_style='mitre'); стенки по контуру — cup
- `place_on` → поставить на другое тело — stack(тело, база, '+Z', gap=…)
- `polar_array` → копии по кругу — array_polar(тело, n, axis='z', c=…)
- `polygon` → многоугольник — prism_xy([(x, y), …], z0, z1) или shapely Polygon и extrude
- `revolve` → тело вращения — revolve_z([(r, z), …], cx, cy)
- `rotate_extrude` → тело вращения — revolve_z([(r, z), …], cx, cy)
- `rounded_box` → скруглённая коробка — slab(round_corners(профиль, r), z0, z1, fillet_top=…)
- `screw` → резьба — thread(d, pitch, z0, z1, clearance=…)
- `shell` → стакан со стенками и дном — cup(профиль, z0, z1, wall, floor)
- `subtract` → вычитание — diff(a, [b, c]) или a - b
- `text` → надпись — text_z(строка, size, z0, z1, x, y, font=…); профилем — text_poly
- `text3d` → надпись — text_z(строка, size, z0, z1, x, y, font=…)
- `translate` → перенос — move(тело, dx, dy, dz)
- `translate_to` → точку габарита — в точку: align(тело, '+X-Y', (x, y, z))
- `translated` → перенос — move(тело, dx, dy, dz)
