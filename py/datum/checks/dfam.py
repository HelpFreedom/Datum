"""Печатность: что мешает напечатать деталь, а не собрать её.

Три вопроса, на которые геометрия отвечает честно и которых до сих пор не
было. Нависания, толщина стенки и габарит уже проверяются; здесь — то, что
рассуждает СЛОЯМИ.

ПЛОЩАДЬ ПРИЛИПАНИЯ. Первый слой держит всю деталь. Высокая деталь на пятачке
отрывается или её ведёт, и узнаётся это на третьем часу печати. Мерится
точно — сечением у самого дна.

ОБЪЁМ ПОДДЕРЖЕК. Слайсер их поставит, но сколько материала уйдёт и сколько
поверхности будет испорчено отрывом — видно только в слайсере, то есть уже
после того, как деталь нарисована. Считается по объёмному растру правилом 45°:
ячейка, под которой пусто и в слое ниже нет соседа в пределах досягаемости,
повисает, и под ней нужна опора до ближайшего материала или до стола.

ДЛИНА МОСТОВ. Пластик тянется через пустоту без опоры на три десятка
миллиметров, дальше провисает. Мерится как самый длинный непрерывный ряд
повисших ячеек.

ОРИЕНТАЦИЯ ОБЪЯВЛЯЕТСЯ, И БЕЗ НЕЁ ОТВЕТ НЕ ТОТ. Детали в Datum строятся в
СБОРОЧНОМ положении — так требуют проверки собираемости, — а печатаются в
другом. Проверено на дозаторе: флексура в сборочном положении стоит торчком и
требует поддержек вдвое больше собственного объёма, а шибер стоит на шейке и
получает 18 мм² прилипания. Оба числа верны про сборочное положение и
бессмысленны про печать. Поэтому у всех трёх проверок есть `up` — какая ось
детали смотрит вверх на столе.

Объявляется `up` У ДЕТАЛИ, `@part(print_up=...)`: проект подставит его во все
проверки печатности этой детали, а расхождение с `up`, заданным в проверке,
отвергнет, ещё не начав считать. Словарь осей — `datum.build.part.PRINT_UP`, и
тест держит его равным `_UPRIGHT` ниже.

ЭТИ ПРОВЕРКИ НЕ ВЫБИРАЮТ ОРИЕНТАЦИЮ, а считают объявленную. Соблазн взять ту,
где лучше одно число, — ошибка, и её здесь уже совершили: шибер дозатора был
объявлен перевёрнутым ради прилипания 484 мм² вместо 96, хотя оригинал
печатает его на ребре — и окно у него капля ровно под эту позу.

ЧЕГО ЭТИ ПРОВЕРКИ НЕ ЗНАЮТ, и это надо назвать. Они не знают о слайсере: ни о
его стратегии поддержек, ни о том, что мелочь он не подпирает вовсе. Объём
поддержек — это ответ ПОСЛОЙНОГО ПРАВИЛА 45°, а не то, что поставит слайсер:
древовидные поддержки обычно меньше, а сплошные под пологим сводом — больше
(см. `support_volume`).
"""

from __future__ import annotations

import math
import time

import numpy as np

from datum.checks.result import Artifact, CheckResult
from datum.geom.measure import bbox, section_xy
from datum.geom.solid import Solid
from datum.geom.voxel import VoxelGrid, voxelize

__all__ = ["bed_contact", "support_volume", "bridge_span", "overhanging", "bridge_cells"]

#: Шаг растра по умолчанию. Замерено на подставке из примеров (182×82×36):
#: сечения 135 мс, растр 52 мс. Мельче — дороже квадратично, а ответы на
#: поддержках меняются в пределах процента.
DEFAULT_STEP = 0.5


# Как повернуть деталь, чтобы названная ось смотрела вверх (проверено
# метками: rx=90 переводит +Y в +Z, ry=-90 переводит +X в +Z). Поза на столе определена в одном месте — `geom.xform.upright`: ей же
# пользуется выгрузка, и напечатано будет ровно то, что здесь проверено.
from datum.geom.xform import UPRIGHT as _UPRIGHT  # noqa: E402
from datum.geom.xform import upright as _oriented  # noqa: E402


def _grid(part: Solid, step: float, up: str) -> VoxelGrid:
    return voxelize(_oriented(part, up), step)


