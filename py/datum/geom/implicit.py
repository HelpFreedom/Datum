"""Органика: неявные поверхности и гладкие сопряжения — претензия №8.

Булевы операции над мешами дают форму, но не дают ПЕРЕХОДА. Всё, что сложнее
фаски, в них приходится собирать из фасок и лестниц, а настоящее скругление
между двумя поверхностями выразить нечем. В корпусе это и вышло боком:
декоративные массивы `spool-base` пришлось резать на меше, потому что OCCT на
сотнях мелких граней давал трещины, — а скруглений там не было вовсе.

Здесь форма задаётся ФУНКЦИЕЙ РАССТОЯНИЯ, а меш строится по ней разом.
Сопряжение тогда — не операция над гранями, а арифметика над функциями, и
трескаться там нечему.

СОГЛАШЕНИЕ О ЗНАКЕ: функция ПОЛОЖИТЕЛЬНА ВНУТРИ тела. Так устроен
`Manifold.level_set`, и заводить своё соглашение ради красоты значило бы
приглашать ошибку знака на каждом стыке. Объединение — максимум, пересечение —
минимум, дополнение — минус.

ЧЕСТНО О ТОЧНОСТИ. Минимум и максимум дают не расстояние, а ОЦЕНКУ СНИЗУ по
модулю: у объединения двух шаров вблизи стыка функция меньше истинного
расстояния. Для построения меша этого достаточно (уровень нуля тот же), а вот
сопряжение по такой функции получается тем менее точным, чем дальше она от
настоящего расстояния. Поэтому сопрягать стоит ПРИМИТИВЫ, а не результаты
длинных цепочек.

РАДИУС СОПРЯЖЕНИЯ ЗНАЧИТ РОВНО ТО, ЧТО НАПИСАНО, и это проверено числом.
Скругление вертикального ребра бруска радиусом r убирает ровно
r²·(1 − π/4)·h материала; замер при r=3, h=20 (точно 38.6283 мм³):

    шаг меша 1.00 мм → 40.99 (+6.10 %)
    шаг меша 0.50 мм → 39.15 (+1.34 %)
    шаг меша 0.25 мм → 38.77 (+0.36 %)

Расхождение — это ошибка МЕША, а не формулы: оно убывает вместе с шагом.
Отсюда и правило: `mesh_sdf` умеет померить её сам, построив меш дважды.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Проверки «наименьший радиус кривизны» нет, хотя для
органики она напрашивается. `Manifold.calculate_curvature` даёт кривизну по
вершинам, и на ИДЕАЛЬНОМ ШАРЕ она смещена — причём смещение не уходит с
измельчением меша:

    Ø24, точная средняя кривизна 0.1667
      медиана    0.1714  (+3 %)
      90 %       0.2109  (+27 %)
      99 %       0.2760  (+66 %)
      максимум   0.3333  (+100 %)

Те же проценты при вдвое более мелком меше и при втрое меньшем шаре, то есть
это свойство способа, а не разрешения. Калибровать поправку на шарах и
применять к произвольной форме — соблазн, который в этом проекте уже трижды
не пережил замера. Радиус кривизны меряется ненадёжно, и проверки, которая
делает вид, что надёжно, здесь не будет.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from datum.geom.solid import Solid

__all__ = ["SDF", "sphere_sdf", "box_sdf", "cyl_z_sdf", "plane_sdf",
           "union_sdf", "intersect_sdf", "diff_sdf", "invert_sdf",
           "blend_union", "blend_intersect", "shell_sdf", "offset_sdf",
           "mesh_sdf", "MeshQuality"]

#: Функция расстояния: (x, y, z) → знаковое расстояние, ПОЛОЖИТЕЛЬНОЕ ВНУТРИ.
SDF = Callable[[float, float, float], float]


# ------------------------------------------------------------------ примитивы
def sphere_sdf(d: float, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> SDF:
    """Шар диаметра `d` с центром в (x, y, z). Точное расстояние."""
    if d <= 0:
        raise ValueError(f"диаметр должен быть положительным, получено {d}")
    r = d / 2

    def f(px: float, py: float, pz: float) -> float:
        return r - math.sqrt((px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2)

    return f


def plane_sdf(normal: Sequence[float], distance: float) -> SDF:
    """Полупространство n·p ≤ distance. Точное расстояние."""
    nx, ny, nz = (float(v) for v in normal)
    norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if norm == 0:
        raise ValueError("нормаль не может быть нулевой")
    nx, ny, nz = nx / norm, ny / norm, nz / norm

    def f(px: float, py: float, pz: float) -> float:
        return distance - (nx * px + ny * py + nz * pz)

    return f


def box_sdf(x0: float, x1: float, y0: float, y1: float,
            z0: float, z1: float) -> SDF:
    """Параллелепипед по границам. ТОЧНОЕ расстояние, включая углы.

    Пересечение шести полупространств дало бы верный уровень нуля, но неверное
    расстояние СНАРУЖИ у рёбер и углов — а именно оно и нужно сопряжениям.
    """
    if x1 <= x0 or y1 <= y0 or z1 <= z0:
        raise ValueError(f"границы должны возрастать, получено "
                         f"({x0},{x1}) ({y0},{y1}) ({z0},{z1})")
    cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
    hx, hy, hz = (x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2

    def f(px: float, py: float, pz: float) -> float:
        dx, dy, dz = abs(px - cx) - hx, abs(py - cy) - hy, abs(pz - cz) - hz
        outside = math.sqrt(max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2 + max(dz, 0.0) ** 2)
        inside = min(max(dx, dy, dz), 0.0)
        return -(outside + inside)

    return f


def cyl_z_sdf(d: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0) -> SDF:
    """Цилиндр вдоль Z: диаметр d, от z0 до z1. Точное расстояние."""
    if d <= 0 or z1 <= z0:
        raise ValueError(f"диаметр и высота должны быть положительными, "
                         f"получено d={d}, z={z0}…{z1}")
    r, cz, hz = d / 2, (z0 + z1) / 2, (z1 - z0) / 2

    def f(px: float, py: float, pz: float) -> float:
        dr = math.hypot(px - x, py - y) - r
        dz = abs(pz - cz) - hz
        outside = math.sqrt(max(dr, 0.0) ** 2 + max(dz, 0.0) ** 2)
        inside = min(max(dr, dz), 0.0)
        return -(outside + inside)

    return f


# ------------------------------------------------------------------ сочетания
def union_sdf(*fs: SDF) -> SDF:
    if not fs:
        raise ValueError("объединять нечего")
    return lambda x, y, z: max(f(x, y, z) for f in fs)


def intersect_sdf(*fs: SDF) -> SDF:
    if not fs:
        raise ValueError("пересекать нечего")
    return lambda x, y, z: min(f(x, y, z) for f in fs)


def invert_sdf(f: SDF) -> SDF:
    """Дополнение: внутри становится снаружи."""
    return lambda x, y, z: -f(x, y, z)


def diff_sdf(a: SDF, *fs: SDF) -> SDF:
    """Разность: из `a` вычитается всё остальное."""
    if not fs:
        return a
    return lambda x, y, z: min(a(x, y, z), *(-f(x, y, z) for f in fs))


def offset_sdf(f: SDF, delta: float) -> SDF:
    """Раздуть (delta > 0) или ужать (delta < 0) на `delta` миллиметров.

    Ради этого и нужна ТОЧНАЯ функция расстояния: по оценке снизу такой сдвиг
    исказил бы форму тем сильнее, чем дальше оценка от истины.
    """
    return lambda x, y, z: f(x, y, z) + delta


def shell_sdf(f: SDF, thickness: float) -> SDF:
    """Оболочка толщины `thickness` по поверхности тела."""
    if thickness <= 0:
        raise ValueError(f"толщина должна быть положительной, получено {thickness}")
    half = thickness / 2
    return lambda x, y, z: half - abs(f(x, y, z))


def blend_union(a: SDF, b: SDF, radius: float) -> SDF:
    """Объединение с ВЫПУКЛЫМ сопряжением радиуса `radius`.

    Дуга получается ровно заданного радиуса, когда поверхности сходятся под
    прямым углом; при других углах это по-прежнему дуга того же радиуса,
    касательная к обеим поверхностям, но её протяжённость иная.
    """
    if radius < 0:
        raise ValueError(f"радиус не может быть отрицательным, получено {radius}")
    if radius == 0:
        return union_sdf(a, b)
    inner = blend_intersect(invert_sdf(a), invert_sdf(b), radius)
    return invert_sdf(inner)


def blend_intersect(a: SDF, b: SDF, radius: float) -> SDF:
    """Пересечение с ВОГНУТЫМ сопряжением (галтелью) радиуса `radius`."""
    if radius < 0:
        raise ValueError(f"радиус не может быть отрицательным, получено {radius}")
    if radius == 0:
        return intersect_sdf(a, b)

    def f(x: float, y: float, z: float) -> float:
        av, bv = a(x, y, z), b(x, y, z)
        ua, ub = radius - av, radius - bv
        if ua <= 0 or ub <= 0:
            # Хотя бы одна поверхность дальше радиуса — сопрягать нечего.
            return min(av, bv)
        return radius - math.hypot(ua, ub)

    return f


# ------------------------------------------------------------------ в мир тел
class MeshQuality:
    """Насколько меш согласен сам с собой при измельчении.

    Единственная честная мера точности неявной поверхности: построить её
    дважды и посмотреть, насколько сдвинулся объём. Теоретический порядок
    подставлять сюда нельзя — он верен для гладкой поверхности, а у детали
    бывают рёбра.
    """

    __slots__ = ("coarse_volume", "volume", "edge", "coarse_edge", "ms")

    def __init__(self, coarse_volume: float, volume: float,
                 coarse_edge: float, edge: float, ms: float):
        self.coarse_volume = coarse_volume
        self.volume = volume
        self.coarse_edge = coarse_edge
        self.edge = edge
        self.ms = ms

    @property
    def change(self) -> float:
        """Относительное изменение объёма при измельчении вдвое."""
        return abs(self.volume - self.coarse_volume) / max(abs(self.volume), 1e-30)

    def line(self) -> str:
        return (f"объём {self.coarse_volume:.3f} (шаг {self.coarse_edge:g}) → "
                f"{self.volume:.3f} (шаг {self.edge:g}), "
                f"сдвиг {self.change * 100:.2f} %, {self.ms:.0f} мс")


def mesh_sdf(f: SDF, bounds: Sequence[float], edge: float, *,
             name: str = "", level: float = 0.0,
             quality: bool = False,
             allow_split: bool = False) -> Solid | tuple[Solid, MeshQuality]:
    """Построить тело по функции расстояния.

    `bounds` — коробка поиска (x0, y0, z0, x1, y1, z1). Тело обязано лежать
    внутри неё целиком: всё, что вышло за границу, будет срезано ПЛОСКОСТЬЮ,
    и заметить это по самому мешу потом трудно.

    `edge` — целевая длина ребра. Это главный рычаг «точность против времени»:
    замер на шаре Ø24 дал погрешность объёма 0.19 % при шаге 1 мм (35 мс),
    0.05 % при 0.5 мм (167 мс) и 0.01 % при 0.25 мм (1.4 с).

    `quality=True` строит меш ещё и вдвое грубее и возвращает меру согласия.
    Вдвое дороже, зато число вместо надежды.

    ТЕЛО ТОНЬШЕ ШАГА РАССЫПАЕТСЯ, и это отказ, а не результат. Замер на
    оболочке Ø24 со стенкой 0.3 мм: при шаге 1 мм — 68 кусков и объём 72 вместо
    543, при 0.5 — 138 кусков; при шаге 0.1 (у шара поменьше) — одно тело, как
    и задумано. Раньше такое тело уходило в сборку молча, и страж потом
    говорил «деталь распалась», не называя причины. Если тел несколько по
    замыслу, передайте `allow_split=True`.
    """
    import time

    from manifold3d import Manifold

    if len(bounds) != 6:
        raise ValueError(f"границы — шесть чисел (x0,y0,z0,x1,y1,z1), "
                         f"получено {len(bounds)}")
    if edge <= 0:
        raise ValueError(f"шаг меша должен быть положительным, получено {edge}")
    lo = tuple(float(v) for v in bounds[:3])
    hi = tuple(float(v) for v in bounds[3:])
    if any(h <= l for l, h in zip(lo, hi)):
        raise ValueError(f"границы должны возрастать, получено {tuple(bounds)}")

    t0 = time.perf_counter()
    box = [*lo, *hi]
    fine = Manifold.level_set(f, box, edge, level)
    if fine.is_empty():
        raise ValueError(f"тело пустое: функция нигде не положительна в границах "
                         f"{tuple(bounds)}, либо тело тоньше шага {edge:g} мм")
    if not allow_split:
        pieces = len(fine.decompose())
        # Кусок, вложенный в другой (полость в оболочке), decompose тоже
        # выдаёт отдельно — но с отрицательным объёмом; распад — это несколько
        # тел с положительным.
        solid_pieces = sum(1 for m in fine.decompose() if m.volume() > 0)
        if solid_pieces > 1:
            raise ValueError(
                f"тело рассыпалось на {solid_pieces} кусков при шаге {edge:g} мм "
                f"(всего компонент {pieces}): где-то оно тоньше шага, и сетка его "
                f"не удержала. Уменьшите edge; если тел несколько по замыслу — "
                f"allow_split=True")
    solid = Solid.wrap(fine, name=name or "органика", op="level_set")
    if not quality:
        return solid

    coarse = Manifold.level_set(f, box, edge * 2, level)
    return solid, MeshQuality(coarse.volume(), fine.volume(), edge * 2, edge,
                              (time.perf_counter() - t0) * 1000)
