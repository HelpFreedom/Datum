"""Карман по направлению вставки: объём, заметаемый предметом на пути к посадке.

ЗАЧЕМ. `footprint` даёт тень по Z, а мышь в подставку входит под углом к своей
оси: агенту пришлось строить карман руками — сечения полости держалки,
переведённые в мир и протянутые вверх. Здесь то же — операцией, для любого
направления.

КАК. Мир поворачивается так, чтобы направление ВЫНИМАНИЯ (против вставки)
смотрело вверх. Дальше по слоям толщиной `layer`: карман в слое — тень всего
предмета НИЖЕ ВЕРХНЕЙ КРОМКИ слоя, выдавленная на весь слой. Предмет, идущий
вверх, проходит через слой всеми своими сечениями ниже этой кромки, поэтому
карман содержит его в КАЖДОМ положении пути — поднутрений нет по построению.
Тень у нижней кромки была бы теснее: агент так и получил задевание у кончика
мыши, пока не взял верхнюю. Цена — карман шире точного не больше чем на
наклон стенки предмета × толщину слоя.

Зазор — раздувом тени в плоскости слоя и опусканием кромки на столько же:
это оценка сверху для смещения предмета во все стороны.
"""

from __future__ import annotations

import math

import numpy as np

from datum.geom.solid import Solid

__all__ = ["insertion_pocket", "removal_frame"]


def _unit(direction) -> np.ndarray:
    if isinstance(direction, str):
        from datum.checks.motion import direction as parse

        v = np.asarray(parse(direction), dtype=float)
    else:
        v = np.asarray(direction, dtype=float)
    n = float(np.linalg.norm(v))
    if not (n > 0 and math.isfinite(n)):
        raise ValueError(f"направление {direction!r} — нулевое или не число")
    return v / n


def removal_frame(insert) -> np.ndarray:
    """Поворот 3×3: направление ВЫНИМАНИЯ (−вставка) → +Z, строки — новые оси."""
    w = -_unit(insert)
    helper = np.array([1.0, 0.0, 0.0]) if abs(w[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(helper, w)
    u /= np.linalg.norm(u)
    v = np.cross(w, u)
    return np.stack([u, v, w])


def _transform(man, rot: np.ndarray):
    mat = np.zeros((3, 4))
    mat[:, :3] = rot
    return man.transform(mat)


def insertion_pocket(solid: Solid, insert, *, clearance: float = 0.0, layer: float = 0.25,
                     travel: float | None = None, name: str = "") -> Solid:
    """Карман, в который предмет входит по `insert` (направление ВСТАВКИ).

    `insert` — «-Z», «+X»… или вектор: куда предмет движется, становясь на
    место (как `insert` в assembly.yaml). `travel` — сколько пути до посадки
    карман обязан пропустить (по умолчанию — высота предмета вдоль пути: так
    карман выходит сквозь любую держалку не выше самого предмета). `layer` —
    толщина слоя, мм: тоньше — точнее и дольше.

    Возвращает тело кармана в мировых координатах; вычесть его из держалки —
    `diff(держалка, [карман])`.
    """
    from manifold3d import Manifold

    from datum.geom.footprint import _polys_from_cross_section
    from datum.geom.profile2d import cs

    if layer <= 0:
        raise ValueError(f"толщина слоя должна быть положительной, получено {layer}")
    if clearance < 0:
        raise ValueError(f"зазор отрицательный: {clearance}")
    rot = removal_frame(insert)
    m = _transform(solid.manifold, rot)
    if m.is_empty():
        raise ValueError("предмет пуст — карману не по чему строиться")
    x0, y0, z0, x1, y1, z1 = (float(v) for v in m.bounding_box())
    height = z1 - z0
    reach = height if travel is None else float(travel)
    if reach < 0:
        raise ValueError(f"путь отрицательный: {travel}")
    pad = 1.0 + clearance

    bottom = z0 - clearance
    n = max(1, int(math.ceil((z1 - bottom) / layer)))
    prisms = []
    for k in range(n):
        lo = bottom + k * layer
        hi = min(lo + layer, z1)
        # Тень всего, что ниже верхней кромки слоя (с запасом на зазор).
        cut = Manifold.cube((x1 - x0 + 2 * pad, y1 - y0 + 2 * pad,
                             hi + clearance - (z0 - pad))) \
            .translate((x0 - pad, y0 - pad, z0 - pad))
        below = m ^ cut
        if below.is_empty():
            continue
        shadow = _polys_from_cross_section(below.project())
        if clearance > 0:
            shadow = shadow.buffer(clearance, join_style=1)
        if shadow.is_empty:
            continue
        top = hi if k < n - 1 else z1 + reach        # последний слой — на весь путь
        prisms.append(Manifold.extrude(cs(shadow), top - lo).translate((0, 0, lo)))
    if not prisms:
        raise ValueError("предмет пуст — карману не по чему строиться")
    pocket = Manifold.batch_boolean(prisms, _union_op()) if len(prisms) > 1 else prisms[0]
    pocket = _transform(pocket, rot.T)
    return Solid.wrap(pocket, name=name or f"карман {solid.name or ''}".strip(),
                      op="insertion_pocket",
                      args=(tuple(np.round(-rot[2], 6)), clearance, layer, reach))


def _union_op():
    from manifold3d import OpType

    return OpType.Add
