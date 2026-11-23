"""Поле расстояния на сетке: скульптинг органики и печатность по построению.

ЗАЧЕМ. `implicit.mesh_sdf` строит поверхность через `Manifold.level_set`, а
тот зовёт Python на КАЖДУЮ точку решётки. Скульптура подставки под мышь
(поле на сетке 0.4 мм, 108×90×56 мм) строилась так ~45 с при каждой сборке, и
агент держал всё поле в numpy сам — `lib/sculpt.py`, 279 строк, — потому что
составлять сопряжения вложенными лямбдами при таком числе точек нельзя.
Здесь то же поле — массив, операции над ним векторные, а поверхность строится
без Python на точку.

ЗНАК — «ПЛЮС ВНУТРИ», как в `implicit` и `Manifold.level_set`: своё соглашение
приглашало бы ошибку знака на каждом стыке.

ПОРЯДОК, ПРОВЕРЕННЫЙ ПЕЧАТЬЮ (подставка, `NOTES.md` её проекта):
  форма → `limit_overhang` → `redistance` → узор (`surface_pattern`) → вычитания.
  * Предел уклона — по форме ДО узора: после узора он заливал нижние половины
    ячеек «зубьями».
  * Переизмерить поле перед узором: в «долинах» гладких сопряжений оно уже не
    расстояние, и узор пробивал там пузырьки — запаянные полости.
  * Затухание узора на нависании — по СГЛАЖЕННОМУ градиенту: сырой давал
    «зерно» на рельефе, которое заметил человек, а не агент.

Все поля одной детали живут на одной сетке (`Grid`): операции между полями на
разных сетках — отказ, а не тихая интерполяция.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "Grid", "Field",
    # примитивы
    "sphere", "box", "cyl_x", "cyl_y", "cyl_z", "plane", "sdf2d", "extrude", "loft",
    # операции
    "union", "intersect", "diff", "smooth_union", "smooth_intersect", "smooth_diff",
    "offset", "smooth", "redistance", "limit_overhang", "down_share",
    "surface_pattern", "diagrid", "honeycomb",
    # поверхность
    "mesh",
]


# ===================================================================== сетка
@dataclass(frozen=True)
class Grid:
    """Решётка: начало `origin` (мм), шаг `h` (мм), число узлов `shape`.

    Узел (i, j, k) лежит в `origin + h·(i, j, k)`. Строится по габариту:
    `Grid.around(lo, hi, h)` — с запасом в две клетки, чтобы поверхность у
    края габарита не упиралась в край решётки.
    """

    origin: tuple[float, float, float]
    h: float
    shape: tuple[int, int, int]

    @staticmethod
    def around(lo, hi, h: float, *, margin: float | None = None) -> "Grid":
        if not (h > 0 and math.isfinite(h)):
            raise ValueError(f"шаг сетки должен быть положительным, получено {h!r}")
        pad = 2 * h if margin is None else float(margin)
        lo = np.asarray(lo, dtype=float) - pad
        hi = np.asarray(hi, dtype=float) + pad
        if np.any(hi <= lo):
            raise ValueError(f"пустой габарит: {lo.tolist()}…{hi.tolist()}")
        shape = tuple(int(math.ceil((b - a) / h)) + 1 for a, b in zip(lo, hi))
        cells = shape[0] * shape[1] * shape[2]
        if cells > 200_000_000:
            raise ValueError(f"сетка {shape[0]}×{shape[1]}×{shape[2]} = {cells:,} узлов — "
                             f"слишком мелкий шаг {h} мм для такого габарита")
        return Grid(tuple(float(v) for v in lo), float(h), shape)   # type: ignore[arg-type]

    def axes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Координаты узлов по осям, мм (одномерные)."""
        return tuple(self.origin[a] + self.h * np.arange(self.shape[a], dtype=np.float64)
                     for a in range(3))     # type: ignore[return-value]

    def mesh(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Координаты узлов в форме для широковещания: X (n,1,1), Y (1,m,1), Z (1,1,k)."""
        x, y, z = self.axes()
        return x[:, None, None], y[None, :, None], z[None, None, :]

    @property
    def lo(self) -> tuple[float, float, float]:
        return self.origin

    @property
    def hi(self) -> tuple[float, float, float]:
        return tuple(o + self.h * (n - 1) for o, n in zip(self.origin, self.shape))  # type: ignore[return-value]

    def full(self, value: float) -> "Field":
        return Field(self, np.full(self.shape, value, dtype=np.float32))


@dataclass(frozen=True, eq=False)
class Field:
    """Значения поля в узлах `grid` (float32), плюс внутри."""

    grid: Grid
    values: np.ndarray

    def __post_init__(self):
        if self.values.shape != self.grid.shape:
            raise ValueError(f"значений {self.values.shape}, а у сетки {self.grid.shape}")

    def like(self, values) -> "Field":
        return Field(self.grid, np.asarray(values, dtype=np.float32))

    def _same(self, other: "Field") -> None:
        if other.grid != self.grid:
            raise ValueError("поля на разных сетках: заведите одну Grid на деталь")

    # арифметика: поле плюс число — смещение уровня, как `offset`
    def __add__(self, other):
        if isinstance(other, Field):
            self._same(other)
            return self.like(self.values + other.values)
        return self.like(self.values + np.float32(other))

    def __sub__(self, other):
        if isinstance(other, Field):
            self._same(other)
            return self.like(self.values - other.values)
        return self.like(self.values - np.float32(other))

    def __mul__(self, other):
        if isinstance(other, Field):
            self._same(other)
            return self.like(self.values * other.values)
        return self.like(self.values * np.float32(other))

    __rmul__ = __mul__

    def __neg__(self):
        return self.like(-self.values)


def _values(f) -> np.ndarray:
    return f.values if isinstance(f, Field) else np.asarray(f, dtype=np.float32)


# ================================================================ примитивы
# Имена и порядок аргументов — как у тел в `datum.geom` (`box`, `sphere`,
# `cyl_z`…): диаметры, границы по осям. Первый аргумент — сетка.
def sphere(grid: Grid, d: float, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Field:
    """Шар диаметра d с центром (x, y, z). Точное расстояние."""
    if d <= 0:
        raise ValueError(f"диаметр должен быть положительным, получено {d}")
    X, Y, Z = grid.mesh()
    return Field(grid, (d / 2 - np.sqrt((X - x) ** 2 + (Y - y) ** 2 + (Z - z) ** 2))
                 .astype(np.float32))


def box(grid: Grid, x0: float, x1: float, y0: float, y1: float,
        z0: float, z1: float) -> Field:
    """Параллелепипед по границам. Точное расстояние, включая углы."""
    X, Y, Z = grid.mesh()
    lo, hi = (x0, y0, z0), (x1, y1, z1)
    if any(b <= a for a, b in zip(lo, hi)):
        raise ValueError(f"пустой брус: {lo}…{hi}")
    c = [(a + b) / 2 for a, b in zip(lo, hi)]
    e = [(b - a) / 2 for a, b in zip(lo, hi)]
    q = np.broadcast_arrays(np.abs(X - c[0]) - e[0], np.abs(Y - c[1]) - e[1],
                            np.abs(Z - c[2]) - e[2])
    outside = np.sqrt(sum(np.maximum(v, 0.0) ** 2 for v in q))
    inside = np.minimum(np.maximum(np.maximum(q[0], q[1]), q[2]), 0.0)
    return Field(grid, (-(outside + inside)).astype(np.float32))


def _cyl(grid: Grid, d: float, a0: float, a1: float, axis: int, c1: float, c2: float) -> Field:
    if d <= 0 or a1 <= a0:
        raise ValueError(f"цилиндр: диаметр {d}, длина {a0}…{a1}")
    coords = grid.mesh()
    along = coords[axis]
    u, v = [coords[k] for k in range(3) if k != axis]
    radial = d / 2 - np.sqrt((u - c1) ** 2 + (v - c2) ** 2)
    axial = np.minimum(along - a0, a1 - along)
    return Field(grid, _intersect_exact(radial, axial).astype(np.float32))


def cyl_z(grid: Grid, d: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0) -> Field:
    """Цилиндр вдоль Z: диаметр d, от z0 до z1, ось в (x, y). Точное расстояние."""
    return _cyl(grid, d, z0, z1, 2, x, y)


def cyl_x(grid: Grid, d: float, x0: float, x1: float, y: float = 0.0, z: float = 0.0) -> Field:
    return _cyl(grid, d, x0, x1, 0, y, z)


def cyl_y(grid: Grid, d: float, y0: float, y1: float, x: float = 0.0, z: float = 0.0) -> Field:
    return _cyl(grid, d, y0, y1, 1, x, z)


def plane(grid: Grid, point, normal) -> Field:
    """Полупространство: внутри — против нормали."""
    X, Y, Z = grid.mesh()
    n = np.asarray(normal, dtype=float)
    n = n / np.linalg.norm(n)
    dist = (X - point[0]) * n[0] + (Y - point[1]) * n[1] + (Z - point[2]) * n[2]
    return Field(grid, np.broadcast_to(-dist, grid.shape).astype(np.float32))


def _intersect_exact(a, b):
    """Точное расстояние до пересечения двух «перпендикулярных» полей (призма)."""
    a, b = np.broadcast_arrays(a, b)
    qa, qb = -a, -b                       # минус внутри, как у классической формулы
    outside = np.sqrt(np.maximum(qa, 0.0) ** 2 + np.maximum(qb, 0.0) ** 2)
    inside = np.minimum(np.maximum(qa, qb), 0.0)
    return -(outside + inside)


def sdf2d(grid: Grid, polygon, *, band: float | None = None) -> np.ndarray:
    """Знаковое расстояние до плоского контура (shapely) в узлах XY, мм (n×m).

    Плюс внутри. Точное — расстояние до границы контура, а не евклидово
    преобразование маски: в лофте граница промежуточного сечения проходит
    там, где оба поля далеко от своих контуров, и ошибка EDT в полклетки легла
    бы рябью на поверхность.

    `band` — точное только в полосе |d| ≤ band, дальше — оценка по EDT с
    ошибкой меньше двух клеток (по модулю не меньше band, знак точный). Для
    полей, которые дальше полосы срезаются или не решают формы (ложемент,
    смещённый на стенку): контур в тысячи вершин на каждом слое сетки иначе
    стоит десятки секунд сборки. Для лофта — без полосы.
    """
    import shapely
    from scipy import ndimage

    x, y, _ = grid.axes()
    X, Y = np.meshgrid(x, y, indexing="ij")
    if polygon is None or polygon.is_empty:
        return np.full(X.shape, -1e3, dtype=np.float32)
    inside = shapely.contains_xy(polygon, X, Y)
    if band is None or not inside.any():
        dist = _boundary_distance(polygon, np.c_[X.ravel(), Y.ravel()]).reshape(X.shape)
        return np.where(inside, dist, -dist).astype(np.float32)
    h = grid.h
    # Полоса — от точек САМОЙ границы, а не от маски: щель уже клетки маска
    # не видит, и узел рядом с ней сошёл бы за дальний. Граница выбирается не
    # реже h, каждая точка метит ближайший узел; узел в band от границы —
    # не дальше band + 1.21·h от метки (h/2 до точки, полдиагонали до узла).
    pts = _boundary_samples(polygon, h)
    ij = np.rint((pts - np.array(grid.origin[:2])) / h).astype(np.int64)
    ok = (ij >= 0).all(axis=1) & (ij[:, 0] < X.shape[0]) & (ij[:, 1] < X.shape[1])
    marks = np.ones(X.shape, dtype=bool)
    marks[ij[ok, 0], ij[ok, 1]] = False
    if not ok.all():                       # граница за краем сетки — без полосы
        dist = _boundary_distance(polygon, np.c_[X.ravel(), Y.ravel()]).reshape(X.shape)
        return np.where(inside, dist, -dist).astype(np.float32)
    dm = ndimage.distance_transform_edt(marks) * h
    near = dm < band + 2 * h
    # Дальше полосы — EDT маски: до узла по ту сторону не меньше d, поэтому
    # de − h/2 ≥ d − h/2. Сверху — метки: до границы не дальше dm + 0.71·h
    # (метка в полудиагонали от точки границы), а щель уже клетки маска не
    # видит и завысила бы. Итог — от d − h/2 до d + 1.92·h (dm ≤ d + 1.21·h),
    # и не меньше band: там |d| > band по построению полосы.
    de = np.where(inside, ndimage.distance_transform_edt(inside),
                  ndimage.distance_transform_edt(~inside)) * h
    dist = np.maximum(np.minimum(de - 0.5 * h, dm + 0.71 * h), band)
    dist[near] = _boundary_distance(polygon, np.c_[X[near], Y[near]])
    return np.where(inside, dist, -dist).astype(np.float32)


def _boundary_samples(polygon, step: float) -> np.ndarray:
    """Точки границы контура не реже `step` (вершины — тоже)."""
    import shapely

    out = []
    for part in shapely.get_parts(polygon.boundary):
        c = shapely.get_coordinates(part)
        if len(c) < 2:
            out.append(c)
            continue
        a, b = c[:-1], c[1:]
        n = np.maximum(1, np.ceil(np.linalg.norm(b - a, axis=1) / step).astype(np.int64))
        own = np.repeat(np.arange(len(a)), n)
        t = (np.arange(len(own)) - np.repeat(np.cumsum(n) - n, n)) / n[own]
        out.append(a[own] + t[:, None] * (b - a)[own])
        out.append(c[-1:])
    return np.concatenate(out)


def _boundary_distance(polygon, points: np.ndarray) -> np.ndarray:
    """Точное расстояние от точек до границы контура — с индексом.

    `shapely.distance` индекса не строит: каждая точка перебирает все
    отрезки, и у ложемента подставки (контур на 1800 вершин, 74 тыс. узлов)
    это было 0.94 с на слой — 96 из 127 с сборки. Здесь отрезки границы
    делятся на куски не длиннее медианного отрезка (но не мельче тысячной
    габарита; короткие остаются как есть), середины кусков — в KD-дереве, и
    расстояние считается точно до кусков k ближайших середин.

    Почему точно: кусок j не ближе |x − m_j| − l, где l — наибольшая
    полудлина куска. Если k-я середина дальше найденного + l, ни один
    непроверенный кусок ближе не будет. Иначе — k вчетверо больше, а после
    128 (в центре круга равноудалено всё) — `shapely.distance`. Замер на
    контурах подставки: в 3.5 раза быстрее, расхождение 1e-14 мм.
    """
    import shapely
    from scipy.spatial import cKDTree

    seg_a, seg_b = [], []
    for part in shapely.get_parts(polygon.boundary):
        c = shapely.get_coordinates(part)
        if len(c) >= 2:
            seg_a.append(c[:-1])
            seg_b.append(c[1:])
    a = np.concatenate(seg_a)
    b = np.concatenate(seg_b)
    length = np.linalg.norm(b - a, axis=1)
    lo, hi = np.min(a, axis=0), np.max(a, axis=0)
    piece = max(float(np.median(length)), float(np.max(hi - lo)) / 1000, 1e-9)
    n = np.maximum(1, np.ceil(length / piece).astype(np.int64))
    owner = np.repeat(np.arange(len(a)), n)
    i = np.arange(len(owner)) - np.repeat(np.cumsum(n) - n, n)
    pa = a[owner] + (i / n[owner])[:, None] * (b - a)[owner]
    pb = a[owner] + ((i + 1) / n[owner])[:, None] * (b - a)[owner]
    e = pb - pa
    e2 = np.einsum("ij,ij->i", e, e)
    half = float(np.sqrt(e2.max())) / 2
    inv = 1.0 / np.maximum(e2, 1e-300)
    tree = cKDTree((pa + pb) / 2)

    dist = np.empty(len(points))
    todo = np.arange(len(points))
    budget = 1 << 21                                 # пар (точка, кусок) за раз
    for k in (32, 128):
        k = min(k, tree.n)
        unsure = []
        step = max(1, budget // k)
        for start in range(0, len(todo), step):
            ids = todo[start:start + step]
            p = points[ids]
            near, idx = tree.query(p, k=k, workers=-1)
            near, idx = near.reshape(len(ids), k), idx.reshape(len(ids), k)
            # До отрезка в плоскости — покоординатно, квадратами, корень один.
            rx = p[:, :1] - pa[idx, 0]
            ry = p[:, 1:] - pa[idx, 1]
            ex, ey = e[idx, 0], e[idx, 1]
            t = np.clip((rx * ex + ry * ey) * inv[idx], 0.0, 1.0)
            rx -= t * ex
            ry -= t * ey
            d = np.sqrt((rx * rx + ry * ry).min(axis=1))
            dist[ids] = d
            if k < tree.n:
                unsure.append(ids[near[:, -1] - half < d])
        todo = np.concatenate(unsure) if unsure else todo[:0]
        if not len(todo):
            return dist
    dist[todo] = shapely.distance(polygon.boundary, shapely.points(points[todo]))
    return dist


def extrude(grid: Grid, polygon, z0: float, z1: float) -> Field:
    """Призма из плоского контура между z0 и z1 — точное расстояние."""
    _, _, Z = grid.mesh()
    d2 = sdf2d(grid, polygon)[:, :, None]
    dz = np.minimum(Z - z0, z1 - Z)
    return Field(grid, _intersect_exact(d2, dz).astype(np.float32))


def loft(grid: Grid, sections) -> Field:
    """Тело из стопки сечений `[(контур, z), …]`, гладко между ними.

    Между соседними сечениями поле — линейная смесь их плоских полей
    расстояния по высоте: контур перетекает в контур без ступеней. На высоте
    сечения тело совпадает с ним точно; между сечениями выпуклые углы
    скругляются (снаружи меньшего контура расстояние у угла евклидово), и
    лофт двух квадратов — не усечённая пирамида, а на ~7 % меньше. Нужна
    точная форма — давайте сечения чаще. Ниже нижнего и выше верхнего
    сечения тело кончается (крышки — точно).
    """
    secs = sorted(((float(z), poly) for poly, z in sections), key=lambda t: t[0])
    if len(secs) < 2:
        raise ValueError("лофту нужно хотя бы два сечения")
    zs = np.array([z for z, _ in secs])
    planes = np.stack([sdf2d(grid, poly) for _, poly in secs])      # S×n×m
    _, _, zg = grid.axes()
    out = np.empty(grid.shape, dtype=np.float32)
    for k, z in enumerate(zg):
        j = int(np.clip(np.searchsorted(zs, z) - 1, 0, len(zs) - 2))
        t = float(np.clip((z - zs[j]) / (zs[j + 1] - zs[j]), 0.0, 1.0))
        out[:, :, k] = (1 - t) * planes[j] + t * planes[j + 1]
    _, _, Z = grid.mesh()
    dz = np.minimum(Z - zs[0], zs[-1] - Z)
    return Field(grid, _intersect_exact(out, dz).astype(np.float32))


# ================================================================ операции
def union(*fields: Field) -> Field:
    out = fields[0]
    for f in fields[1:]:
        out._same(f)
        out = out.like(np.maximum(out.values, f.values))
    return out


def intersect(*fields: Field) -> Field:
    out = fields[0]
    for f in fields[1:]:
        out._same(f)
        out = out.like(np.minimum(out.values, f.values))
    return out


def diff(a: Field, *cut: Field) -> Field:
    out = a
    for f in cut:
        out._same(f)
        out = out.like(np.minimum(out.values, -f.values))
    return out


def smooth_union(a: Field, b: Field, r: float) -> Field:
    """Гладкое объединение с радиусом сопряжения r (квадратичное smax)."""
    a._same(b)
    if r <= 0:
        return union(a, b)
    k = np.clip(r - np.abs(a.values - b.values), 0.0, None) / r
    return a.like(np.maximum(a.values, b.values) + k * k * r / 4)


def smooth_intersect(a: Field, b: Field, r: float) -> Field:
    a._same(b)
    if r <= 0:
        return intersect(a, b)
    k = np.clip(r - np.abs(a.values - b.values), 0.0, None) / r
    return a.like(np.minimum(a.values, b.values) - k * k * r / 4)


def smooth_diff(a: Field, b: Field, r: float) -> Field:
    return smooth_intersect(a, -b, r)


def offset(f: Field, d: float) -> Field:
    """Нарастить на d мм (отрицательное — ужать). Точно — пока поле расстояние."""
    return f + d


def smooth(f: Field, sigma_mm: float) -> Field:
    """Гауссово сглаживание поля, σ в мм. Скругляет рёбра, гасит рябь."""
    from scipy import ndimage

    if sigma_mm <= 0:
        return f
    return f.like(ndimage.gaussian_filter(f.values, sigma_mm / f.grid.h, mode="nearest"))


def _crossings(values: np.ndarray, h: float, diagonals: bool) -> np.ndarray:
    """Точки, где поле переходит ноль на рёбрах решётки (в клетках, не в мм).

    По осям, а на плоскости ещё и по диагоналям клеток: точки ложатся на
    границу чаще шага, и расстояние до ближайшей из них отличается от
    расстояния до самой границы на сотые доли клетки.
    """
    nd = values.ndim
    steps = [tuple(int(a == k) for a in range(nd)) for k in range(nd)]
    if diagonals and nd == 2:
        steps += [(1, 1), (1, -1)]
    pts = []
    for step in steps:
        lo = tuple(slice(0, n - 1) if d == 1 else slice(1, n) if d == -1 else slice(0, n)
                   for n, d in zip(values.shape, step))
        hi = tuple(slice(1, n) if d == 1 else slice(0, n - 1) if d == -1 else slice(0, n)
                   for n, d in zip(values.shape, step))
        a, b = values[lo].astype(np.float64), values[hi].astype(np.float64)
        cross = (a > 0) != (b > 0)
        if not cross.any():
            continue
        idx = np.argwhere(cross).astype(np.float64)
        base = idx + np.array([1.0 if d == -1 else 0.0 for d in step])
        t = a[cross] / (a[cross] - b[cross])
        pts.append(base + t[:, None] * np.asarray(step, dtype=np.float64))
    return np.concatenate(pts) if pts else np.zeros((0, nd))


def _distance(values: np.ndarray, h: float, band: float, *,
              diagonals: bool = False) -> np.ndarray:
    """Знаковое расстояние до нуля поля, мм: точно у границы, EDT вдали.

    У границы (в полосе `band`) — расстояние до ближайшей точки перехода
    нуля на рёбрах решётки, а не `F/|∇F|`: срез трёхмерного расстояния у
    горизонтальной грани почти не меняется в плоскости слоя, и оценка по
    градиенту завышала расстояние в разы — предел уклона на «грибе» давал
    ступени по 2…3 мм вместо ровного конуса. Дальше полосы — евклидово
    преобразование расстояний от маски с поправкой в полклетки: там точность
    не нужна, нужен только знак и порядок величины.
    """
    from scipy import ndimage
    from scipy.spatial import cKDTree

    inside = values > 0
    if inside.all() or not inside.any():
        return values.astype(np.float32)
    d = np.where(inside,
                 ndimage.distance_transform_edt(inside) * h - 0.5 * h,
                 -(ndimage.distance_transform_edt(~inside) * h - 0.5 * h))
    near = np.abs(d) < band + h
    if not near.any():
        return d.astype(np.float32)
    nodes = np.argwhere(near).astype(np.float64)
    if values.ndim == 3:
        # В объёме точки перехода лежат на поверхности реже, чем узлы к ней
        # близки: расстояние до ближайшей точки завышало у самой поверхности
        # до 0.4 клетки, и малое смещение (зазор в 0.3 мм) уезжало на столько
        # же. Поэтому — точное расстояние до треугольников той самой
        # поверхности, которую построит `mesh`. Замер на шаре: 0.006 мм
        # против 0.155 при шаге 0.4.
        verts, faces = _surface(values, (0.0, 0.0, 0.0), 1.0)
        if not len(faces):
            return d.astype(np.float32)
        dist = _triangle_distance(nodes, verts, faces)
    else:
        # На плоскости точек перехода по осям и диагоналям хватает: граница —
        # кривая, и они ложатся на неё чаще шага (замер на круге: 0.003 мм).
        pts = _crossings(values, h, diagonals)
        if not len(pts):
            return d.astype(np.float32)
        dist, _ = cKDTree(pts).query(nodes, k=1, workers=-1)
    d[near] = np.where(inside[near], dist, -dist) * h
    return d.astype(np.float32)


def _triangle_distance(points: np.ndarray, verts: np.ndarray, faces: np.ndarray, *,
                       candidates: int = 8, chunk: int = 200_000) -> np.ndarray:
    """Расстояние от точек до ближайшего из треугольников (в единицах координат).

    Кандидаты — треугольники с ближайшими центроидами (`candidates` штук):
    треугольник поверхности по сетке не больше клетки, и ближайший почти
    всегда среди них; если нет (крупный рядом с мелкими), расстояние выходит
    завышенным не больше чем на клетку. Расстояние до треугольника — до
    плоскости, если проекция внутри, иначе до ближайшего из трёх рёбер.
    """
    from scipy.spatial import cKDTree

    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    tree = cKDTree((a + b + c) / 3.0)
    k = min(candidates, len(faces))
    out = np.empty(len(points))
    for s in range(0, len(points), chunk):
        p = points[s:s + chunk]
        _, idx = tree.query(p, k=k, workers=-1)
        idx = idx.reshape(len(p), k)
        best = np.full(len(p), np.inf)
        for j in range(k):
            t = idx[:, j]
            best = np.minimum(best, _point_triangle(p, a[t], b[t], c[t]))
        out[s:s + chunk] = best
    return out


def _segment(p, a, b):
    ab = b - a
    t = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab),
                                                               1e-30), 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def _point_triangle(p, a, b, c) -> np.ndarray:
    n = np.cross(b - a, c - a)
    nn = np.linalg.norm(n, axis=1)
    ok = nn > 1e-30
    unit = n / np.where(ok, nn, 1.0)[:, None]
    dist_plane = np.einsum("ij,ij->i", p - a, unit)
    q = p - dist_plane[:, None] * unit
    # Проекция внутри — если по одну сторону от всех трёх рёбер.
    s1 = np.einsum("ij,ij->i", np.cross(b - a, q - a), unit)
    s2 = np.einsum("ij,ij->i", np.cross(c - b, q - b), unit)
    s3 = np.einsum("ij,ij->i", np.cross(a - c, q - c), unit)
    inside = ok & (s1 >= 0) & (s2 >= 0) & (s3 >= 0)
    edges = np.minimum(np.minimum(_segment(p, a, b), _segment(p, b, c)), _segment(p, c, a))
    return np.where(inside, np.abs(dist_plane), edges)


def redistance(f: Field) -> Field:
    """Сделать поле снова расстоянием, не сдвинув поверхность.

    После гладких сопряжений и предела уклона значения — уже не расстояние:
    в «долинах» оно занижено, и узор глубиной в миллиметр пробивал там
    пузырьки. Переизмерить — перед узором и перед смещением. Точно — в
    полосе в несколько клеток у поверхности, где это и нужно.
    """
    return f.like(_distance(f.values, f.grid.h, 4.0 * f.grid.h))


_UP = {"+X": (0, 1), "-X": (0, -1), "+Y": (1, 1), "-Y": (1, -1), "+Z": (2, 1), "-Z": (2, -1)}


def _up(up: str) -> tuple[int, int]:
    key = str(up).strip().upper()
    if key not in _UP:
        raise ValueError(f"направление печати {up!r}: допустимо {sorted(_UP)}")
    return _UP[key]


def limit_overhang(f: Field, angle: float = 45.0, *, up: str = "+Z") -> Field:
    """Нигде не круче `angle` от вертикали: добавить материал снизу, не трогая верх.

    Слой за слоем сверху вниз (по `up` — ориентации печати детали) каждый
    слой обязан содержать слой над ним, ужатый в ПЛОСКОСТИ слоя на
    h·tg(angle). Расстояние меряется в плоскости от самой границы слоя, а не
    объёмное и не по градиенту: объёмное у потолка меньше плоского, а срез
    объёмного поля у горизонтальной грани почти не меняется в плоскости — и
    слой то не ужимался вовсе, то ужимался на 2…3 мм разом («зубья»).

    Замер на «грибе» (шляпка Ø36 на ножке Ø10, h = 0.25, 45°): наибольший шаг
    опоры 0.268 мм на слой при 0.250 — 47°, у самой шляпки, где в слое
    встречаются её значения и опоры. Промежуточные подслои пробовали — шаг
    у шляпки только рос (до 51° при четырёх).

    Ниже самой нижней точки детали материал не добавляется: там стол.
    Только добавляет материал. Поле после — не расстояние: перед узором и
    смещением — `redistance`.
    """
    if not 0 < angle < 89:
        raise ValueError(f"угол нависания {angle}° — нужен от 0 до 89")
    axis, sign = _up(up)
    h = f.grid.h
    shrink = h * math.tan(math.radians(angle))
    v = np.moveaxis(f.values.copy(), axis, -1)
    if sign < 0:
        v = v[..., ::-1]
    filled = np.nonzero((v > 0).any(axis=(0, 1)))[0]
    if not len(filled):
        return f
    floor = int(filled[0])                 # нижний слой, где деталь есть
    for k in range(v.shape[-1] - 2, floor - 1, -1):
        above = v[..., k + 1]
        # Пропуск — только по пустому слою. По значению поля («есть ли глубже
        # shrink») пропускалась тонкая полка: внутри пластины толщиной t
        # объёмное поле не больше t/2, и полка в 0.8 мм оставалась без опоры.
        if not (above > 0).any():
            continue
        d = _distance(above, h, shrink + 2 * h, diagonals=True)
        np.maximum(v[..., k], d - shrink, out=v[..., k])
    if sign < 0:
        v = v[..., ::-1]
    return f.like(np.moveaxis(v, -1, axis))


def down_share(f: Field, *, up: str = "+Z", sigma_mm: float = 0.0) -> Field:
    """Насколько поверхность смотрит вниз: синус её наклона за вертикаль.

    0 — стена, 1 — потолок, отрицательное — смотрит вверх. С `sigma_mm` —
    по сглаженному полю: по сырому градиенту затухание узора давало «зерно».
    """
    axis, sign = _up(up)
    src = smooth(f, sigma_mm) if sigma_mm > 0 else f
    grads = np.gradient(src.values.astype(np.float64), f.grid.h)
    g = np.sqrt(sum(gi * gi for gi in grads))
    # Градиент поля смотрит ВНУТРЬ; у потолка нависания внутрь — вверх.
    share = sign * grads[axis] / np.maximum(g, 1e-9)
    return f.like(share)


# ==================================================================== узоры
def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _cylindrical(grid: Grid, axis_xy) -> tuple[np.ndarray, np.ndarray, float]:
    """Развёртка вокруг вертикальной оси: (дуга u по опорному радиусу, z)."""
    X, Y, Z = grid.mesh()
    ax, ay, r_ref = axis_xy
    theta = np.arctan2(Y - ay, X - ax)
    return np.broadcast_to(theta * r_ref, grid.shape), np.broadcast_to(Z, grid.shape), r_ref


def diagrid(period: float, angle: float = 60.0, rib: float = 0.25, *, axis_xy=(0.0, 0.0, 30.0)):
    """Ромбическая сетка рёбер: две семьи винтовых линий под ±angle к горизонтали.

    Возвращает функцию `grid → (рёбра 0…1, фаза в ячейке 0…1)`: рёбра — 1 на
    ребре, фаза — от нижней вершины ячейки (0) к верхней (1). По фазе узор
    делает «чешую»: глубже у нижней вершины, к верхней выходит на поверхность,
    и у ячейки нет потолка — она печатается и на нависании.
    `axis_xy` — (x, y, опорный радиус) оси развёртки; на опорном радиусе шаг
    ровно `period`, и число ячеек по кругу округляется до целого, чтобы узор
    сходился на шве.
    """
    def make(grid: Grid):
        u, z, r_ref = _cylindrical(grid, axis_xy)
        n = max(1, round(2 * math.pi * r_ref / period))
        p = 2 * math.pi * r_ref / n                  # шаг, сходящийся на шве
        a = math.radians(angle)
        s1 = (u * math.sin(a) + z * math.cos(a)) / p
        s2 = (-u * math.sin(a) + z * math.cos(a)) / p
        d1 = np.abs(s1 - np.round(s1))
        d2 = np.abs(s2 - np.round(s2))
        ribs = 1.0 - _smoothstep(rib / 2, rib / 2 + 0.08, np.minimum(d1, d2))
        phase = (np.mod(s1, 1.0) + np.mod(s2, 1.0)) / 2.0
        return ribs.astype(np.float32), phase.astype(np.float32)

    return make


def honeycomb(period: float, rib: float = 0.15, *, axis_xy=(0.0, 0.0, 30.0)):
    """Соты в развёртке вокруг вертикальной оси. Фаза — по высоте в ячейке."""
    def make(grid: Grid):
        u, z, r_ref = _cylindrical(grid, axis_xy)
        n = max(1, round(2 * math.pi * r_ref / period))
        p = 2 * math.pi * r_ref / n
        # Гексагональная решётка: расстояние до ближайшего центра по трём осям.
        x, y = u / p, z / (p * math.sqrt(3) / 2) / 2
        best = np.full(np.shape(x), np.inf)
        phase = np.zeros(np.shape(x))
        for ox, oy in ((0.0, 0.0), (0.5, 0.5)):
            cx = np.round(x - ox) + ox
            cy = np.round(y - oy) + oy
            dx, dy = (x - cx), (y - cy) * math.sqrt(3)
            # шестиугольная «норма»
            hexd = np.maximum(np.abs(dx), (np.abs(dx) + np.abs(dy) * math.sqrt(3)) / 2)
            closer = hexd < best
            best = np.where(closer, hexd, best)
            phase = np.where(closer, 0.5 + (y - cy), phase)
        ribs = _smoothstep(0.5 - rib / 2 - 0.06, 0.5 - rib / 2, best)
        return ribs.astype(np.float32), np.clip(phase, 0, 1).astype(np.float32)

    return make


def surface_pattern(f: Field, pattern, depth: float, *, power: float = 1.5,
                    fade=(), up: str = "+Z", fade_overhang=(20.0, 35.0),
                    fade_sigma_mm: float = 1.0, soften_mm: float | None = None) -> Field:
    """Рельеф по поверхности: ячейки узора вдавлены на `depth` мм, рёбра — нет.

    Глубина ячейки — по фазе: `depth·(1−фаза)^power`, полная у нижней вершины
    и ноль у верхней («чешуя»), поэтому потолка у ячейки нет. Глубина гаснет:
      * по маскам `fade` (поля 0…1, 1 — узор есть), например у гнёзд и окон;
      * на нависании между `fade_overhang` градусами (по сглаженному полю):
        там, где форма уже смотрит вниз круче, вдавленная ячейка повисла бы.
    Перед узором поле должно быть расстоянием (`redistance`). `soften_mm` —
    сглаживание рельефа (по умолчанию — шаг сетки, см. ниже); 0 — без него.
    """
    ribs, phase = pattern(f.grid)
    relief = depth * (1.0 - ribs) * np.power(np.clip(1.0 - phase, 0.0, 1.0), power)
    a0, a1 = (math.sin(math.radians(v)) for v in fade_overhang)
    lean = down_share(f, up=up, sigma_mm=fade_sigma_mm).values
    keep = 1.0 - _smoothstep(a0, a1, lean)
    for m in fade:
        keep = keep * np.clip(_values(m), 0.0, 1.0)
    # Рельеф сглаживается ДО вычитания: рисунок узора меняется быстрее шага
    # сетки (кромка ребра, верх ячейки), и выборка его в узлах давала
    # «лесенку» на кромках — настоящую, видную на печати и на виде с
    # освещением (её заметил человек на подставке под мышь). σ — шаг сетки.
    from scipy import ndimage

    sigma = f.grid.h if soften_mm is None else soften_mm
    cut = (relief * keep).astype(np.float32)
    if sigma > 0:
        cut = ndimage.gaussian_filter(cut, sigma / f.grid.h, mode="nearest")
    return f.like(f.values - cut)


# ============================================================== поверхность
#: Шесть тетраэдров Куна: 0 → e_a → e_a+e_b → (1,1,1) для каждой перестановки
#: осей. Разбиение одно на все кубы, поэтому диагонали общих граней соседей
#: совпадают и поверхность сшивается без щелей.
_PERMS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))


def _kuhn_offsets() -> np.ndarray:
    out = np.zeros((6, 4, 3), dtype=np.int64)
    for t, (a, b, c) in enumerate(_PERMS):
        out[t, 1, a] = 1
        out[t, 2] = out[t, 1]
        out[t, 2, b] = 1
        out[t, 3] = (1, 1, 1)
    return out


_TET = _kuhn_offsets()


def _case_table() -> list[list[tuple[tuple[int, int], ...]]]:
    """Треугольники для каждого из 16 случаев знаков в вершинах тетраэдра.

    Треугольник — три ребра (пары вершин). Ориентация выправляется потом по
    направлению от внутренней вершины к внешней, поэтому порядок здесь любой.
    """
    table: list[list[tuple[tuple[int, int], ...]]] = []
    for code in range(16):
        ins = [v for v in range(4) if code >> v & 1]
        outs = [v for v in range(4) if not code >> v & 1]
        tris: list[tuple[tuple[int, int], ...]] = []
        if len(ins) == 1:
            i = ins[0]
            tris.append(tuple((i, o) for o in outs))
        elif len(ins) == 3:
            o = outs[0]
            tris.append(tuple((i, o) for i in ins))
        elif len(ins) == 2:
            (i1, i2), (o1, o2) = ins, outs
            # Четырёхугольник i1o1 → i1o2 → i2o2 → i2o1 — по кругу.
            tris.append(((i1, o1), (i1, o2), (i2, o2)))
            tris.append(((i1, o1), (i2, o2), (i2, o1)))
        table.append(tris)
    return table


_CASES = _case_table()


def _surface(values: np.ndarray, origin, h: float, *, chunk: int = 131_072):
    """Нулевая поверхность поля марширующими тетраэдрами: вершины и треугольники.

    Ноль считается СНАРУЖИ (строго больше нуля — внутри): уровень, на котором
    нет ни одного узла, у кусочно-линейного поля даёт двумерное многообразие —
    без особых точек, а значит замкнутую сетку. Вершина ребра — линейной
    интерполяцией, с параметром не ближе `EPS` к узлу: две вершины соседних
    рёбер в одной точке были бы ровно тем слипанием, которое потом не
    различит float32.
    """
    EPS = 1e-3
    F = values
    nx, ny, nz = F.shape
    inside = F > 0
    # Активные кубы: знаки в восьми углах разные.
    any_in = np.zeros((nx - 1, ny - 1, nz - 1), dtype=bool)
    all_in = np.ones_like(any_in)
    for ox in (0, 1):
        for oy in (0, 1):
            for oz in (0, 1):
                s = inside[ox:nx - 1 + ox, oy:ny - 1 + oy, oz:nz - 1 + oz]
                any_in |= s
                all_in &= s
    cubes = np.argwhere(any_in & ~all_in)
    del any_in, all_in
    flat = F.ravel()
    strides = np.array([ny * nz, nz, 1], dtype=np.int64)

    keys_parts: list[np.ndarray] = []      # ключи рёбер треугольников (T×3)
    ref_parts: list[np.ndarray] = []       # (внутренний узел, внешний узел) у треугольника
    for start in range(0, len(cubes), chunk):
        c = cubes[start:start + chunk]
        corners = c[:, None, None, :] + _TET[None]               # K×6×4×3
        lin = corners @ strides                                   # K×6×4
        ins = flat[lin] > 0
        code = (ins * (1 << np.arange(4))).sum(axis=-1)           # K×6
        for case in range(1, 15):
            tris = _CASES[case]
            sel = np.nonzero(code == case)
            if not len(sel[0]):
                continue
            tl = lin[sel]                                         # S×4
            for tri in tris:
                k = np.empty((len(tl), 3), dtype=np.int64)
                for e, (a, b) in enumerate(tri):
                    lo, hi = min(a, b), max(a, b)
                    d = _TET[sel[1], hi] - _TET[sel[1], lo]       # направление ребра
                    did = d[:, 0] * 4 + d[:, 1] * 2 + d[:, 2]
                    k[:, e] = tl[:, lo] * 8 + did
                keys_parts.append(k)
                i0, o0 = tri[0]                                   # внутренняя и внешняя
                ref_parts.append(np.stack([tl[:, i0], tl[:, o0]], axis=1))
    if not keys_parts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    keys = np.concatenate(keys_parts)
    refs = np.concatenate(ref_parts)
    del keys_parts, ref_parts
    uniq, faces = np.unique(keys.ravel(), return_inverse=True)
    faces = faces.reshape(-1, 3).astype(np.int64)

    # Положение вершины на ребре (узел p, направление d).
    p = uniq // 8
    did = uniq % 8
    d = np.stack([did // 4, (did // 2) % 2, did % 2], axis=1)
    q = p + d @ strides
    a, b = flat[p].astype(np.float64), flat[q].astype(np.float64)
    t = np.clip(a / (a - b), EPS, 1.0 - EPS)
    pi = np.stack(np.unravel_index(p, F.shape), axis=1).astype(np.float64)
    verts = np.asarray(origin, dtype=np.float64) + h * (pi + t[:, None] * d)

    # Ориентация: нормаль наружу — от внутреннего узла к внешнему.
    def node(lin_idx):
        return np.stack(np.unravel_index(lin_idx, F.shape), axis=1).astype(np.float64)

    outward = node(refs[:, 1]) - node(refs[:, 0])
    v0, v1, v2 = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    normal = np.cross(v1 - v0, v2 - v0)
    flip = np.einsum("ij,ij->i", normal, outward) < 0
    faces[flip] = faces[flip][:, [0, 2, 1]]
    return verts, faces


def mesh(f: Field, *, name: str = "поле", min_body_mm3: float = 1.0,
         simplify: float = 0.0):
    """Поверхность поля телом Datum: замкнутая, без крупинок.

    `min_body_mm3` — тела меньше этого объёма выбрасываются: построение по
    сетке оставляет крупинки там, где поле едва переходит ноль, и прежде их
    чистили в каждом скрипте. ПОЛОСТИ (компоненты с отрицательным объёмом)
    остаются при любом размере — их судит страж, а не построение. Отбор идёт
    по сетке, до сборки тела: `Manifold.compose` полость внутри тела теряет.
    `simplify` — допуск упрощения сетки, мм (manifold `simplify`): сотни тысяч
    треугольников у скульптуры — это время каждой проверки.

    Острое ребро поле передаёт срезанным примерно на h/2 (у призмы 10×10×8
    при h = 0.25 объём −0.23 %): поле — для органики, точные рёбра — телами.
    Поверхность, дошедшая до края решётки, закрывается ровно по краю.
    """
    from manifold3d import Manifold, Mesh

    from datum.geom.solid import Solid

    g = f.grid
    # Рамка «глубоко снаружи»: переход нуля на ребре к ней — у самого края.
    v = np.pad(f.values.astype(np.float32), 1, mode="constant", constant_values=-1e6)
    origin = tuple(o - g.h for o in g.origin)
    verts, faces = _surface(v, origin, g.h)
    dropped = 0
    if len(faces):
        faces, dropped = _drop_crumbs(verts, faces, min_body_mm3)
    if not len(faces):
        return Solid.wrap(Manifold(), name=name, op="field.mesh", args=(g.h,))
    used, faces = np.unique(faces, return_inverse=True)
    faces = faces.reshape(-1, 3)
    man = Manifold(Mesh(vert_properties=verts[used].astype(np.float32),
                        tri_verts=faces.astype(np.uint32)))
    status = str(man.status()).removeprefix("Error.")
    if status != "NoError":
        raise RuntimeError(f"поверхность поля не замкнулась: {status} — это ошибка "
                           f"построения, а не детали; сообщите")
    if simplify > 0:
        man = man.simplify(simplify)
    return Solid.wrap(man, name=name, op="field.mesh", args=(g.h, dropped, simplify))


def _drop_crumbs(verts: np.ndarray, faces: np.ndarray, min_body: float):
    """Убрать связные куски сетки с объёмом от 0 до `min_body` (крупинки)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(verts)
    rows = np.repeat(np.arange(len(faces)), 3)
    graph = coo_matrix((np.ones(len(rows)), (rows, faces.ravel())),
                       shape=(len(faces), n)).tocsr()
    # Треугольники связаны, если делят вершину: граф «треугольник — вершина».
    adj = graph @ graph.T
    count, label = connected_components(adj, directed=False)
    if count == 1:
        return faces, 0
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    vol = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
    per = np.bincount(label, weights=vol, minlength=count)
    crumbs = (per >= 0) & (per < min_body)
    if not crumbs.any():
        return faces, 0
    return faces[~crumbs[label]], int(crumbs.sum())
