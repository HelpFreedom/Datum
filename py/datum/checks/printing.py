"""Проверки печатности.

`under_part_violation` — порт `check4.under_part_violation`. Правило из NOTES.md
`phone-case-gears`: «за подвижной деталью корпуса быть не может: он лёг бы на
деталь и сварился». Деталь и корпус печатаются одновременно, уже сцепленными,
поэтому материал корпуса, оказавшийся НИЖЕ подвижной детали по направлению
печати и внутри её контура, спекается с ней намертво. Объёмная проверка этого
не видит — нужен именно контур детали, спроецированный вниз.

`min_wall` — размыкание сечения: область тоньше w исчезает при сжатии на w/2 и
обратном расширении. Так тонкие места находятся вместе с координатами, а не
одним числом «минимум по меша».
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Sequence

import numpy as np
from shapely.ops import unary_union

from datum.checks.result import Artifact, CheckResult
from datum.geom.measure import bbox, extents, inter_vol, section_xy, section_xz, section_yz, vol
from datum.geom.prims import extrude
from datum.geom.solid import Solid

__all__ = ["under_part_violation", "min_wall", "overhang", "bbox_fits"]

_SECTION = {"xy": section_xy, "xz": section_xz, "yz": section_yz}


def under_part_violation(fixed: Solid, moving: Solid, *, erode: float = 0.4,
                         z_levels: Sequence[float] | None = None,
                         zone_z: tuple[float, float] | None = None,
                         exclude: Iterable[Solid] = (), max_vol: float = 0.5,
                         id: str = "", fixed_name: str = "",
                         moving_name: str = "") -> CheckResult:
    """Материал корпуса под подвижной деталью в её же контуре.

    `erode` сжимает контур, чтобы не ловить материал у самых кромок (там он
    законен). `exclude` — тела, которые так и задуманы: оси, мосты, направляющие.
    """
    t0 = time.perf_counter()
    fixed_name = fixed_name or fixed.name or "корпус"
    moving_name = moving_name or moving.name or "деталь"

    lo, hi = bbox(moving)
    if z_levels is None and zone_z is None:
        return _under_part_swept(fixed, moving, erode=erode, exclude=exclude,
                                 max_vol=max_vol, id=id, fixed_name=fixed_name,
                                 moving_name=moving_name, t0=t0)
    if z_levels is None:
        z0, z1 = lo[2], hi[2]
        z_levels = (z0 + 0.3, (z0 + z1) / 2, z1 - 0.3)
    if zone_z is None:
        zone_z = (bbox(fixed)[0][2] - 1.0, lo[2] - 0.05)

    foot = unary_union([section_xy(moving, float(z)) for z in z_levels])
    inner = foot.buffer(-erode, join_style=2)     # mitre — как в оригинале
    if inner.is_empty:
        return CheckResult(
            id=id or f"{moving_name}.under_part", title="Корпус под подвижной деталью",
            group="printing", status="skip",
            message=f"{moving_name}: контур после сжатия на {erode} мм пуст — нечего проверять",
            inputs=(fixed_name, moving_name))

    if zone_z[1] <= zone_z[0]:
        return CheckResult(
            id=id or f"{moving_name}.under_part", title="Корпус под подвижной деталью",
            group="printing", status="pass",
            message=f"{moving_name}: под деталью нет высоты для материала",
            value=0.0, unit="мм³", op="<=", threshold=max_vol,
            inputs=(fixed_name, moving_name))

    zone = extrude(inner, zone_z[0], zone_z[1])
    bad = fixed.manifold ^ zone.manifold
    for e in exclude:
        bad = bad - (e.manifold if isinstance(e, Solid) else e)
    v = vol(bad)
    ok = v <= max_vol

    return CheckResult(
        id=id or f"{moving_name}.under_part", title="Корпус под подвижной деталью",
        group="printing", status="pass" if ok else "fail",
        message=(f"под {moving_name} материала корпуса {v:.2f} мм³ "
                 f"{'≤' if ok else '>'} {max_vol:.2f}"
                 + ("" if ok else " — при печати он ляжет на деталь и спечётся с ней")),
        value=v, unit="мм³", op="<=", threshold=max_vol,
        inputs=(fixed_name, moving_name),
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence=(Artifact(kind="json", caption="зона проверки",
                           data={"erode_mm": erode, "z_levels": [float(z) for z in z_levels],
                                 "zone_z": [float(zone_z[0]), float(zone_z[1])]}),))


#: Слоёв области под деталью — не больше (у скульптуры уровней вершин тысячи).
UNDER_LEVELS_MAX = 64


def _under_part_swept(fixed: Solid, moving: Solid, *, erode: float, exclude,
                      max_vol: float, id: str, fixed_name: str, moving_name: str,
                      t0: float) -> CheckResult:
    """Материал корпуса, НАД которым по вертикали есть подвижная деталь.

    Прежде контур детали брался из трёх срезов (низ, середина, верх) и
    опускался в одну зону ниже самой нижней точки детали: фланец между
    срезами выпадал из контура, а полка корпуса выше нижнего конца вала — из
    зоны. Ручка с фланцем над полкой (зазор 0.1 мм) давала «0.00 мм³» при
    настоящих сотнях (найдено обзором). Теперь — область под деталью
    послойно: на каждой высоте вершин детали h — тень её части выше h,
    опущенная от дна корпуса до h (приём `geom/sweep.py`). У плоскогранной
    детали тень меняется только на высотах вершин, и это точно; у
    скульптуры — не больше UNDER_LEVELS_MAX слоёв поровну.
    """
    from shapely.geometry import MultiPolygon, Polygon

    from datum.checks.motion import _vertices
    from datum.geom.footprint import footprint
    from datum.geom.booleans import union

    lo, hi = bbox(moving)
    floor = bbox(fixed)[0][2] - 1.0
    levels = np.unique(np.round(_vertices(moving)[:, 2], 4))
    if len(levels) > UNDER_LEVELS_MAX:
        levels = np.linspace(lo[2], hi[2], UNDER_LEVELS_MAX)
    slabs, gap = [], 0.05            # на 0.05 ниже уровня: сама деталь — не корпус
    for h in levels[:-1]:
        top = float(h) - gap
        if top <= floor:
            continue
        try:
            shadow = footprint(moving, z0=float(h), keep_holes=True)
        except ValueError:                       # выше h у детали ничего нет
            continue
        inner = shadow.buffer(-erode, join_style=2)
        if inner.is_empty:
            continue
        parts = list(inner.geoms) if isinstance(inner, MultiPolygon) else [inner]
        polys = [g for g in parts if isinstance(g, Polygon) and g.area > 1e-9]
        if polys:
            slabs.append(extrude(MultiPolygon(polys) if len(polys) > 1 else polys[0],
                                 floor, top))
    name = id or f"{moving_name}.under_part"
    if not slabs:
        return CheckResult(id=name, title="Корпус под подвижной деталью", group="printing",
                           status="skip",
                           message=f"{moving_name}: контур после сжатия на {erode} мм пуст — "
                                   f"нечего проверять", inputs=(fixed_name, moving_name))
    zone = union(slabs) if len(slabs) > 1 else slabs[0]
    bad = fixed.manifold ^ zone.manifold
    for e in exclude:
        bad = bad - (e.manifold if isinstance(e, Solid) else e)
    v = vol(bad)
    ok = v <= max_vol
    return CheckResult(
        id=name, title="Корпус под подвижной деталью", group="printing",
        status="pass" if ok else "fail",
        message=(f"под {moving_name} материала корпуса {v:.2f} мм³ "
                 f"{'≤' if ok else '>'} {max_vol:.2f}"
                 + ("" if ok else " — при печати он ляжет на деталь и спечётся с ней")),
        value=v, unit="мм³", op="<=", threshold=max_vol, inputs=(fixed_name, moving_name),
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence=(Artifact(kind="json", caption="зона проверки",
                           data={"erode_mm": erode, "levels": len(slabs),
                                 "floor_z": float(floor)}),))


#: Насколько тоньше `wall` щуп в `min_wall`: равенство — не нарушение.
WALL_EQUAL_TOL = 0.01


def thin_regions(part: Solid, wall: float, plane: str, positions):
    """Сечения и их части тоньше `wall` — одни на `min_wall` и карту печатности.

    Отдаёт (положение, сечение, потерянное) для каждого непустого сечения.
    Потерянное — то, что исчезает при размыкании щупом на сотую тоньше
    `wall` (см. `WALL_EQUAL_TOL`).
    """
    probe = max(wall - WALL_EQUAL_TOL, wall / 2)
    for a in positions:
        sec = _SECTION[plane](part, float(a))
        if sec.is_empty or sec.area < 1e-6:
            continue
        opened = sec.buffer(-probe / 2, join_style=1).buffer(probe / 2, join_style=1)
        yield float(a), sec, sec.difference(opened)


#: Уровней вершин не больше — режем посередине каждого промежутка между ними.
WALL_LEVELS_MAX = 400
#: Иначе — равномерно: не реже стольких срезов и не чаще слоя (как карта печатности).
WALL_UNIFORM_SLICES = 150
WALL_MIN_SPACING = 0.2


#: Тонкое засчитывается стенкой, только если держится по высоте не меньше этого.
WALL_PERSIST_MM = 1.0


def _persists(part: Solid, wall: float, plane: str, a: float, piece, a0: float,
              a1: float) -> bool:
    """Тонкое место — СТЕНКА, если в плане оно же есть и на полмиллиметра выше,
    и на полмиллиметра ниже (держится ≥ WALL_PERSIST_MM, около пяти слоёв).
    Иначе это КРОМКА — верх сужающегося борта, вершина ската: тонко там на
    двух-трёх слоях, и печатается это как обычно. Замер на `examples/spool`:
    верх борта гнезда тонок лишь в верхних 0.47 мм детали (39 мм² на z=19.72,
    сходит на нет к 20.19); частая нарезка без этого признака кричала бы на
    каждую такую кромку, а прежние 24 среза её просто не задевали. Паз,
    оставляющий стенку 0.6 мм на 3 мм высоты, — стенка, и он ловится.
    Цена признака: поясок тоньше миллиметра по высоте не ловится — сказано в
    `assumes` проверки."""
    half = WALL_PERSIST_MM / 2
    # Сначала — строго над и под (щуп wall/2), как было. Не нашлось — щуп шире
    # на `half`: наклонная стенка (до 45° — круче не печатается) на
    # полмиллиметра выше стоит на столько же в стороне, и узким щупом тонкий
    # наклонный лист выходил «кромкой» и не ловился вовсе. Но широкому щупу
    # мало коснуться ЧЕГО-ТО тонкого: у катушки (168 канавок) рядом с кромкой
    # борта всегда есть соседняя мелочь, и одно касание красило её ложно
    # (тест корпуса). Стенка — та, что на соседних уровнях тонка ПРИМЕРНО
    # ТОЙ ЖЕ площадью (не меньше половины): сечение стенки по высоте держится,
    # кромка сходит на нет.
    narrow = piece.buffer(wall / 2)
    wide = piece.buffer(wall / 2 + half)
    for b in (a - half, a + half):
        if b < a0 or b > a1:
            return False
        for _, _, lost in thin_regions(part, wall, plane, np.array([b])):
            if lost.is_empty:
                return False
            if lost.intersects(narrow):
                continue
            if lost.intersection(wide).area < 0.5 * piece.area:
                return False
    return True


def wall_levels(part: Solid, ax: int, a0: float, a1: float, samples: int) -> np.ndarray:
    """Где резать деталь, чтобы не пропустить тонкий поясок.

    Прежде — `samples` (24) срезов на всю высоту: паз, оставлявший стенку 0.6
    мм на полосе 3 мм, проскакивал между ними («0.00 мм² тонкого»), а карта
    печатности рядом показывала его красным (найдено обзором). У детали из
    плоских граней сечение меняется ТОЛЬКО на высотах её вершин, поэтому срез
    посередине каждого промежутка между ними не пропустит ни одного пояска —
    точно. Таких уровней у печатной детали единицы и десятки (замер по
    примерам: 2…289); у скульптуры их тысячи, и там режем равномерно — не
    реже `WALL_UNIFORM_SLICES` и не чаще `WALL_MIN_SPACING`, как карта
    печатности. Прежняя сетка остаётся сверх того.
    """
    from datum.checks.motion import _vertices

    pad = (a1 - a0) * 0.02
    grid = np.linspace(a0 + pad, a1 - pad, samples)
    coords = _vertices(part)[:, ax]
    levels = np.unique(np.round(coords, 4))
    levels = levels[(levels >= a0 - 1e-9) & (levels <= a1 + 1e-9)]
    if len(levels) <= WALL_LEVELS_MAX:
        extra = (levels[:-1] + levels[1:]) / 2
    else:
        spacing = max(WALL_MIN_SPACING, (a1 - a0) / WALL_UNIFORM_SLICES)
        extra = np.arange(a0 + spacing / 2, a1, spacing)
    return np.unique(np.concatenate([grid, extra]))


def min_wall(part: Solid, *, wall: float = 1.6, planes: Sequence[str] = ("xy",),
             samples: int = 24, max_lost_area: float = 5.0,
             exclude_zones: Sequence[Solid] = (), up: str = "+Z", id: str = "",
             name: str = "") -> CheckResult:
    """Тонкие стенки через размыкание сечений.

    Область тоньше `wall` исчезает при сжатии контура на wall/2 и обратном
    расширении. Но доля потерянной площади — ПЛОХОЙ признак: её одинаково дают и
    настоящая тонкая стенка, и законные тонкие кромки (кончики зубьев шестерён,
    фаски, шевроны).

    Замер на реальных деталях phone-case-gears v4 при требовании 1.6 мм:

        деталь     кусков потери  крупнейший кусок
        case            190          130.46 мм²   ← настоящее тонкое место
        stand_a          11           10.97 мм²   ← стоит посмотреть
        gear1           365            0.51 мм²   ← кончики зубьев, норма
        shutter          12            0.14 мм²   ← фаски, норма

    По доле площади все четыре выглядят одинаково подозрительно (1–9 %), а по
    КРУПНЕЙШЕМУ СВЯЗНОМУ КУСКУ разделяются на три порядка. Поэтому порог стоит
    на нём: стенка толщиной на 0.6 мм меньше нужной, тянущаяся 10 мм, даёт около
    6 мм² — отсюда значение по умолчанию.

    `exclude_zones` — зоны детали (`@part(zones=…)`), где тонко НАРОЧНО:
    упругий язычок. Их сечения вычитаются из потерянного, и сколько тонкого
    в них было — пишется в сообщении: обход не прячется. Прежде вместо этого
    поднимали `max_lost_area`, и порог переставал ловить настоящую беду.

    Режется деталь В ПОЗЕ ПЕЧАТИ (`up` — ось из `@part(print_up=...)`): «xy» —
    это плоскость СЛОЯ, как и на карте печатности. Прежде проверка резала
    деталь как построена, и у детали, печатаемой на боку, ловила тонкое не в тех
    плоскостях, где его печатают. Плоскость и положение в сообщении — тоже в
    позе печати.
    """
    from datum.geom.xform import upright

    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    pose = str(up).strip().upper()
    part = upright(part, pose)
    exclude_zones = [upright(z, pose).renamed(z.name) for z in exclude_zones]
    lo, hi = bbox(part)
    axis_of = {"xy": 2, "xz": 1, "yz": 0}
    # Стенка РОВНО в `wall` требование выполняет. Но размыкание ровно на wall/2
    # стягивает её в линию, и что останется — решает округление: замер дал
    # «проходит» у полосы 1.6 при wall 1.6 и «40 мм² тонкого» у 2.0 при 2.0.
    # Щуп на сотую тоньше — далеко ниже того, что различит принтер
    # (`thin_regions`).

    worst_area, worst_frac, worst_at, worst_plane, worst_pieces = 0.0, 0.0, None, "", 0
    total_checked = 0
    excluded = 0.0                    # наибольшее тонкое внутри зон — по всем сечениям
    edge_area, edge_at, edge_plane = 0.0, None, "xy"   # отброшенное как кромка (`_persists`)
    for plane in planes:
        ax = axis_of[plane]
        a0, a1 = lo[ax], hi[ax]
        if a1 - a0 < 1e-6:
            continue
        for a, sec, lost in thin_regions(part, wall, plane,
                                         wall_levels(part, ax, a0, a1, samples)):
            total_checked += 1
            if exclude_zones and not lost.is_empty:
                from shapely.ops import unary_union

                cut = unary_union([_SECTION[plane](z, a) for z in exclude_zones])
                if not cut.is_empty:
                    inside = lost.intersection(cut)
                    excluded = max(excluded, float(inside.area))
                    lost = lost.difference(cut)
            if lost.is_empty:
                continue
            pieces = list(lost.geoms) if hasattr(lost, "geoms") else [lost]
            # Куски — по убыванию площади, и стенкой считается ПЕРВЫЙ устойчивый
            # по высоте. Прежде судили только крупнейший: признан кромкой — и
            # сечение пропускалось целиком, вместе с настоящей тонкой стенкой
            # рядом (аудит 2026-11-21: стенка 0.3 мм давала fail 12 мм², а с
            # наклонным листом по соседству — pass).
            biggest = 0.0
            for piece in sorted(pieces, key=lambda g: -g.area):
                if piece.area <= max(worst_area, max_lost_area):
                    biggest = max(biggest, piece.area)   # мельче порога — как раньше
                    break
                if _persists(part, wall, plane, float(a), piece, a0, a1):
                    biggest = piece.area
                    break
                if piece.area > edge_area:
                    edge_area, edge_at, edge_plane = piece.area, float(a), plane
            if biggest > worst_area:
                worst_area = biggest
                worst_frac = lost.area / sec.area
                worst_at, worst_plane, worst_pieces = float(a), plane, len(pieces)

    if total_checked == 0:
        return CheckResult(id=id or f"{name}.min_wall", title="Тонкие стенки",
                           group="printing", status="skip",
                           message=f"{name}: сечения пусты, мерить нечего", inputs=(name,))

    ok = worst_area <= max_lost_area
    # Положение — по НОРМАЛИ к плоскости сечения: срез XY стоит на высоте z.
    # Раньше писалось «при xy=13.76», и читалось это как точка в плане.
    across = {"xy": "z", "xz": "y", "yz": "x"}
    where = (f" в сечении {worst_plane.upper()} на {across[worst_plane]}={worst_at:.2f}"
             if worst_at is not None else "")
    # Срез — в позе печати; `section` режет деталь как построена. Поза — чистый
    # поворот, так что слой на высоте c — плоскость поперёк оси print_up на ±c.
    # Без этого у детали с print_up="-Z" место «z=-17.77» `section` не находил
    # (найдено обзором): там пусто, а тонкое — на z=17.77.
    if worst_at is not None and worst_plane == "xy" and pose != "+Z":
        built_plane, sign = {"-Z": ("xy", -1), "+X": ("yz", 1), "-X": ("yz", -1),
                             "+Y": ("xz", 1), "-Y": ("xz", -1)}[pose]
        where += f" (как построена — section {built_plane} {sign * worst_at:.2f})"
    return CheckResult(
        id=id or f"{name}.min_wall", title="Тонкие стенки", group="printing",
        status="pass" if ok else "fail",
        message=(f"{name}: стенка {wall:g} мм — крупнейшее тонкое место "
                 f"{worst_area:.2f} мм²{where} "
                 f"{'≤' if ok else '>'} {max_lost_area:.2f} "
                 f"(всего теряется {100 * worst_frac:.1f} % площади сечения "
                 f"в {worst_pieces} кусках; слои печати, вверх {pose})" + _zones_note(exclude_zones, excluded, "тонкого (наибольшее в одном сечении)")
                 + (f"; кромкой (тоньше по высоте {WALL_PERSIST_MM:g} мм) не считается "
                    f"{edge_area:.2f} мм² на {across[edge_plane]}={edge_at:.2f}"
                    if edge_at is not None and ok else "")),
        value=worst_area, unit="мм²", op="<=", threshold=max_lost_area,
        inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000,
        evidence=(Artifact(kind="json", caption="худшее сечение",
                           data={"plane": worst_plane, "at": worst_at, "up": pose,
                                 "largest_thin_area_mm2": worst_area,
                                 "lost_fraction": worst_frac, "pieces": worst_pieces,
                                 "wall_mm": wall, "sections_checked": total_checked}),))


def _zones_note(zones, excluded: float, what: str, unit: str = "мм²") -> str:
    """«; без зон язычок: в них 20.1 мм² тонкого» — обход назван, а не спрятан."""
    if not zones:
        return ""
    names = ", ".join(z.name or "?" for z in zones)
    return f"; без зон {names}: в них {what} {excluded:.1f} {unit}"


def _up_vector(up) -> np.ndarray:
    """Ось вверх: имя оси из словаря печати или вектор в осях детали."""
    if isinstance(up, str):
        from datum.checks.dfam import _UPRIGHT

        key = up.strip().upper()
        if key not in _UPRIGHT:
            raise ValueError(f"ось вверх задаётся как +Z, -Z, +X, -X, +Y или -Y, "
                             f"получено {up!r}")
        v = np.zeros(3)
        v["XYZ".index(key[1])] = 1.0 if key[0] == "+" else -1.0
        return v
    v = np.asarray(up, dtype=float)
    n = float(np.linalg.norm(v)) if v.shape == (3,) else 0.0
    if not n > 0:
        raise ValueError(f"ось вверх должна быть ненулевым вектором из трёх чисел, получено {up!r}")
    return v / n


def overhang_faces(normals: np.ndarray, centroids: np.ndarray, up_v: np.ndarray,
                   limit_deg: float = 45.0, bed_eps: float = 0.2) -> np.ndarray:
    """Маска граней, нависающих круче `limit_deg`, — одна на проверку и карту.

    Грань нависает, если смотрит вниз круче предела: угол между её нормалью и
    «вниз» меньше (90° − limit). Грани первого слоя (ближе `bed_eps` к нижней
    точке) лежат на столе — это не нависание.
    """
    thr = math.cos(math.radians(90.0 - limit_deg))      # = sin(limit_deg)
    # «Круче» — строго. Грань РОВНО на пределе (фаска 45° у `slab`) после
    # округления вершин до float32 выходила чуть круче и шла в нависания: на
    # коротких рёбрах скруглённого угла (0.4 мм в 100 мм от нуля) угол гуляет
    # на ~2·10⁻⁵. Допуск 10⁻⁴ по косинусу — около 0.01°: настоящих нависаний
    # он не касается.
    bad = (-(normals @ up_v)) > thr + 1e-4
    heights = centroids @ up_v
    return bad & ~(heights <= heights.min() + bed_eps)


def overhang(part: Solid, *, limit_deg: float = 45.0, up=(0.0, 0.0, 1.0),
             max_frac: float = 0.15, bed_eps: float = 0.2,
             exclude_zones: Sequence[Solid] = (), id: str = "",
             name: str = "") -> CheckResult:
    """Нависания круче предела: площадь и доля от всей поверхности.

    Геометрия вопроса. Пусть β — угол между нормалью грани и направлением печати:
    верхняя горизонталь β=0°, вертикальная стенка β=90°, нижняя горизонталь
    β=180°. Поверхность отклонена от вертикали на (β − 90°), и поддержка нужна,
    когда это больше предела, то есть β > 90° + предел, что равносильно
    `n·up < −sin(предел)`.

    Нижняя грань, лежащая НА СТОЛЕ, поддержек не требует, поэтому грани в
    пределах `bed_eps` от нижней точки детали исключаются.

    `up` — ось детали, смотрящая вверх на столе: «+X» и т. п., как у остальных
    проверок печатности (и как `@part(print_up=...)`), или вектор — для
    наклонной печати, которую объявить у детали нельзя.

    `exclude_zones` — зоны детали, где нависание принято (грани с центром в
    зоне не считаются); исключённая площадь — в сообщении.
    """
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    mesh = part.to_trimesh()
    if len(mesh.faces) == 0:
        return CheckResult(id=id or f"{name}.overhang", title="Нависания",
                           group="printing", status="skip",
                           message=f"{name}: меш пуст", inputs=(name,))

    up_v = _up_vector(up)
    areas = np.asarray(mesh.area_faces, dtype=float)
    centres = np.asarray(mesh.triangles_center, dtype=float)
    bad = overhang_faces(np.asarray(mesh.face_normals, dtype=float), centres,
                         up_v, limit_deg, bed_eps)
    excluded = 0.0
    if exclude_zones and bad.any():
        from datum.geom.measure import contains

        idx = np.nonzero(bad)[0]
        hit = np.zeros(len(idx), dtype=bool)
        for zone in exclude_zones:
            hit |= contains(zone, centres[idx])
        excluded = float(areas[idx[hit]].sum())
        bad[idx[hit]] = False

    bad_area = float(areas[bad].sum())
    total = float(areas.sum())
    frac = bad_area / max(total, 1e-12)
    ok = frac <= max_frac

    return CheckResult(
        id=id or f"{name}.overhang", title="Нависания", group="printing",
        status="pass" if ok else "warn",
        message=(f"{name}: нависаний круче {limit_deg:g}° — {bad_area:.0f} мм² "
                 f"({100 * frac:.1f} % поверхности) {'≤' if ok else '>'} {100 * max_frac:.0f} %, "
                 f"вверх {up if isinstance(up, str) else tuple(round(float(c), 3) for c in up)}"
                 + ("" if ok else " — понадобятся поддержки или другая ориентация")
                 + _zones_note(exclude_zones, excluded, "нависаний", unit="мм²")),
        value=frac, unit="доля", op="<=", threshold=max_frac, severity="warn",
        inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000,
        evidence=(Artifact(kind="json", caption="нависания",
                           data={"limit_deg": limit_deg, "area_mm2": bad_area,
                                 "fraction": frac}),))


def bbox_fits(part: Solid, *, bed: tuple[float, float, float] = (256.0, 256.0, 256.0),
              margin: float = 2.0, up: str = "+Z", id: str = "",
              name: str = "") -> CheckResult:
    """Деталь помещается на стол. Допускается поворот на 90° вокруг Z.

    Габарит берётся в ПЕЧАТНОЙ позе (`up`): деталь, построенная стоймя и
    печатаемая на боку, упирается в стол другими размерами.
    """
    from datum.checks.dfam import _oriented

    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    e = extents(_oriented(part, up))
    bx, by, bz = (b - margin for b in bed)
    fits_direct = e[0] <= bx and e[1] <= by and e[2] <= bz
    fits_rot = e[1] <= bx and e[0] <= by and e[2] <= bz
    ok = fits_direct or fits_rot
    # Доля стола — по ЛУЧШЕЙ из двух укладок, как и вердикт. Раньше она
    # считалась только для прямой, и деталь, проходящая с поворотом, получала
    # pass при запасе −26 %: статус и число противоречили друг другу.
    if min(bx, by, bz) > 0:
        worst = min(max(e[0] / bx, e[1] / by), max(e[1] / bx, e[0] / by))
        worst = max(worst, e[2] / bz)
    else:
        worst = 9.9
    return CheckResult(
        id=id or f"{name}.bbox_fits", title="Помещается на стол", group="printing",
        status="pass" if ok else "fail",
        message=(f"{name}: габарит {e[0]:.1f}×{e[1]:.1f}×{e[2]:.1f} мм при столе "
                 f"{bed[0]:g}×{bed[1]:g}×{bed[2]:g} (запас {margin:g} мм), вверх {up} — "
                 + ("помещается" + (" с поворотом на 90°" if not fits_direct else "")
                    if ok else "НЕ ПОМЕЩАЕТСЯ")),
        value=worst, unit="доля стола", op="<=", threshold=1.0,
        inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)