def overhanging(grid: VoxelGrid, limit_deg: float = 45.0) -> np.ndarray:
    """Маска ПОВИСШИХ ячеек: занято, а опереться не на что.

    Правило 45° на регулярной сетке: поверхность, отклонённая от вертикали на
    угол θ, уходит вбок на `шаг · tan θ` за слой. Значит ячейка держится, если
    в слое ниже есть занятая в пределах `tan θ` ячеек. При 45° это ровно одна
    соседняя — классическое правило, по которому живут все слайсеры.

    Зернистость тут есть, и врать о ней не стоит: досягаемость целая, поэтому
    при 45° и при 50° маска выйдет одинаковой. Для нависаний с углом полезнее
    `printing.overhang`, который считает по НОРМАЛЯМ граней и дробных градусов
    не теряет.
    """
    from scipy import ndimage

    if not (0.0 < limit_deg < 90.0):
        raise ValueError(f"предел нависания должен лежать в (0, 90), получено {limit_deg}")
    if limit_deg < 45.0:
        # Строже одной ячейки на слой растр не умеет: досягаемость целая, и
        # 30° раньше молча считались как 45° — с подписью «правило 30°» в
        # сообщении и недооценённым объёмом поддержек.
        raise ValueError(
            f"предел нависания {limit_deg:g}° строже 45°: на растре правило не бывает "
            f"строже одной ячейки на слой, и ответ был бы про 45°. Для такого "
            f"материала берите `overhang` — он считает по нормалям граней")
    reach = max(1, int(math.floor(math.tan(math.radians(limit_deg)))))
    filled = grid.filled
    hanging = np.zeros_like(filled)
    footprint = np.ones((2 * reach + 1, 2 * reach + 1), dtype=bool)
    for k in range(1, filled.shape[0]):
        support = ndimage.binary_dilation(filled[k - 1], structure=footprint)
        hanging[k] = filled[k] & ~support
    # Нижний слой лежит на столе — он не повисает никогда.
    return hanging


def _gap_below(grid: VoxelGrid) -> np.ndarray:
    """Сколько пустых ячеек под каждой — до ближайшего материала или стола."""
    filled = grid.filled
    nz = filled.shape[0]
    index = np.arange(nz)[:, None, None]
    marked = np.where(filled, index, -1)
    highest = np.maximum.accumulate(marked, axis=0)          # занятое на уровне k и ниже
    below = np.concatenate([np.full((1, *filled.shape[1:]), -1, dtype=highest.dtype),
                            highest[:-1]], axis=0)
    return index - below - 1


def bed_contact(part: Solid, *, min_area: float = 100.0, layer: float = 0.2,
                up: str = "+Z", id: str = "", name: str = "") -> CheckResult:
    """Площадь первого слоя: чем деталь держится за стол.

    Мерится сечением на половине высоты первого слоя — там, где он и лежит.
    Отдельно сообщается ДОЛЯ от габарита в плане: у детали, стоящей на трёх
    ножках, площадь мала законно, а у сплошной коробки малая доля означает,
    что она поставлена на ребро.
    """
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    part = _oriented(part, up)
    lo, hi = bbox(part)
    section = section_xy(part, float(lo[2]) + layer / 2)
    area = float(section.area)
    footprint = (hi[0] - lo[0]) * (hi[1] - lo[1])
    ok = area >= min_area
    return CheckResult(
        id=id or f"{name}.прилипание", title="Площадь прилипания", group="printing",
        status="pass" if ok else "fail",
        message=(f"{name}: первый слой {area:.1f} мм² {'≥' if ok else '<'} "
                 f"{min_area:g} — это {100 * area / max(footprint, 1e-9):.0f} % "
                 f"габарита в плане ({footprint:.0f} мм²), вверх {up}"
                 + ("" if ok else ". Высокую деталь на таком пятачке оторвёт или поведёт")),
        value=area, unit="мм²", op=">=", threshold=min_area,
        inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)


