"""Что изменилось в ФОРМЕ между версиями — картинкой.

`snapshot_diff` сравнивает числа: параметры, объёмы, переходы проверок. Но на
вопрос «что именно стало другим» число объёма не отвечает: +12 мм³ — это
ребро выросло или отверстие сдвинулось? Здесь две формы одной детали
вычитаются друг из друга, и лист видов красит:

  * серым   — общее для обеих версий (A ∩ B);
  * зелёным — добавилось (B − A);
  * красным — убралось (A − B).

Идея — из cadcraft (COMPARE красит различия двух чертежей). Формы берутся из
`.datum/snapshot-meshes/` (их кладёт `Snapshot.create`); у снимка, снятого
раньше, или у проекта, распакованного без `.datum/`, формы нет — так и
говорится, а не рисуется пустота.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["GeometryDiff", "geometry_diff", "COMMON", "ADDED", "REMOVED"]

COMMON, ADDED, REMOVED = "#9aa4b2", "#3fae5a", "#d9534f"

#: Меньше — не изменение, а шум булевой операции на совпадающих гранях.
NOISE_MM3 = 1e-3


@dataclass
class GeometryDiff:
    #: деталь → (добавилось, убралось), мм³
    volumes: dict[str, tuple[float, float]] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)
    #: детали, формы которых для одной из версий нет
    missing: list[str] = field(default_factory=list)
    png: bytes | None = None

    def as_dict(self) -> dict:
        return {"volumes": {n: {"added_mm3": round(a, 3), "removed_mm3": round(r, 3)}
                            for n, (a, r) in sorted(self.volumes.items())},
                "unchanged": sorted(self.unchanged), "missing": sorted(self.missing)}

    def lines(self) -> list[str]:
        out = []
        for name, (added, removed) in sorted(self.volumes.items()):
            out.append(f"  {name}: +{added:.1f} мм³ добавилось, −{removed:.1f} мм³ убралось")
        if self.unchanged:
            out.append(f"  форма не изменилась: {', '.join(sorted(self.unchanged))}")
        if self.missing:
            out.append(f"  формы нет (снимок снят до картинок или проект распакован без "
                       f".datum/): {', '.join(sorted(self.missing))}")
        return out


def _volume(solid) -> float:
    from datum.geom.measure import vol

    return 0.0 if solid is None or solid.manifold.is_empty() else float(vol(solid))


def geometry_diff(root: str | Path, a, b) -> GeometryDiff:
    """Сравнить формы деталей снимков `a` и `b` (Snapshot) и нарисовать листы
    «было» и «стало»."""
    from datum.doc.snapshot import kept_geometry
    from datum.viz.render import render_views

    out = GeometryDiff()
    # Два листа, а не один: убранное лежит там, где у новой версии обычно
    # стоит другое (крышка выросшего корпуса уехала вверх — старое её место
    # теперь внутри стенок), и на одном непрозрачном виде красное пряталось
    # за серым и зелёным. Поэтому «было» — версия A с красным убранным,
    # «стало» — версия B с зелёным добавленным, в одном кадре и масштабе.
    before: list[tuple[Any, str]] = []
    after: list[tuple[Any, str]] = []
    for name in sorted(set(a.parts) | set(b.parts)):
        pa, pb = a.parts.get(name), b.parts.get(name)
        da = pa.result_digest if pa is not None and pa.ok else ""
        db = pb.result_digest if pb is not None and pb.ok else ""
        sa = kept_geometry(root, da, name=name) if da else None
        sb = kept_geometry(root, db, name=name) if db else None
        if (da and sa is None) or (db and sb is None):
            out.missing.append(name)
            continue
        if da and da == db:
            out.unchanged.append(name)
            before.append((sa, COMMON))
            after.append((sb, COMMON))
            continue
        if sa is None and sb is None:
            continue                                  # не собралась ни в одной
        if sa is None:
            after.append((sb, ADDED))
            out.volumes[name] = (_volume(sb), 0.0)
            continue
        if sb is None:
            before.append((sa, REMOVED))
            out.volumes[name] = (0.0, _volume(sa))
            continue
        common, added, removed = sa ^ sb, sb - sa, sa - sb
        out.volumes[name] = (_volume(added), _volume(removed))
        before += [(common, COMMON), (removed, REMOVED)]
        after += [(common, COMMON), (added, ADDED)]

    meshes = {"было": _mesh(before), "стало": _mesh(after)}
    present = [m for m in meshes.values() if m is not None]
    if not present:
        return out
    every = np.concatenate([m[0][np.unique(m[1])] for m in present])
    frame = (tuple(every.min(0)), tuple(every.max(0)))
    sheets = []
    for (label, mesh), tag, legend in zip(
            meshes.items(), (a.tag, b.tag),
            ((("общее", COMMON), ("убралось", REMOVED)),
             (("общее", COMMON), ("добавилось", ADDED)))):
        if mesh is None:
            continue
        verts, faces, colors = mesh
        sheets.append(render_views(verts, faces, title=f"{label}: {tag}", note=_note(out),
                                   face_colors=colors, legend=legend, frame=frame,
                                   tile=320))
    out.png = _stack(sheets)
    return out


def _note(out: GeometryDiff) -> str:
    return ("; ".join(f"{n}: +{ad:.1f} / −{rm:.1f} мм³" for n, (ad, rm) in
                      sorted(out.volumes.items())) or "форма не изменилась")


def _mesh(pieces):
    """Куски (тело, цвет) → одна сетка с цветами граней; пусто — None."""
    from datum.geom.convert import vertices_faces

    verts, faces, colors, offset = [], [], [], 0
    for solid, color in pieces:
        if solid is None or solid.manifold.is_empty():
            continue
        if color != COMMON and _volume(solid) < NOISE_MM3:
            continue
        v, f = vertices_faces(solid.manifold)
        if not len(f):
            continue
        rgb = np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.uint8)
        verts.append(v)
        faces.append(f + offset)
        colors.append(np.tile(rgb, (len(f), 1)))
        offset += len(v)
    if not faces:
        return None
    return np.concatenate(verts), np.concatenate(faces), np.concatenate(colors)


def _stack(sheets: list[bytes]) -> bytes:
    """Листы PNG — один под другим."""
    import io

    from PIL import Image

    images = [Image.open(io.BytesIO(png)).convert("RGB") for png in sheets]
    width = max(i.width for i in images)
    canvas = Image.new("RGB", (width, sum(i.height for i in images)), images[0].getpixel((0, 0)))
    y = 0
    for image in images:
        canvas.paste(image, (0, y))
        y += image.height
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
