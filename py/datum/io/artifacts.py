"""Публикация геометрии в кеш артефактов — то, что видит окно.

Байтов меша по сети не ходит НИКОГДА. Деталь кладётся в кеш как GLB, а наружу
уезжает только `{digest, url}`; браузер тянет `/obj/<digest>.glb` с заголовком
`immutable`. Отпечаток и есть ключ кеша браузера, поэтому правка параметра, не
изменившая деталь, стоит ноль байт и ноль раскодирования.

Отпечаток здесь — содержимое САМОГО GLB, а не тела. Так и должно быть: по этому
адресу лежат именно эти байты, и `immutable` не врёт. Смена цвета детали или
версии писателя даёт другой адрес, и браузер честно перечитает файл, вместо
того чтобы показывать вчерашнее из кеша.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from datum.io.glb import DEFAULT_COLOR, solid_to_glb

__all__ = ["publish_glb", "view_of"]


def view_of(solid, *, name: str = "", color: str | None = None) -> tuple[bytes, dict]:
    """GLB детали и сводка о ней. Вершины читаются один раз на оба."""
    from datum.geom.convert import vertices_faces

    verts, faces = vertices_faces(solid.manifold)
    from datum.io.glb import mesh_to_glb

    blob = mesh_to_glb(verts, faces, color=color, name=name or solid.name)
    if len(verts):
        lo = np.asarray(verts, dtype=float).min(axis=0)
        hi = np.asarray(verts, dtype=float).max(axis=0)
        bbox = [round(float(v), 4) for v in (*lo, *hi)]
    else:
        bbox = [0.0] * 6
    return blob, {"tris": int(len(faces)), "verts": int(len(verts)), "bbox": bbox}


def publish_glb(cache, solid, *, name: str, color: str | None = None) -> dict[str, Any]:
    """Положить деталь в кеш артефактов и вернуть адрес для окна."""
    blob, summary = view_of(solid, name=name, color=color)
    digest = cache.put_blob(blob, "glb", kind="geom",
                            meta={"part": name, "color": color or DEFAULT_COLOR})
    return {"digest": digest, "url": f"/obj/{digest}.glb",
            "bytes": len(blob), **summary}