def support_volume(part: Solid, *, max_volume: float = float("inf"),
                   limit_deg: float = 45.0, step: float = DEFAULT_STEP,
                   up: str = "+Z", id: str = "", name: str = "") -> CheckResult:
    """Сколько материала уйдёт на поддержки — и сколько поверхности испортится.

    Считается по объёмному растру: под каждой повисшей ячейкой ставится опора
    до ближайшего материала снизу или до стола. Ответ — то, что требует
    ПОСЛОЙНОЕ правило 45°, и с шагом он сходится к нему снизу, ошибкой первого
    порядка по шагу.

    Сверху или снизу это от настоящих поддержек — зависит от формы, и путать
    тут нельзя. На плоском нависании правило подпирает всё, и древовидные
    поддержки выйдут меньше. На ПОЛОГОМ своде (уклон s < 1) каждый слой
    выступает за нижний больше, чем правило разрешает, но повисает только
    полоса, доля (1 − s) выступа; остальное держится на слое ниже. Слайсер,
    смыкающий щели между такими полосами, подпирает всю зону. Замер на
    горизонтальном отверстии Ø6 длиной 10: правило — 141 мм³ (растр даёт 100,
    121, 132 при шаге 0.25, 0.125, 0.0625), вся зона круче 45° — 231 мм³.

    Заодно сообщается площадь поверхности, к которой поддержки прилипнут: её
    придётся зачищать, и часто это важнее объёма материала.

    В оценку попадают и МОСТЫ: полоса, опёртая с обеих сторон, здесь выглядит
    повисшей, хотя слайсер пройдёт её без опор. Разделять их эта проверка не
    берётся — решение «мост или поддержка» принимает слайсер по своим
    настройкам. Смотрите `bridge_span`: если пролёты короткие, большая часть
    этого объёма не понадобится.
    """
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    grid = _grid(part, step, up)
    hanging = overhanging(grid, limit_deg)
    volume = float((_gap_below(grid)[hanging]).sum()) * grid.cell_volume
    touched = float(hanging.sum()) * grid.cell_area
    ok = volume <= max_volume
    share = 100 * volume / max(grid.volume, 1e-9)
    return CheckResult(
        id=id or f"{name}.поддержки", title="Объём поддержек", group="printing",
        status="pass" if ok else "fail",
        message=(f"{name}: поддержек {volume:.0f} мм³ {'≤' if ok else '>'} "
                 f"{max_volume:g} — это {share:.0f} % от объёма детали; "
                 f"зачищать {touched:.0f} мм² поверхности; сюда попали и МОСТЫ, "
                 f"которые слайсер пройдёт без опор — смотрите `bridge_span` "
                 f"(вверх {up}, растр {step:g} мм, правило {limit_deg:g}°)"),
        value=volume, unit="мм³", op="<=", threshold=max_volume,
        inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000,
        evidence=(Artifact(kind="json", caption="растр и поддержки",
                           data={"step_mm": step, "limit_deg": limit_deg, "up": up,
                                 "support_mm3": volume, "touched_mm2": touched,
                                 "part_mm3": grid.volume,
                                 "grid": list(grid.shape)}),))


#: Насколько далеко от опоры СВОЕГО слоя повисшая ячейка должна быть, чтобы
#: её печатали мостом. Ближе — это нависающая кромка, приклеенная к стене
#: сбоку: её ведёт периметр, и речь о ней у поддержек (правило 45°), а не у
#: мостов. Без этого губка корпуса latch — кольцо шириной 0.8 мм вокруг проёма
#: — считалась мостом в 35.5 мм: вдоль стороны ряд её ячеек «опёрт» с обоих
#: концов на углы, хотя поперёк это консоль в 0.8 мм, прилипшая к стене.
BRIDGE_ANCHOR_MM = 1.0


def _run_map(hanging: np.ndarray, solid: np.ndarray, axis: int) -> np.ndarray:
    """Длина опёртого с обеих сторон ряда через каждую ячейку; 0 — не мост."""
    h = np.moveaxis(hanging, axis, 0)
    s = np.moveaxis(solid, axis, 0)
    out = np.zeros(h.shape, dtype=np.int64)
    for i in range(h.shape[1]):
        column = h[:, i]
        if not column.any():
            continue
        padded = np.concatenate([[False], column, [False]])
        edges = np.diff(padded.astype(np.int8))
        for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            if a - 1 < 0 or b >= len(column):
                continue                         # ряд упёрся в край растра
            if s[a - 1, i] and not column[a - 1] and s[b, i] and not column[b]:
                out[a:b, i] = b - a
    return np.moveaxis(out, 0, axis)


