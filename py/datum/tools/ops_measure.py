"""Замеры и взгляд: расстояния, щуп, сечение, вид детали, след, карта занятости.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from datum.tools.ops_base import Plane, Project, _err, _guard, open_project
from datum.tools.registry import Arg, op


# ------------------------------------------------------------------ замеры
@op("measure_parts", cli_name="measure", group="замеры",
    isolate=True,
    title="Замер между деталями",
    summary=("Минимальный зазор и объём пересечения двух деталей. Основной способ "
             "ответить «влезет ли» и «не задевает ли»."))
async def measure_parts(
    project: Project,
    a: Annotated[str, Arg(help="первая деталь")],
    b: Annotated[str, Arg(help="вторая деталь")],
    search: Annotated[float, Arg(help="предел поиска зазора, мм")] = 4.0,
) -> dict[str, Any]:
    from datum.geom.measure import inter_vol, min_gap

    def run():
        # Ядро отвечает самим `search`, когда ближе ничего нет, — и при
        # search=1e-300 это ноль: «зазор 0.000 мм» у деталей в 0.3 мм друг от
        # друга. Поэтому поиск — не короче тысячной миллиметра, а «дальше не
        # искали» говорится словами и полем, а не числом, похожим на замер.
        if search < SEARCH_MIN_MM:
            return _err(f"search {search:g} мм — короче {SEARCH_MIN_MM:g} мм: так зазор "
                        f"не найти, а ответ вышел бы нулём. Умолчание — 4 мм")
        pr = open_project(project)
        built = pr.build([a, b])
        if not all(r.ok for r in built.values()):
            return _err("детали не собрались: "
                        + "; ".join(r.line() for r in built.values() if not r.ok))
        sa, sb = built[a].solid, built[b].solid
        gap = min_gap(sa, sb, search)
        inter = inter_vol(sa, sb)
        beyond = gap >= search
        return {"a": a, "b": b, "min_gap_mm": round(gap, 4), "gap_beyond_search": beyond,
                "intersection_mm3": round(inter, 4), "search_mm": search,
                "report": (f"{a} ↔ {b}: "
                           + (f"зазор не меньше {search:g} мм (дальше не искали — "
                              f"увеличьте search)" if beyond else f"зазор {gap:.3f} мм")
                           + f", пересечение {inter:.3f} мм³")}

    return _guard(run)


#: Короче — зазор не найти: ядро отвечает самим пределом, и тот неотличим от нуля.
SEARCH_MIN_MM = 1e-3


@op("measure_probe", cli_name="probe", group="замеры",
    isolate=True,
    title="Зонд: расстояние и толщина стенки",
    summary=("Луч из точки в заданном направлении: расстояние до поверхности и "
             "толщина стенки в месте попадания. Отвечает на вопрос «а сколько "
             "здесь миллиметров», который в окне возникает чаще всех."))
async def measure_probe(
    project: Project,
    part: Annotated[str, Arg(help="деталь")],
    point: Annotated[list[float], Arg(help="начало луча: x,y,z через запятую, мм")],
    direction: Annotated[list[float], Arg(help="направление луча: dx dy dz")],
    max_dist: Annotated[float, Arg(help="предел поиска, мм")] = 60.0,
    cone: Annotated[float, Arg(help="полуугол конуса уточнения, град; 0 — один луч")] = 6.0,
) -> dict[str, Any]:
    """Толщина меряется ПО КАНОНИЧЕСКОЙ ГЕОМЕТРИИ, а не по мешу в окне.

    У браузера на руках только GLB — тесселяция, годная, чтобы показать. Число,
    которое человек запишет в параметр, обязано совпадать с тем, что увидит
    проверка `min_wall`, а она считает здесь. Отсюда и разделение: точка-точка
    и угол считаются в окне мгновенно, а толщина — запросом сюда.

    Конус из дополнительных лучей берёт МЕСТНЫЙ МИНИМУМ толщины. На плоской
    стенке он ничего не меняет: косой луч проходит длиннее осевого, и минимумом
    остаётся осевой. Он нужен там, где тонкое место лежит не по нормали —
    у клина, у схода стенки на нет, у края кармана. Ровно это и ищет проверка
    `min_wall`, и зонд обязан отвечать тем же числом.
    """
    import numpy as np

    from datum.geom.measure import ray_hit

    def run():
        if len(point) != 3 or len(direction) != 3:
            return _err("точка и направление задаются тремя числами каждая")
        if not any(direction):
            return _err("направление луча нулевое — куда смотреть, не сказано")
        # Отрицательный предел давал «луч не встретил поверхности за -1 мм» —
        # фразу, похожую на замер. Конус — полуугол: от 0 (один луч) до 90.
        if max_dist <= 0:
            return _err(f"max_dist {max_dist:g} мм — предел поиска должен быть больше нуля")
        if not 0 <= cone < 90:
            return _err(f"cone {cone:g}° — полуугол конуса берётся от 0 (один луч) "
                        f"до 90°, не включая")
        pr = open_project(project)
        built = pr.build([part])
        result = built[part]
        if not result.ok:
            return _err(f"деталь не собралась: {result.line()}")
        solid = result.solid

        hit = ray_hit(solid, point, direction, max_dist=max_dist)
        if hit is None:
            return {"part": part, "hit": None,
                    "report": f"{part}: луч не встретил поверхности за {max_dist:g} мм"}

        distance, position, normal = hit
        thickness = _thickness_min(solid, position, normal, max_dist, cone)
        return {
            "part": part,
            "hit": {"distance_mm": round(distance, 4),
                    "point": [round(v, 4) for v in position],
                    "normal": [round(v, 4) for v in normal]},
            "thickness_mm": None if thickness is None else round(thickness, 4),
            "report": (f"{part}: до поверхности {distance:.3f} мм"
                       + ("" if thickness is None
                          else f", толщина стенки {thickness:.3f} мм")),
        }

    def _thickness_min(solid, position, normal, limit, half_angle):
        from datum.geom.measure import ray_probe

        n = np.asarray(normal, dtype=float)
        norm = float(np.linalg.norm(n))
        if norm == 0:                              # pragma: no cover
            return None
        n = n / norm

        # Опорные векторы, перпендикулярные нормали: берём наименее
        # сонаправленную ось, иначе на осевой нормали векторное произведение
        # выродилось бы в ноль.
        helper = np.eye(3)[int(np.argmin(np.abs(n)))]
        u = np.cross(n, helper)
        u /= float(np.linalg.norm(u))
        v = np.cross(n, u)

        best = ray_probe(solid, position, -n, max_dist=limit)
        if half_angle <= 0:
            return best
        angle = np.radians(half_angle)
        for k in range(8):
            phi = 2 * np.pi * k / 8
            offset = np.tan(angle) * (np.cos(phi) * u + np.sin(phi) * v)
            value = ray_probe(solid, position, -n + offset, max_dist=limit)
            if value is not None and (best is None or value < best):
                best = value
        return best

    return _guard(run)


def _ring(ring) -> list[list[float]]:
    """Кольцо shapely → список точек без повторной замыкающей."""
    points = [[round(float(x), 4), round(float(y), 4)] for x, y in ring.coords]
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


@op("section", group="замеры",
    isolate=True,
    title="Сечение детали",
    summary=("Сечение детали плоскостью: площадь, габариты, число контуров и дыр. "
             "Снаружи не видно ни затора в канале, ни тонкой перемычки — сечение "
             "показывает это сразу."))
async def section(
    project: Project,
    part: Annotated[str, Arg(help="имя детали")],
    plane: Annotated[Plane, Arg(help="плоскость: xy, xz или yz")],
    at: Annotated[float, Arg(help="координата плоскости, мм")],
    contours: Annotated[bool, Arg(help="вернуть сами контуры, а не только числа")] = False,
    image: Annotated[bool, Arg(help="нарисовать сечение в PNG (путь — в ответе, "
                                    "агенту — прямо в ответе)")] = False,
) -> dict[str, Any]:
    """Координаты контуров — в плоскости сечения, и отображение здесь такое:

        xy (плоскость z = at)  →  (x, y)
        xz (плоскость y = at)  →  (x, z)
        yz (плоскость x = at)  →  (y, z)

    Это не произвол, а то, что выверено на несимметричном теле в
    `test_measure.py`: сечение не должно оказаться ни зеркальным, ни
    перевёрнутым. Окно по этому отображению кладёт крышку среза обратно в
    пространство, и ошибка здесь сразу стала бы видна.
    """
    from datum.geom.measure import section_xy, section_xz, section_yz

    fns = {"xy": section_xy, "xz": section_xz, "yz": section_yz}
    if plane not in fns:
        return _err(f"плоскость должна быть xy, xz или yz, получено {plane!r}")

    def run():
        pr = open_project(project)
        built = pr.build([part])[part]
        if not built.ok:
            return _err(built.line())
        poly = fns[plane](built.solid, at)
        if poly.is_empty:
            return {"part": part, "plane": plane, "at": at, "empty": True,
                    "report": f"{part}: сечение {plane}={at:g} пустое"}
        geoms = list(poly.geoms) if hasattr(poly, "geoms") else [poly]
        holes = sum(len(g.interiors) for g in geoms)
        bounds = [round(v, 4) for v in poly.bounds]
        out: dict[str, Any] = {}
        if contours:
            # Точки НЕ упрощаются: окно заливает по ним крышку среза, и
            # показанный срез обязан быть тем же, что измеренный. Последняя
            # точка кольца совпадает с первой — её отбрасываем, замыкание
            # подразумевается.
            out["contours"] = [
                {"shell": _ring(g.exterior),
                 "holes": [_ring(h) for h in g.interiors]}
                for g in geoms
            ]
        report = (f"{part}, сечение {plane}={at:g}: площадь {poly.area:.2f} мм², "
                  f"контуров {len(geoms)}, дыр {holes}, границы {bounds}")
        if image:
            # Раньше картинка сечения рождалась только у проваленной проверки,
            # а по запросу — одни числа. Но «посмотреть внутрь» агент должен
            # ДО провала: затор в канале и тонкая перемычка видны глазом.
            from datum.viz.section import Layer, render_section

            png = render_section([Layer(geom=poly, label=part)], plane=plane, at=at,
                                 title=f"{part}: сечение {plane.upper()}",
                                 note=f"площадь {poly.area:.2f} мм², контуров "
                                      f"{len(geoms)}, дыр {holes}")
            digest = pr.cache.put_blob(png, "png", kind="section",
                                       meta={"part": part, "plane": plane, "at": at})
            blob = pr.cache.blob_path(digest, "png")
            path = (str(blob.relative_to(pr.root))
                    if blob is not None and blob.is_relative_to(pr.root) else str(blob))
            out["image"] = {"url": f"/obj/{digest}.png", "path": path}
            report += f"\nкартинка: {path}"
        return {"part": part, "plane": plane, "at": at, "empty": False,
                "area_mm2": round(poly.area, 4), "bounds": bounds,
                "pieces": len(geoms), "holes": holes, **out, "report": report}

    return _guard(run)



Pose = Literal["built", "print"]


@op("part_view", cli_name="view", group="замеры",
    isolate=True,
    title="Вид детали в картинке",
    summary=("Нарисовать деталь с четырёх сторон (изометрия, спереди, справа, сверху) "
             "в PNG — один масштаб на лист. Агенту картинка приходит в ответе, путь — там же. "
             "pose=print — в позе печати, как её выгрузит export. "
             "color_by=print — карта печатности в позе печати: нависания красным, мосты "
             "жёлтым, тонкие стенки синим (те же функции, что у проверок). "
             "style=render — со светом и тенью: видно форму и рельеф, зернистость. "
             "zoom=[x0,y0,z0,x1,y1,z1] — крупный план коробки; turntable — поворот "
             "(GIF для человека и лист из восьми ракурсов для Read)."))
async def part_view(
    project: Project,
    part: Annotated[str, Arg(help="имя детали")],
    pose: Annotated[Pose, Arg(help="built — как построена, print — как на столе")] = "built",
    color_by: Annotated[Literal["part", "print"],
                        Arg(help="part — цветом детали, print — карта печатности")] = "part",
    style: Annotated[Literal["technical", "render"],
                     Arg(help="technical — грани и рёбра, render — свет и тень")] = "technical",
    zoom: Annotated[list[float] | None,
                    Arg(help="крупный план: x0,y0,z0,x1,y1,z1, мм, в показанной позе; в "
                             "командной строке — одним словом через запятую и через "
                             "«=», если первое отрицательное: --zoom=-10,0,0,10,5,8",
                        metavar="X0,Y0,Z0,X1,Y1,Z1")] = None,
    turntable: Annotated[bool, Arg(help="поворот: GIF и лист восьми ракурсов")] = False,
) -> dict[str, Any]:
    """Взгляд, которого у агента без окна не было: объём виден глазом.

    Рисует свой растеризатор (`viz/render.py`) по той же канонической
    геометрии, что мерят проверки, — это не снимок окна.
    """
    def run():
        from datum.checks.printmap import CLASSES, PLAIN, colors, print_map
        from datum.geom.convert import vertices_faces
        from datum.io.export import on_bed
        from datum.viz.render import render_turntable, render_views

        if zoom is not None and len(zoom) != 6:
            return _err(f"zoom — шесть чисел x0 y0 z0 x1 y1 z1, получено {len(zoom)}")
        pr = open_project(project)
        built = pr.build([part])[part]
        if not built.ok:
            return _err(built.line())
        solid = built.solid
        up = pr.parts[part].print_up
        color = pr.parts[part].color or "#9aa4b2"
        shown = "print" if color_by == "print" else pose
        notes = []
        if shown == "print":
            solid = on_bed(solid, up, pr.bed)
            notes.append(f"в позе печати: {up} вверх" if up else
                         "поза печати НЕ объявлена — показано как построено")
        verts, faces = vertices_faces(solid.manifold)
        face_colors, legend, mapped = None, (), None
        if color_by == "print":
            from datum.io.export import bed_pose

            limits, zone_names, limit_notes = _print_limits(pr, part)
            notes += limit_notes
            pose_of = bed_pose(built.solid, up, pr.bed)
            zones = pr.zones(part) if any(zone_names.values()) else {}
            unknown = sorted({n for names in zone_names.values() for n in names} - set(zones))
            if unknown:
                return _err(f"проверки детали исключают зоны {unknown}, а у неё их нет; "
                            f"есть: {sorted(zones)}")
            mapped = print_map(
                solid, layer=float(pr.manifest.get("printer", {}).get("layer", 0.2)),
                exclude_overhang=[pose_of(zones[n]).renamed(n) for n in zone_names["overhang"]],
                exclude_thin=[pose_of(zones[n]).renamed(n) for n in zone_names["min_wall"]],
                **limits)
            verts, faces = mapped.verts, mapped.faces
            face_colors = colors(mapped, PLAIN)
            legend = (("без замечаний", PLAIN),) + tuple(
                (label, hex_) for label, hex_ in CLASSES.values())
            notes.append(mapped.summary())
            notes.append(f"пороги: нависание {limits['limit_deg']:g}°, стенка "
                         f"{limits['wall']:g} мм; тонкое — в слоях печати")
            notes += list(mapped.notes)
        frame = None if zoom is None else (tuple(zoom[:3]), tuple(zoom[3:]))
        note = "; ".join(notes)
        meta = {"part": part, "pose": shown, "color_by": color_by, "style": style}
        out: dict[str, Any] = {"part": part, "pose": shown, "print_up": up}
        if turntable:
            gif, sheet = render_turntable(verts, faces, title=part, color=color,
                                          face_colors=face_colors)
            gif_digest = pr.cache.put_blob(gif, "gif", kind="view", meta=meta)
            png, gif_path = sheet, _blob_rel(pr, gif_digest, "gif")
            out["animation"] = {"url": f"/obj/{gif_digest}.gif", "path": gif_path}
        else:
            # Карте нужен вид снизу: нависания и мосты смотрят вниз, и сверху и
            # сбоку их не видно вовсе.
            views = (("изометрия", "изометрия снизу", "спереди", "сверху")
                     if color_by == "print" else
                     ("изометрия", "спереди", "справа", "сверху"))
            cutaway = None
            if mapped is not None:
                # Нависание внутри (губка в коробке) снизу закрыто дном: вид снизу
                # без всего, что ниже самого нижнего нависания, — вместо «спереди».
                cut = _below_cut(mapped)
                if cut is not None:
                    views = ("изометрия", "изометрия снизу", "снизу в разрезе", "сверху")
                    cutaway = {"снизу в разрезе": cut}
            png = render_views(verts, faces, title=part, note=note, color=color,
                               face_colors=face_colors, legend=legend, style=style,
                               frame=frame, views=views, cutaway=cutaway)
        digest = pr.cache.put_blob(png, "png", kind="view", meta=meta)
        path = _blob_rel(pr, digest, "png")
        out["image"] = {"url": f"/obj/{digest}.png", "path": path}
        if mapped is not None:
            out["print_map"] = {"overhang_mm2": round(mapped.overhang_mm2, 1),
                                "bridge_mm2": round(mapped.bridge_mm2, 1),
                                "bridge_span_mm": round(mapped.bridge_span_mm, 2),
                                "thin_mm2": round(mapped.thin_mm2, 1),
                                "thin_piece_mm2": round(mapped.thin_piece_mm2, 2),
                                "thin_at": mapped.thin_at}
        what = "поворот (лист восьми ракурсов)" if turntable else "вид с четырёх сторон"
        lines = [f"{part}: {what}" + (f" — {note}" if note else ""), f"картинка: {path}"]
        if turntable:
            lines.append(f"анимация: {out['animation']['path']}")
        out["report"] = "\n".join(lines)
        return out

    return _guard(run)


def _blob_rel(pr, digest: str, ext: str) -> str:
    blob = pr.cache.blob_path(digest, ext)
    return (str(blob.relative_to(pr.root))
            if blob is not None and blob.is_relative_to(pr.root) else str(blob))


def _below_cut(mapped):
    """Грани не ниже самого нижнего нависания (минус полмиллиметра), или None.

    None — если срезать нечего: нависания у самого дна видны снизу и так.
    """
    import numpy as np

    tri = mapped.verts[mapped.faces]
    z = tri[:, :, 2].mean(axis=1)
    hanging = (mapped.labels == 1) | (mapped.labels == 2)
    if not hanging.any():
        return None
    level = float(z[hanging].min()) - 0.5
    keep = z >= level
    if keep.all():
        return None
    return keep


def _print_limits(pr, part: str) -> tuple[dict, dict, list[str]]:
    """Пороги карты — из проверок, объявленных у детали; нет их — умолчания.

    Карта, считающая по 45°, когда проект проверяет нависания по 50°,
    показывала бы не то, что проверено. Так же — зоны, которые проверки
    исключают (`exclude_zones`): по видам, отдельно. Аргументы разрешаются
    тем же `resolve_args`, что у проверки, — `${params.…}` и `${expr:…}`
    тоже; у нескольких объявлений с разными порогами берётся первое, и это
    сказано.
    """
    from datum.checks.library import LIBRARY, resolve_args

    limits = {"limit_deg": 45.0, "wall": 1.6}
    zones: dict[str, list[str]] = {"overhang": [], "min_wall": []}
    seen: dict[str, list[float]] = {"limit_deg": [], "wall": []}
    proxy = pr.params.proxy()
    for decl in pr.check_decls:
        raw = decl.get("args") or {}
        use = decl.get("use")
        if raw.get("part") != f"@{part}" or use not in zones:
            continue
        names = raw.get("exclude_zones") or []
        zones[use] += [names] if isinstance(names, str) else list(names)
        args = resolve_args({k: v for k, v in raw.items() if k != "exclude_zones"},
                            LIBRARY[use], proxy)
        key = "limit_deg" if use == "overhang" else "wall"
        if isinstance(args.get(key), (int, float)):
            seen[key].append(float(args[key]))
    notes = []
    for key, values in seen.items():
        if values:
            limits[key] = values[0]
            if len(set(values)) > 1:
                notes.append(f"{key}: у детали несколько проверок с разными порогами "
                             f"{sorted(set(values))} — взят {values[0]:g}")
    return limits, zones, notes


#: Зазор посадки на сторону — не больше метра (см. `footprint_op`).
FOOTPRINT_CLEARANCE_MAX_MM = 1000.0


@op("footprint", cli_name="footprint", group="замеры",
    isolate=True,
    title="Контур предмета в плане",
    summary=("Точная тень слоя детали или импортированной модели (скан предмета) на "
             "плоскость XY — контур для кармана-ложемента. С align предмет сначала "
             "ложится длинной осью вдоль X; в ответе площадь, габарит, поворот, "
             "при котором описанный квадрат наименьший, картинка и строка для "
             "скрипта детали."))
async def footprint_op(
    project: Project,
    part: Annotated[str, Arg(help="деталь проекта (или model)")] = "",
    model: Annotated[str, Arg(help="импортированная модель из imported/ (или part)")] = "",
    z_from: Annotated[float | None, Arg(help="низ слоя, мм; пусто — низ предмета")] = None,
    z_to: Annotated[float | None, Arg(help="верх слоя, мм; пусто — верх предмета")] = None,
    align: Annotated[bool, Arg(help="выровнять длинную ось вдоль X, низ на Z=0")] = False,
    clearance: Annotated[float, Arg(help="зазор посадки на сторону, мм")] = 0.0,
    simplify: Annotated[float, Arg(help="допуск упрощения контура, мм")] = 0.05,
    keep_holes: Annotated[bool, Arg(help="оставить дыры тени (по умолчанию заливаются)")] = False,
) -> dict[str, Any]:
    """Для ложемента под реальную вещь нужен её контур, а не габарит.

    z — в координатах тела; с `align` низ предмета на нуле, и слой «нижние
    19 мм» — это `z_to=19`. Числа те же, что даст `footprint(...)` в скрипте
    детали: в ответе готовая строка, так что точки не переписываются руками.
    """
    if bool(part) == bool(model):
        return _err("нужно одно из двух: part (деталь проекта) или model (из imported/)")
    # Отрицательные зазор и допуск прежде молча не значили ничего — ответ был
    # как при нуле, и агент думал, что задал натяг. Зазор больше метра — не
    # посадка: GEOS на таком падал своим английским «NaN/Inf numbers».
    if not 0 <= clearance <= FOOTPRINT_CLEARANCE_MAX_MM:
        return _err(f"clearance {clearance:g} мм — зазор посадки на сторону берётся от 0 "
                    f"до {FOOTPRINT_CLEARANCE_MAX_MM:g} мм")
    if simplify < 0:
        return _err(f"simplify {simplify:g} мм — допуск упрощения не бывает отрицательным; "
                    f"0 — не упрощать")

    def run():
        from datum.build.imported import imported
        from datum.geom.footprint import footprint, min_square_angle, align_long_axis
        from datum.viz.section import Layer, render_section

        pr = open_project(project)
        if part:
            built = pr.build([part])[part]
            if not built.ok:
                return _err(built.line())
            # Строки для скрипта у детали нет: скрипт детали не видит других
            # деталей проекта, и честной замены ей не существует.
            solid, source, call = built.solid, f"деталь {part}", None
        else:
            solid, source = imported(model, root=pr.root), f"модель {model}"
            call = f'imported("{model}")'
        angle = None
        if align:
            solid, angle = align_long_axis(solid)
            call = call and f"align_long_axis({call})[0]"
        from datum.geom.footprint import holes_of

        holes = holes_of(footprint(solid, z_from, z_to, keep_holes=True))
        shape = footprint(solid, z_from, z_to, clearance=clearance, simplify=simplify,
                          keep_holes=keep_holes)
        x0, y0, x1, y1 = shape.bounds
        square_angle, square_side = min_square_angle(shape)
        geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
        points = sum(len(g.exterior.coords) - 1 for g in geoms)
        layer = ("весь предмет" if z_from is None and z_to is None else
                 f"слой z {'низ' if z_from is None else f'{z_from:g}'}…"
                 f"{'верх' if z_to is None else f'{z_to:g}'} мм")
        png = render_section([Layer(geom=shape, label=source)], plane="xy",
                             where=f"тень: {layer}",
                             title=f"контур: {source}",
                             note=f"{layer}, зазор {clearance:g} мм, площадь {shape.area:.1f} мм²")
        digest = pr.cache.put_blob(png, "png", kind="footprint",
                                   meta={"source": source, "z": [z_from, z_to]})
        blob = pr.cache.blob_path(digest, "png")
        path = (str(blob.relative_to(pr.root))
                if blob is not None and blob.is_relative_to(pr.root) else str(blob))
        snippet = ""
        if call:
            args = [call] + [f"{k}={v!r}" for k, v in (("z0", z_from), ("z1", z_to))
                             if v is not None]
            if clearance:
                args.append(f"clearance={clearance!r}")
            if simplify:
                args.append(f"simplify={simplify!r}")
            if keep_holes:
                args.append("keep_holes=True")
            snippet = f"контур = footprint({', '.join(args)})"
        lines = [f"{source}, {layer}: площадь {shape.area:.1f} мм², габарит "
                 f"{x1 - x0:.2f}×{y1 - y0:.2f} мм, контуров {len(geoms)}, точек {points}"]
        if holes and not keep_holes:
            lines.append(f"  дыр в тени {holes} — залиты: карману нужен внешний контур "
                         f"(keep_holes — оставить)")
        if angle is not None:
            lines.append(f"  выровнено поворотом на {angle:+.2f}° вокруг Z")
        lines.append(f"  наименьший описанный квадрат {square_side:.2f} мм — при повороте "
                     f"на {square_angle:.2f}° (для раскладки по сетке)")
        if snippet:
            lines.append(f"  в скрипте детали: {snippet}  "
                         f"(from datum.build import imported; from datum.geom import *)")
        lines.append(f"картинка: {path}")
        return {"source": source, "area_mm2": round(shape.area, 3),
                "bounds": [round(v, 4) for v in (x0, y0, x1, y1)],
                "size_mm": [round(x1 - x0, 4), round(y1 - y0, 4)],
                "pieces": len(geoms), "points": points, "holes_filled": 0 if keep_holes else holes,
                "aligned_deg": angle, "min_square": {"angle_deg": round(square_angle, 3),
                                                     "side_mm": round(square_side, 3)},
                "snippet": snippet, "image": {"url": f"/obj/{digest}.png", "path": path},
                "report": "\n".join(lines)}

    return _guard(run)

@op("occupancy_map", cli_name="map", group="замеры",
    isolate=True,
    title="Карта занятости",
    summary=("Текстовая карта «внутри/снаружи» в сечении. Грубое, но быстрое средство "
             "увидеть, что где стоит, когда одного сечения мало."))
async def occupancy_map(
    project: Project,
    part: Annotated[str, Arg(help="имя детали")],
    plane: Annotated[Plane, Arg(help="плоскость: xy, xz или yz")],
    at: Annotated[float, Arg(help="координата плоскости, мм")],
    u_from: Annotated[float, Arg(help="начало первой оси")],
    u_to: Annotated[float, Arg(help="конец первой оси")],
    v_from: Annotated[float, Arg(help="начало второй оси")],
    v_to: Annotated[float, Arg(help="конец второй оси")],
    step: Annotated[float, Arg(help="шаг сетки, мм")] = 1.0,
) -> dict[str, Any]:
    from datum.geom.measure import occupancy_ascii

    def run():
        import math

        from datum.geom.measure import section_xy, section_xz, section_yz

        # Прежде: шаг 0 — ZeroDivisionError, отрицательный или перевёрнутый
        # участок — IndexError, шаг 1e-4 — MemoryError на 1.75 ТиБ. Отказ —
        # до работы и словами; ячеек — не больше, чем агент прочтёт.
        if step <= 0:
            return _err(f"step {step:g} мм — шаг сетки должен быть больше нуля")
        if u_from > u_to or v_from > v_to:
            return _err(f"участок задан наоборот: u {u_from:g}…{u_to:g}, "
                        f"v {v_from:g}…{v_to:g} — начало не больше конца")
        cells = ((u_to - u_from) / step + 1) * ((v_to - v_from) / step + 1)
        if not math.isfinite(cells) or cells > MAP_CELLS_MAX:
            return _err(f"карта вышла бы в {cells:.3g} ячеек при пределе "
                        f"{MAP_CELLS_MAX:,} — возьмите шаг крупнее или участок меньше"
                        .replace(",", " "))

        built = open_project(project).build([part])[part]
        if not built.ok:
            return _err(built.line())
        text = occupancy_ascii(built.solid, plane, at, (u_from, u_to),
                               (v_from, v_to), step)
        empty = "#" not in text

        # Пустая карта читается как «материала нет», хотя чаще означает, что шаг
        # сетки перескочил через тонкие стенки: узлы попадают мимо, а точка ровно
        # на грани внутренней не считается. Сверяемся с сечением той же плоскости.
        note = ""
        if empty:
            cut = {"XY": section_xy, "XZ": section_xz, "YZ": section_yz}.get(plane.upper())
            if cut is not None:
                poly = cut(built.solid, at)
                if not poly.is_empty and poly.area > 1e-9:
                    note = (f"\n\nВНИМАНИЕ: карта пуста, но сечение в этой плоскости "
                            f"имеет площадь {poly.area:.1f} мм². Шаг {step:g} мм слишком "
                            f"крупный — узлы сетки проходят мимо тонких стенок. "
                            f"Возьмите шаг мельче.")
        return {"map": text, "empty": empty, "coarse_step_warning": bool(note),
                "report": text + note}

    return _guard(run)


#: Больше ячеек карта не строит: 500×500 знаков — уже предел того, что
#: читают глазами (агент или человек), а за ним начиналась память в терабайтах.
MAP_CELLS_MAX = 250_000
