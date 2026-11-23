"""Объёмный растр детали: «занято/пусто» на регулярной сетке.

Нужен тому, что рассуждает СЛОЯМИ, а не поверхностями: поддержкам, мостам,
площади прилипания. Поверхность для них — не тот язык: вопрос «есть ли что-то
под этой точкой» через грани не задать.

ПОЧЕМУ ПОСЛОЙНО, А НЕ ТОЧКА ЗА ТОЧКОЙ. Прямая проверка принадлежности
(`measure.contains`) на объёмной сетке непригодна: замерено 70 тысяч точек за
15 секунд, то есть четыре с половиной тысячи в секунду — на подставке из
примеров это минуты. Здесь берутся ПЛОСКИЕ СЕЧЕНИЯ (их ядро строит быстро) и
растеризуются как многоугольники. Та же подставка при шаге 0.5 мм: сечения
135 мс, растр 52 мс — в полтораста раз быстрее, и занятость сходится с
отношением объёма к габариту (37.8 % против 37.7 %).

СЕТКА ЛЕЖИТ ПО ЦЕНТРАМ ЯЧЕЕК, а не по узлам. Иначе слой у самого дна попал бы
ровно на границу тела, и ответ «занято или нет» зависел бы от округления.

ТОНЬШЕ ШАГА — ЗНАЧИТ НЕ ВИДНО, и об этом надо помнить дважды. Деталь целиком
тоньше шага растр не опишет вовсе: пробный листок 0.2 мм при шаге 1 мм давал
ПУСТУЮ сетку, а проверки по ней бодро отвечали «поддержек нет». Теперь такой
случай — отказ с числом. А вот тонкую ЧАСТЬ большой детали — ребро 0.3 мм при
шаге 0.5 — растр потеряет молча, и это цена способа: выбирайте шаг мельче
самой тонкой стенки, которая вас занимает.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from datum.geom.measure import bbox, section_xy
from datum.geom.solid import Solid

__all__ = ["VoxelGrid", "voxelize"]


@dataclass(frozen=True, slots=True)
class VoxelGrid:
    """Занятость на сетке. Оси массива — (z, x, y), как у стопки слоёв."""

    filled: np.ndarray
    origin: tuple[float, float, float]
    step: float

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(int(v) for v in self.filled.shape)      # type: ignore[return-value]

    @property
    def cell_volume(self) -> float:
        return self.step ** 3

    @property
    def cell_area(self) -> float:
        return self.step ** 2

    @property
    def volume(self) -> float:
        """Объём по растру. Сходится с настоящим при измельчении шага."""
        return float(self.filled.sum()) * self.cell_volume

    def z_of(self, layer: int) -> float:
        """Высота центра слоя."""
        return self.origin[2] + (layer + 0.5) * self.step

    def layer_count(self) -> int:
        return int(self.filled.shape[0])


def _raster(section, origin, width: int, depth: int, step: float) -> np.ndarray:
    """Многоугольник сечения → маска (x, y).

    Дырки закрашиваются ФОНОМ поверх наружного контура: у `ImageDraw` нет
    понятия дырки, и рисовать их иначе пришлось бы разбором вложенности.
    """
    image = Image.new("1", (width, depth), 0)
    draw = ImageDraw.Draw(image)
    for piece in getattr(section, "geoms", [section]):
        if piece.is_empty:
            continue
        outer = [((x - origin[0]) / step - 0.5, (y - origin[1]) / step - 0.5)
                 for x, y in piece.exterior.coords]
        if len(outer) >= 3:
            draw.polygon(outer, fill=1)
        for ring in piece.interiors:
            hole = [((x - origin[0]) / step - 0.5, (y - origin[1]) / step - 0.5)
                    for x, y in ring.coords]
            if len(hole) >= 3:
                draw.polygon(hole, fill=0)
    # PIL держит картинку как (строки=y, столбцы=x) — приводим к (x, y).
    return np.array(image, dtype=bool).T


def voxelize(solid: Solid, step: float = 0.5, *,
             max_cells: int = 40_000_000) -> VoxelGrid:
    """Объёмный растр тела с шагом `step` миллиметров.

    `max_cells` — предохранитель: на крупной детали мелкий шаг даёт сетку в
    десятки гигабайт, и отказ с числом понятнее, чем съеденная память.
    """
    if step <= 0:
        raise ValueError(f"шаг растра должен быть положительным, получено {step}")
    lo, hi = bbox(solid)
    extent = [float(hi[i] - lo[i]) for i in range(3)]
    if min(extent) < step:
        thinnest = "XYZ"[int(np.argmin(extent))]
        raise ValueError(
            f"деталь тоньше шага растра: {min(extent):.3f} мм по {thinnest} при шаге "
            f"{step} мм. Такая сетка выйдет пустой, и ответы по ней будут враньём. "
            f"Возьмите шаг не крупнее {min(extent) / 2:.3f} мм")
    size = [max(e, step) for e in extent]
    counts = [max(1, int(np.ceil(size[i] / step))) for i in range(3)]
    total = counts[0] * counts[1] * counts[2]
    if total > max_cells:
        raise ValueError(
            f"растр {counts[0]}×{counts[1]}×{counts[2]} = {total} ячеек при шаге "
            f"{step} мм — больше предела {max_cells}. Возьмите шаг крупнее")

    origin = (float(lo[0]), float(lo[1]), float(lo[2]))
    layers = []
    for k in range(counts[2]):
        z = origin[2] + (k + 0.5) * step
        layers.append(_raster(section_xy(solid, z), origin,
                              counts[0], counts[1], step))
    return VoxelGrid(filled=np.stack(layers), origin=origin, step=float(step))