def _layer_bridges(grid: VoxelGrid, hanging: np.ndarray, k: int,
                   anchor_mm: float) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]:
    """Слой k: какие повисшие ячейки дальше `anchor_mm` от опоры, и пролёты по X, Y."""
    from scipy import ndimage

    layer, solid = hanging[k], grid.filled[k]
    anchors = solid & ~layer
    far = ndimage.distance_transform_edt(~anchors) * grid.step > anchor_mm
    return far, (_run_map(layer, solid, 0), _run_map(layer, solid, 1))


def bridge_cells(grid: VoxelGrid, hanging: np.ndarray,
                 anchor_mm: float = BRIDGE_ANCHOR_MM) -> np.ndarray:
    """Ячейки мостов — те, по которым `bridge_span` меряет пролёт (для карты).

    Мост — повисшая ячейка дальше `anchor_mm` от опоры своего слоя на ряду,
    опёртом с обеих сторон хотя бы по одной оси. Остальные повисшие — консоли
    и нависания: им нужны поддержки.
    """
    out = np.zeros_like(hanging)
    for k in range(grid.layer_count()):
        if not hanging[k].any():
            continue
        far, (run_x, run_y) = _layer_bridges(grid, hanging, k, anchor_mm)
        out[k] = hanging[k] & far & ((run_x > 0) | (run_y > 0))
    return out


def _longest_bridge(grid: VoxelGrid, hanging: np.ndarray,
                    anchor_mm: float = BRIDGE_ANCHOR_MM) -> tuple[float, float, float]:
    """Самый длинный мост, и по каждой оси отдельно.

    НАПРАВЛЕНИЕ ВЫБИРАЕТ СЛАЙСЕР, и он выберет выгодное — поэтому у каждой
    СВЯЗНОЙ области берётся МЕНЬШИЙ из её пролётов по X и по Y. Брать меньшее
    по всей детали было бы неверно: в разных местах выгодны разные направления.

    В пролёт идут только ряды через ячейки дальше `anchor_mm` от опоры своего
    слоя (см. `BRIDGE_ANCHOR_MM`).
    """
    from scipy import ndimage

    best = best_x = best_y = 0
    for k in range(grid.layer_count()):
        layer = hanging[k]
        if not layer.any():
            continue
        far, runs = _layer_bridges(grid, hanging, k, anchor_mm)
        labels, count = ndimage.label(layer)
        for piece in range(1, count + 1):
            mask = (labels == piece) & far
            if not mask.any():
                continue                         # вся область — кромка у стены
            spans = []
            for axis, run in enumerate(runs):
                longest = int(run[mask].max())
                if longest:
                    spans.append(longest)
                    if axis == 0:
                        best_x = max(best_x, longest)
                    else:
                        best_y = max(best_y, longest)
            if spans:
                best = max(best, min(spans))
    return best * grid.step, best_x * grid.step, best_y * grid.step


def bridge_span(part: Solid, *, max_span: float = 30.0, step: float = DEFAULT_STEP,
                limit_deg: float = 45.0, up: str = "+Z",
                id: str = "", name: str = "") -> CheckResult:
    """Самый длинный мост — полоса материала, опёртая с двух сторон и висящая
    над пустотой между ними.

    Полоса, у которой опоры нет хотя бы с одного конца, мостом НЕ считается:
    это консоль, и о ней говорят поддержки. Направление печати моста выбирает
    слайсер, поэтому у каждой связной области берётся меньший из её пролётов.
    """
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    grid = _grid(part, step, up)
    hanging = overhanging(grid, limit_deg)
    span, along_x, along_y = _longest_bridge(grid, hanging)
    ok = span <= max_span
    return CheckResult(
        id=id or f"{name}.мосты", title="Длина мостов", group="printing",
        status="pass" if ok else "fail",
        message=(f"{name}: самый длинный мост {span:.1f} мм {'≤' if ok else '>'} "
                 f"{max_span:g} (по X до {along_x:.1f}, по Y до {along_y:.1f}; "
                 f"у каждой области берётся выгодное направление; вверх {up})"
                 + ("" if ok else ". Пластик на таком пролёте провиснет")),
        value=span, unit="мм", op="<=", threshold=max_span,
        inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)
