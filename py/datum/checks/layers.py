"""Выдержит ли деталь руки: изгиб между слоями в самом слабом сечении.

Откуда проверка. Ядро Gear Cube «разваливалось прямо в процессе сборки, когда
прикручивал первую шестерню», — нога ломалась там, где гнездо под гайку
съедает сечение. Ни одна проверка этого не видела: печатность была в порядке
(стенка есть, нависаний нет), а прочность узла никто не объявлял, потому что
нагрузки «в руках» в проекте нет.

Что считается. Каждый слой (в позе печати) делит деталь на два куска: один
держат, на край другого давят вбок — рукой, отвёрткой, полом при падении.
Плечо — у МЕНЬШЕГО куска: он и отломится. В слое — изгибающий момент F·плечо,
и слои РАЗРЫВАЕТ: слабейшее направление печатной детали — между слоями
(`MAT_TENSILE_Z`). Напряжение — момент на момент сопротивления сечения слоя
относительно его слабой оси (сила может прийти с любой стороны).

Это оценка порядка, а не расчёт: концентрация у острых углов гнезда не
учтена (она ещё хуже), сила и её точка — догадка того, кто объявляет. Зато
она находит место — слой, где сечение съедено, — и говорит число, с которым
это место сравнить.
"""

from __future__ import annotations

import time

import numpy as np

from datum.checks.result import CheckResult
from datum.geom.solid import Solid

__all__ = ["layer_strength", "section_bending"]


def _ring_moments(ring: np.ndarray) -> tuple[float, float, float, float, float, float]:
    """Площадь и моменты многоугольника относительно начала координат (Грин):
    (A, Sx, Sy, Ixx, Iyy, Ixy) со знаком обхода."""
    x, y = ring[:-1, 0], ring[:-1, 1]
    x1, y1 = ring[1:, 0], ring[1:, 1]
    cross = x * y1 - x1 * y
    a = cross.sum() / 2
    sx = ((y + y1) * cross).sum() / 6          # ∫ y dA
    sy = ((x + x1) * cross).sum() / 6          # ∫ x dA
    ixx = ((y * y + y * y1 + y1 * y1) * cross).sum() / 12
    iyy = ((x * x + x * x1 + x1 * x1) * cross).sum() / 12
    ixy = ((x * y1 + 2 * x * y + 2 * x1 * y1 + x1 * y) * cross).sum() / 24
    return float(a), float(sx), float(sy), float(ixx), float(iyy), float(ixy)


def section_bending(polygon) -> tuple[float, float]:
    """(площадь, наименьший момент сопротивления W, мм³) односвязного куска
    сечения — относительно его СЛАБОЙ главной оси."""
    rings = [np.asarray(polygon.exterior.coords, float)]
    rings += [np.asarray(r.coords, float) for r in polygon.interiors]
    total = np.zeros(6)
    for k, ring in enumerate(rings):
        m = np.array(_ring_moments(ring))
        # Внешний контур — плюсом, дыры — минусом, каким бы ни был обход.
        total += (abs(m[0]) / m[0] if m[0] else 1.0) * m * (1.0 if k == 0 else -1.0)
    area, sx, sy, ixx, iyy, ixy = total
    if area <= 1e-12:
        return 0.0, 0.0
    cx, cy = sy / area, sx / area
    ixx -= area * cy * cy
    iyy -= area * cx * cx
    ixy -= area * cx * cy
    # Момент инерции относительно оси через центр вдоль единичного u:
    # I(u) = uᵀ·J·u, J = [[Ixx, −Ixy], [−Ixy, Iyy]]. Слабая ось — собственный
    # вектор наименьшего собственного числа (сверено с брусом b·h²/6, коробкой
    # и кругом π·d³/32, в том числе повёрнутыми).
    values, vectors = np.linalg.eigh(np.array([[ixx, -ixy], [-ixy, iyy]]))
    i_min = float(values[0])
    axis = vectors[:, 0]
    normal = np.array([-axis[1], axis[0]])
    pts = rings[0][:-1] - np.array([cx, cy])
    reach = float(np.abs(pts @ normal).max())
    if i_min <= 0 or reach <= 0:
        return float(area), 0.0
    return float(area), float(i_min / reach)


