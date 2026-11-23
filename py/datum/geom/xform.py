"""Преобразования тел. Порт `mf.py` плюс локальные системы координат."""

from __future__ import annotations

import numpy as np

from datum.geom.solid import Solid

__all__ = ["move", "rotate", "rot_about", "mirror", "scale", "frame", "place",
           "array_linear", "array_polar", "upright", "UPRIGHT",
           "anchor", "align", "stack", "centre_on"]

_AXIS = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def move(s: Solid, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> Solid:
    """Перенос на (dx, dy, dz), мм."""
    return s.derive(s.manifold.translate((dx, dy, dz)), "move", dx=dx, dy=dy, dz=dz)


def rotate(s: Solid, rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> Solid:
    """Поворот вокруг осей, проходящих через начало координат (градусы)."""
    return s.derive(s.manifold.rotate((rx, ry, rz)), "rotate", rx=rx, ry=ry, rz=rz)


#: Как поставить деталь на стол, чтобы названная ось смотрела вверх. ОДНО
#: определение на всех: проверки печатности считают эту позу, выгрузка пишет
#: её в файл. Разойдись они — проверено было бы одно, а напечатано другое.
UPRIGHT = {
    "+Z": {},
    "-Z": {"rx": 180.0},
    "+Y": {"rx": 90.0},
    "-Y": {"rx": -90.0},
    "+X": {"ry": -90.0},
    "-X": {"ry": 90.0},
}


def upright(s: Solid, up: str) -> Solid:
    """Повернуть деталь так, чтобы ось `up` (+Z, -Z, +X, -X, +Y, -Y) смотрела вверх."""
    key = str(up).strip().upper()
    if key not in UPRIGHT:
        raise ValueError(f"ось вверх задаётся как +Z, -Z, +X, -X, +Y или -Y, "
                         f"получено {up!r}")
    turn = UPRIGHT[key]
    return s if not turn else rotate(s, **turn)


def rot_about(s: Solid, axis: str, deg: float, c: tuple[float, float, float]) -> Solid:
    """Поворот вокруг оси ('x'|'y'|'z'), проходящей через точку c."""
    ax = axis.lower()
    if ax not in _AXIS:                       # pragma: no cover
        raise ValueError(f"ось должна быть 'x', 'y' или 'z', получено {axis!r}")
    r = tuple(deg * v for v in _AXIS[ax])
    man = s.manifold.translate((-c[0], -c[1], -c[2])).rotate(r).translate(tuple(c))
    return s.derive(man, "rot_about", axis=ax, deg=deg, c=tuple(c))


def mirror(s: Solid, normal: tuple[float, float, float]) -> Solid:
    """Зеркало относительно плоскости через начало координат с данной нормалью."""
    return s.derive(s.manifold.mirror(normal), "mirror", normal=tuple(normal))


def scale(s: Solid, sx: float = 1.0, sy: float | None = None, sz: float | None = None) -> Solid:
    """Масштаб относительно начала координат; sy, sz по умолчанию равны sx."""
    sy = sx if sy is None else sy
    sz = sx if sz is None else sz
    return s.derive(s.manifold.scale((sx, sy, sz)), "scale", sx=sx, sy=sy, sz=sz)


def frame(origin=(0.0, 0.0, 0.0), *, rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> np.ndarray:
    """Локальная система координат как матрица 4×4.

    Нужна там же, где в корпусе: деталь удобно строить в своих осях, а ставить в
    сборку под углом (в `phone-desk-mount` так задавался наклон телефона 20°).
    """
    a, b, c = np.deg2rad([rx, ry, rz])
    cx, sx = np.cos(a), np.sin(a)
    cy, sy = np.cos(b), np.sin(b)
    cz, sz = np.cos(c), np.sin(c)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    m = np.eye(4)
    m[:3, :3] = Rz @ Ry @ Rx
    m[:3, 3] = origin
    return m


def place(s: Solid, m: np.ndarray) -> Solid:
    """Поставить тело по матрице 4×4 из `frame`."""
    m = np.asarray(m, dtype=float)
    if m.shape != (4, 4):                     # pragma: no cover
        raise ValueError(f"нужна матрица 4×4, получена {m.shape}")
    return s.derive(s.manifold.transform(m[:3, :]), "place")


def array_linear(s: Solid, n: int, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> Solid:
    """Линейный массив копий (включая исходную)."""
    from datum.geom.booleans import union

    return union([move(s, dx * i, dy * i, dz * i) for i in range(n)])


def array_polar(s: Solid, n: int, *, axis: str = "z",
                c: tuple[float, float, float] = (0.0, 0.0, 0.0),
                degrees: float = 360.0) -> Solid:
    """Круговой массив копий вокруг оси через точку c."""
    from datum.geom.booleans import union

    return union([rot_about(s, axis, degrees * i / n, c) for i in range(n)])


# ---------------------------------------------------------------- точки габарита
#: Слово стороны → (ось, знак). «+X-Y+Z» — угол, «+X-Y» — середина ребра,
#: «+Z» — центр верхней стороны габарита; оси без слова — посередине.
_SIDE = {"X": 0, "Y": 1, "Z": 2}


def _anchor_spec(which: str) -> dict[int, int]:
    import re

    text = str(which).replace(" ", "").upper()
    parts = re.findall(r"([+-])([XYZ])", text)
    if not parts or "".join(s + a for s, a in parts) != text:
        raise ValueError(f"точка габарита задаётся как «+X», «+X-Y» или «+X-Y+Z», а не {which!r}")
    spec: dict[int, int] = {}
    for sign, axis in parts:
        k = _SIDE[axis]
        if k in spec:
            raise ValueError(f"в {which!r} ось {axis} названа дважды")
        spec[k] = 1 if sign == "+" else -1
    return spec


def anchor(s: Solid, which: str = "") -> tuple[float, float, float]:
    """Точка габарита тела по словам: «+Z» — центр верха, «-Z» — центр низа,
    «+X-Y» — середина ребра, «+X-Y+Z» — угол; пусто — центр габарита.

    Форсирует расчёт (габарит), как и `bbox`. Нужна, чтобы ставить детали друг
    к другу без ручной арифметики координат — на ней ошибаются чаще всего."""
    from datum.geom.measure import bbox

    lo, hi = bbox(s)
    spec = _anchor_spec(which) if which else {}
    return tuple(float(hi[k] if spec.get(k) == 1 else lo[k] if spec.get(k) == -1
                       else (lo[k] + hi[k]) / 2) for k in range(3))


def align(s: Solid, which: str, to) -> Solid:
    """Сдвинуть тело так, чтобы его точка `which` (как у `anchor`) встала в `to` —
    точку (x, y, z) или точку габарита другого тела (`anchor(основа, "+Z")`)."""
    here = anchor(s, which)
    x, y, z = (float(v) for v in to)
    return move(s, x - here[0], y - here[1], z - here[2])


def stack(s: Solid, base: Solid, side: str = "+Z", gap: float = 0.0) -> Solid:
    """Приставить тело к стороне `side` другого тела, по центру этой стороны.

    «+Z» — поставить сверху (низ тела на верх основы), «-X» — прислонить слева
    и т. д. `gap` — зазор между ними (печатный зазор посадки — параметр проекта).
    """
    spec = _anchor_spec(side)
    if len(spec) != 1:
        raise ValueError(f"stack: сторона — одна ось («+Z», «-X»), а не {side!r}")
    (k, sign), = spec.items()
    target = list(anchor(base, side))
    target[k] += sign * gap
    opposite = ("-" if sign == 1 else "+") + "XYZ"[k]
    return align(s, opposite, target)


def centre_on(s: Solid, base: Solid, axes: str = "xy") -> Solid:
    """Выровнять центр габарита тела по центру другого тела на осях `axes`."""
    if not axes or any(a not in "xyz" for a in axes.lower()):
        raise ValueError(f"centre_on: оси — из «xyz», а не {axes!r}")
    here, there = anchor(s), anchor(base)
    shift = [there[k] - here[k] if "xyz"[k] in axes.lower() else 0.0 for k in range(3)]
    return move(s, *shift)
