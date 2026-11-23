"""Вершины, слипающиеся в файле: найти и развести до записи.

ОТКУДА БЕДА. Ядро считает в двойной точности, а STL и 3MF хранят координаты во
float32. Две вершины, разные в ядре, но совпавшие после округления, при чтении
сливаются в одну — и в месте, где два листа поверхности касались точкой или
ребром (слои органики, прорезь у края цилиндра), сетка перестаёт быть
замкнутой: «меш не замкнут», хотя деталь в ядре замкнута. Так `export`
отказывал на подставке под мышь, и агент разводил вершины в скрипте детали
сам (`_separate_pinches`). Здесь то же — в одном месте и для всех.

ПОЧЕМУ К ЦЕНТРУ СВОИХ ГРАНЕЙ. Вершина сдвигается к центроиду смежных с ней
граней — то есть внутрь СВОЕГО листа поверхности, прочь от касания. Сдвиг —
микроны (`GAP_MM`), меньше любого разрешения печати, и не больше половины
расстояния до центроида: мелкий треугольник не выворачивается.

ПОЧЕМУ ЭТО НЕ ПРЯЧЕТ ДЕФЕКТ. Касание двух листов в точке — не ошибка
конструкции, а свойство сетки, которого слайсер в float32 не различит. Сколько
вершин сдвинуто и на сколько — уходит в отчёт выгрузки; записанный файл
по-прежнему перечитывается и сверяется (`export._verify`) — он и судья.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["Separation", "separate_coincident", "coincident_pairs", "GAP_MM"]

#: На сколько разводятся слипающиеся вершины за проход, мм. Микрон: в тридцать
#: раз больше шага float32 у края стола в 256 мм (3·10⁻⁵) и в сотни раз меньше
#: слоя. Вершина, которую пришлось двигать во втором проходе, уходит на два.
GAP_MM = 1e-3

#: Сколько проходов делать. Второй нужен, когда сдвиг упёрся в половину
#: расстояния до центроида у крошечных граней и пара ещё близка.
ROUNDS = 3


@dataclass(frozen=True, slots=True)
class Separation:
    verts: np.ndarray
    moved: int                 # сколько вершин сдвинуто
    max_shift: float           # наибольший сдвиг, мм
    left: int                  # сколько пар так и осталось слипающимися

    def note(self) -> str:
        if not self.moved and not self.left:
            return ""
        text = (f"разведено вершин, слипавшихся во float32: {self.moved} "
                f"(наибольший сдвиг {self.max_shift * 1000:.2f} мкм)")
        if self.left:
            text += f"; развести не удалось пар: {self.left}"
        return text


def _radius(verts: np.ndarray) -> float:
    """Ближе этого две вершины в файле неразличимы: четыре шага float32."""
    span = float(np.max(np.abs(verts))) if verts.size else 1.0
    return 4.0 * float(np.spacing(np.float32(max(span, 1.0))))


def coincident_pairs(verts: np.ndarray) -> np.ndarray:
    """Пары разных вершин, которые после округления к float32 неразличимы (K×2)."""
    from scipy.spatial import cKDTree

    if len(verts) < 2:
        return np.zeros((0, 2), dtype=np.int64)
    tree = cKDTree(np.asarray(verts, dtype=np.float64))
    return tree.query_pairs(_radius(verts), output_type="ndarray").astype(np.int64)


def _ring_centroids(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Центроид смежных граней у каждой вершины (у одиноких — она сама)."""
    centre = verts[faces].mean(axis=1)                    # M×3
    acc = np.zeros_like(verts)
    count = np.zeros(len(verts))
    for k in range(3):
        np.add.at(acc, faces[:, k], centre)
        np.add.at(count, faces[:, k], 1.0)
    lonely = count == 0
    count[lonely] = 1.0
    out = acc / count[:, None]
    out[lonely] = verts[lonely]
    return out


def separate_coincident(verts: np.ndarray, faces: np.ndarray, *,
                        gap: float = GAP_MM) -> Separation:
    """Развести вершины, которые слиплись бы при записи во float32.

    В каждой группе слипающихся остаётся на месте вершина с меньшим номером,
    остальные сдвигаются к центроиду своих граней. Связность сетки не
    меняется: двигаются координаты, а не номера.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    v = np.array(verts, dtype=np.float64, copy=True)
    f = np.asarray(faces, dtype=np.int64)
    original = v.copy()
    moved: set[int] = set()
    # Только вершины, на которые ссылаются грани: после ремапа слияния ядра в
    # массиве остаются неиспользуемые двойники, и они давали бы ложное
    # «развести не удалось» и лишний сдвиг используемой.
    used = np.unique(f)

    def pairs_of(points):
        found = coincident_pairs(points[used])
        return used[found] if len(found) else found

    pairs = pairs_of(v)
    for _ in range(ROUNDS):
        if not len(pairs):
            break
        n = len(v)
        graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
        _, label = connected_components(graph, directed=False)
        involved = np.unique(pairs.ravel())
        groups = label[involved]
        # Первая (наименьшая) вершина каждой группы остаётся на месте.
        order = np.lexsort((involved, groups))
        involved, groups = involved[order], groups[order]
        first = np.ones(len(involved), dtype=bool)
        first[1:] = groups[1:] != groups[:-1]
        movers = involved[~first]
        centre = _ring_centroids(v, f)[movers]
        delta = centre - v[movers]
        dist = np.linalg.norm(delta, axis=1)
        ok = dist > 0
        step = np.minimum(gap, dist[ok] / 2)
        v[movers[ok]] += delta[ok] / dist[ok, None] * step[:, None]
        moved.update(movers[ok].tolist())
        pairs = pairs_of(v)
    shift = float(np.max(np.linalg.norm(v - original, axis=1))) if moved else 0.0
    return Separation(verts=v, moved=len(moved), max_shift=shift, left=int(len(pairs)))