def layer_strength(part: Solid, *, force: float, strength_z: float, safety: float = 2.0,
                   min_span: float = 3.0, up: str = "+Z", id: str = "",
                   name: str = "") -> CheckResult:
    """Изгиб между слоями в самом слабом сечении при силе `force` (Н) в руках.

    Слой делит деталь на два куска. Один держат, на край другого давят вбок:
    плечо — у МЕНЬШЕГО куска (расстояние от слоя до самой дальней его точки):
    за короткий кусок длинным рычагом не возьмёшься — он и отломится. Так
    ножка, отходящая от корпуса, судится по своей длине, а сужение у самого
    стола (фаска первого слоя) — по своим миллиметрам, а не по высоте всей
    детали: первая версия считала деталь консолью от стола и показывала на
    первый слой ядра Gear Cube вместо его ножки.

    `strength_z` — предел между слоями, МПа (`MAT_TENSILE_Z` подставляется
    сам), `safety` — запас. Слои с плечом короче `min_span` не судятся.
    """
    from datum.checks.motion import _vertices
    from datum.checks.printing import wall_levels
    from datum.geom.measure import bbox, section_xy
    from datum.geom.xform import upright

    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    if not force > 0:
        raise ValueError(f"force {force!r}: нужна сила больше нуля, Н")
    if not strength_z > 0:
        raise ValueError(f"strength_z {strength_z!r}: нужен предел прочности больше нуля, МПа")
    if not safety >= 1:
        raise ValueError(f"safety {safety!r}: запас не меньше 1")
    pose = str(up).strip().upper()
    posed = upright(part, pose)
    lo, hi = bbox(posed)
    z0, z1 = float(lo[2]), float(hi[2])
    verts = _vertices(posed)
    common = dict(id=id or f"{name}.прочность_слоёв", title="Изгиб между слоями",
                  group="printing", inputs=(name,), unit="МПа", op="<=",
                  threshold=strength_z / safety)
    worst = None                                   # (σ, z, площадь, W, кусков, плечо)
    for z in wall_levels(posed, 2, z0, z1, 24):
        z = float(z)
        section = section_xy(posed, z)
        pieces = [p for p in list(getattr(section, "geoms", [section]))
                  if not p.is_empty and p.area > 1e-6]
        if not pieces:
            continue
        area = sum(p.area for p in pieces)
        cx = sum(p.centroid.x * p.area for p in pieces) / area
        cy = sum(p.centroid.y * p.area for p in pieces) / area
        away = np.linalg.norm(verts - np.array([cx, cy, z]), axis=1)
        above, below = away[verts[:, 2] > z], away[verts[:, 2] < z]
        if not len(above) or not len(below):
            continue
        arm = float(min(above.max(), below.max()))
        if arm < min_span:
            continue
        # Куски сечения считаются ПОРОЗНЬ, моменты сопротивления складываются:
        # выше и ниже слоя они соединены, но рамой, а не сплошным сечением —
        # считать их одной балкой было бы оптимистично.
        modulus = sum(section_bending(p)[1] for p in pieces)
        if modulus <= 0:
            continue
        sigma = force * arm / modulus
        if worst is None or sigma > worst[0]:
            worst = (sigma, z, area, modulus, len(pieces), arm)
    ms = (time.perf_counter() - t0) * 1000
    if worst is None:
        return CheckResult(status="skip", duration_ms=ms, **common,
                           message=f"{name}: нет слоя, по обе стороны от которого деталь "
                                   f"длиннее {min_span:g} мм, — отламывать нечего")
    sigma, z, area, modulus, count, arm = worst
    ok = sigma * safety <= strength_z
    where = f"на высоте {z - z0:.2f} мм над столом (поза печати, вверх {pose})"
    apart = f", кусков сечения {count} — считаны порознь" if count > 1 else ""
    return CheckResult(
        status="pass" if ok else "fail", value=sigma, duration_ms=ms, **common,
        message=(f"{name}: сила {force:g} Н вбок на плече {arm:.1f} мм — слабейший слой "
                 f"{where}: сечение {area:.1f} мм², момент сопротивления {modulus:.1f} мм³"
                 f"{apart}; изгиб между слоями {sigma:.1f} МПа, с запасом ×{safety:g} — "
                 f"{sigma * safety:.1f} {'≤' if ok else '>'} {strength_z:g} МПа"
                 + ("" if ok else f" — СЛОМАЕТСЯ ПО СЛОЮ: посмотрите section на этой высоте; "
                                  f"толще сечение в этом месте (гнездо, отверстие, шейка), "
                                  f"скругление у перехода или другая поза печати, чтобы "
                                  f"изгиб шёл вдоль слоёв")))
