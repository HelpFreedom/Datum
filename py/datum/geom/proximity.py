"""Расстояние от множества точек до поверхности тела — быстро и точно.

Нужно, чтобы найти, чем деталь КАСАЕТСЯ соседа (вал в отверстии, подшипник в
гнезде): опора звена в расчёте прочности. Прежде это делал `trimesh.contains`
со сдвинутыми точками — лучи из Python: на плече руки 178 из 211 с всего расчёта.

Как: поверхность соседа обходится точками-представителями (вершины, центры и
точки по рёбрам не реже `spacing`), у каждой — номер её треугольника. Для точки
запроса берутся треугольники `k` ближайших представителей, и до них считается
ТОЧНОЕ расстояние (`field._point_triangle`). У длинного узкого треугольника САПР
(«щепка» по образующей отверстия) представители идут вдоль его рёбер, поэтому он
попадает в кандидаты, даже когда точка над его серединой. Знак — по нормали
ближайшего треугольника (обход manifold — наружу): так ловится и посадка с
натягом, где поверхность детали ушла внутрь соседа.
"""

from __future__ import annotations

import numpy as np

from datum.geom.solid import Solid

__all__ = ["surface_distance", "mesh_distance"]


def _representatives(v: np.ndarray, f: np.ndarray, spacing: float):
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    pts = [a, b, c, (a + b + c) / 3.0]
    ids = [np.arange(len(f))] * 4
    for p, q in ((a, b), (b, c), (c, a)):
        n = np.maximum(np.ceil(np.linalg.norm(q - p, axis=1) / spacing).astype(int), 1)
        rep = np.repeat(np.arange(len(f)), n - 1)
        if rep.size:
            # доли 1/n … (n−1)/n по ребру каждого треугольника
            start = np.repeat(np.cumsum(n - 1) - (n - 1), n - 1)
            k = np.arange(rep.size) - start + 1
            t = (k / n[rep])[:, None]
            pts.append(p[rep] * (1 - t) + q[rep] * t)
            ids.append(rep)
    # Внутри широких треугольников — сетка по барицентрическим долям: над серединой
    # большой грани ближайшие представители рёбер могли принадлежать соседям, и
    # расстояние выходило завышенным на миллиметры. Щепкам (высота меньше шага)
    # хватает точек на рёбрах.
    e1, e2 = b - a, c - a
    area2 = np.linalg.norm(np.cross(e1, e2), axis=1)
    longest = np.maximum.reduce([np.linalg.norm(e1, axis=1), np.linalg.norm(e2, axis=1),
                                 np.linalg.norm(c - b, axis=1)])
    wide = np.where(area2 / np.maximum(longest, 1e-12) > spacing)[0]
    for i in wide:
        n = int(np.ceil(longest[i] / spacing))
        u, w = np.meshgrid(np.arange(1, n), np.arange(1, n), indexing="ij")
        keep = u + w < n
        u, w = u[keep] / n, w[keep] / n
        pts.append(a[i] + u[:, None] * e1[i] + w[:, None] * e2[i])
        ids.append(np.full(len(u), i))
    return np.concatenate(pts), np.concatenate(ids)


def surface_distance(solid: Solid, points: np.ndarray, *, spacing: float = 1.0,
                     candidates: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """(расстояние до поверхности, внутри ли) для каждой точки, мм."""
    from datum.geom.convert import vertices_faces
    from datum.geom.measure import man

    v, f = vertices_faces(man(solid))
    return mesh_distance(v, f, points, spacing=spacing, candidates=candidates)


def mesh_distance(vertices, faces, points, *, spacing: float = 1.0,
                  candidates: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """То же для поверхности, заданной массивами (треугольники обходом наружу)."""
    from scipy.spatial import cKDTree

    from datum.geom.field import _point_triangle

    v = np.asarray(vertices, float)
    f = np.asarray(faces, np.int64)
    p = np.asarray(points, float).reshape(-1, 3)
    if len(f) == 0 or len(p) == 0:
        return np.full(len(p), np.inf), np.zeros(len(p), bool)
    reps, owner = _representatives(v, f, spacing)
    tree = cKDTree(reps)
    k = min(candidates, len(reps))
    _, idx = tree.query(p, k=k, workers=-1)
    idx = idx.reshape(len(p), k)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    best = np.full(len(p), np.inf)
    who = np.zeros(len(p), np.int64)
    for j in range(k):
        t = owner[idx[:, j]]
        d = _point_triangle(p, a[t], b[t], c[t])
        closer = d < best
        best[closer] = d[closer]
        who[closer] = t[closer]
    n = np.cross(b[who] - a[who], c[who] - a[who])
    inside = np.einsum("ij,ij->i", p - a[who], n) < 0
    return best, inside
