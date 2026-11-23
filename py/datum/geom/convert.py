"""Мост между manifold3d и trimesh.

manifold3d — каноническое представление геометрии Datum. trimesh нужен для
ввода-вывода, лучевых запросов и всего, чего нет в manifold.

О ремапе вершин. В `mf.py` рабочего процесса CAD стояло предупреждение, что
`to_mesh()` оставляет совпадающие граничные вершины расщеплёнными, и без ремапа
`merge_from_vert → merge_to_vert` меш «разваливается на лоскуты». Замер на
manifold3d 3.5.3 показал, что эти массивы пусты и ремап ничего не меняет
(проверено на кубе, разности, 168 канавках и объединении 50 сфер). Код ремапа
оставлен как защита на случай других версий; тест `test_convert.py` проверяет
эквивалентность, а не наличие ремапа.
"""

from __future__ import annotations

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh

__all__ = ["to_trimesh", "from_trimesh", "from_arrays", "vertices_faces"]


def vertices_faces(man: Manifold) -> tuple[np.ndarray, np.ndarray]:
    """Вершины (float64, N×3) и треугольники (int64, M×3) из Manifold."""
    mesh = man.to_mesh()
    verts = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    faces = np.asarray(mesh.tri_verts, dtype=np.int64)

    # Защитный ремап: если версия manifold оставила вершины расщеплёнными.
    merge_from = np.asarray(mesh.merge_from_vert, dtype=np.int64).ravel()
    merge_to = np.asarray(mesh.merge_to_vert, dtype=np.int64).ravel()
    if merge_from.size and merge_from.size == merge_to.size and faces.size:
        remap = np.arange(len(verts), dtype=np.int64)
        remap[merge_from] = merge_to
        faces = remap[faces]

    return verts, faces


def to_trimesh(man: Manifold, *, process: bool = False) -> trimesh.Trimesh:
    """Manifold → Trimesh.

    process=False по умолчанию: geometry уже корректна, а обработка trimesh
    сливает вершины и может незаметно изменить топологию.
    """
    verts, faces = vertices_faces(man)
    if faces.size == 0:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64))
    return trimesh.Trimesh(vertices=verts, faces=faces, process=process)


def from_arrays(vertices, faces) -> Manifold:
    """Вершины N×3 и треугольники M×3 → Manifold (вершины — float32, как в ядре).

    np.array(..., order="C") копирует, и это намеренно: привязка manifold3d
    принимает только записываемые C-непрерывные массивы, а вход может быть
    представлением только для чтения (у кеша — `np.frombuffer`).
    """
    verts = np.array(vertices, dtype=np.float32, order="C")
    tris = np.array(faces, dtype=np.uint32, order="C")
    return Manifold(Mesh(vert_properties=verts, tri_verts=tris))


def from_trimesh(mesh: trimesh.Trimesh) -> Manifold:
    """Trimesh → Manifold. Ошибку не прячем: `status()` расскажет о проблеме."""
    return from_arrays(mesh.vertices, mesh.faces)
