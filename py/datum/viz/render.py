"""Вид детали в картинке — для агента, у которого нет окна.

Окно показывает деталь в трёх измерениях, а агент в терминале видел только
числа и сечения. Лист из четырёх видов (изометрия, спереди, справа, сверху)
даёт ему тот же взгляд, что у человека: деталь, у которой отъехала бобышка или
перевёрнут паз, видна сразу, а по «объём 12058.9 мм³» — нет.

РИСУЕТ СВОЙ РАСТЕРИЗАТОР, а не OpenGL. Внеэкранный OpenGL на этой машине —
это выбор между EGL и osmesa, поиск контекста и лишний пакет; здесь нужен
плоский вид детали CAD, а для него хватает z-буфера на numpy. Треугольники
обрабатываются пачками по размеру: мелкие — перебором смещений внутри
общей рамки сразу для всех, крупные (грани коробок) — по одному сеткой
пикселей.

ПЛОСКАЯ ЗАЛИВКА И РЁБРА. Как и в окне, нормалей по вершинам нет: деталь CAD
должна выглядеть гранёной, иначе фаска неотличима от скругления. Поверх
заливки рисуются изломы (угол между гранями больше 30°) и контур; рёбра
проходят тест глубины, поэтому задние не просвечивают.

Проекция ортогональная и масштаб ОДИН на весь лист: размеры на видах
сравнимы между собой.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from datum.viz.section import BACKGROUND, DIM, TEXT, _font

__all__ = ["VIEWS", "render_views"]

#: Направление НА КАМЕРУ (от детали к глазу) и «верх» экрана. Z вверх, как в
#: Datum. «Спереди» — взгляд со стороны −Y, как принято в черчении.
VIEWS = {
    "изометрия": ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
    "спереди": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "справа": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "сверху": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    # Снизу видно то, что смотрит вниз, — нависания и мосты на карте печатности.
    "изометрия снизу": ((1.0, -1.0, -1.0), (0.0, 0.0, 1.0)),
    "снизу в разрезе": ((1.0, -1.0, -1.0), (0.0, 0.0, 1.0)),
}

EDGE_ANGLE_DEG = 30.0
EDGE = (24, 26, 31)
MARK = (255, 181, 71)                                   # как замер в окне
SMALL_CLASSES = (2, 4, 8, 16, 32)


@dataclass(frozen=True)
class _Camera:
    right: np.ndarray
    up: np.ndarray
    toward: np.ndarray            # на камеру: больше — ближе


def _camera(toward, up) -> _Camera:
    e = np.asarray(toward, float)
    e /= np.linalg.norm(e)
    r = np.cross(np.asarray(up, float), e)
    if np.linalg.norm(r) < 1e-6:
        # Верх вдоль взгляда (камера окна строго сверху): верх экрана — +Y.
        r = np.cross(np.array([0.0, 1.0, 0.0]) if abs(e[1]) < 0.9 else np.array([1.0, 0.0, 0.0]), e)
    r /= np.linalg.norm(r)
    return _Camera(right=r, up=np.cross(e, r), toward=e)


def _hex(color: str) -> np.ndarray:
    c = color.lstrip("#")
    if len(c) != 6:
        return np.array([154.0, 164.0, 178.0])
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], float)


def _feature_edges(verts: np.ndarray, faces: np.ndarray):
    """Рёбра и нормали пар граней при них — один раз на деталь."""
    import trimesh

    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    return mesh.face_adjacency, mesh.face_adjacency_edges, mesh.face_adjacency_angles, \
        mesh.face_normals


def _cover_all(sx, sy, depth, faces, size):
    """Ближайший треугольник в каждом пикселе: пиксели, глубина, номер, веса.

    Отдаёт только занятые пиксели (номер в плоском массиве w·h), глубину
    (больше — ближе), номер треугольника и барицентрические веса w0, w1 его
    первых двух вершин (w2 = 1 − w0 − w1).
    """
    w, h = size
    tx, ty, tz = sx[faces], sy[faces], depth[faces]          # (n, 3)
    x0 = np.floor(tx.min(1) - 0.5).astype(np.int64)
    y0 = np.floor(ty.min(1) - 0.5).astype(np.int64)
    x1 = np.ceil(tx.max(1) - 0.5).astype(np.int64)
    y1 = np.ceil(ty.max(1) - 0.5).astype(np.int64)
    area = ((tx[:, 1] - tx[:, 0]) * (ty[:, 2] - ty[:, 0])
            - (tx[:, 2] - tx[:, 0]) * (ty[:, 1] - ty[:, 0]))
    live = (np.abs(area) > 1e-12) & (x1 >= 0) & (y1 >= 0) & (x0 < w) & (y0 < h)
    span = np.maximum(x1 - x0, y1 - y0) + 1

    pix_all, dep_all, tri_all, w0_all, w1_all = [], [], [], [], []

    def cover(idx, px, py):
        """Покрытие пикселей (px, py) треугольниками idx — векторно."""
        ax, ay = tx[idx, 0], ty[idx, 0]
        bx, by = tx[idx, 1], ty[idx, 1]
        cx, cy = tx[idx, 2], ty[idx, 2]
        a = area[idx]
        qx, qy = px + 0.5, py + 0.5
        w0 = ((bx - qx) * (cy - qy) - (cx - qx) * (by - qy)) / a
        w1 = ((cx - qx) * (ay - qy) - (ax - qx) * (cy - qy)) / a
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9) \
            & (px >= 0) & (py >= 0) & (px < w) & (py < h)
        if not inside.any():
            return
        d = w0 * tz[idx, 0] + w1 * tz[idx, 1] + w2 * tz[idx, 2]
        pix_all.append((py * w + px)[inside])
        dep_all.append(d[inside])
        tri_all.append(np.broadcast_to(idx, px.shape)[inside])
        w0_all.append(w0[inside])
        w1_all.append(w1[inside])

    lower = 0
    for bound in SMALL_CLASSES:
        idx = np.flatnonzero(live & (span > lower) & (span <= bound))
        lower = bound
        if idx.size == 0:
            continue
        for dx in range(bound):
            for dy in range(bound):
                cover(idx, x0[idx] + dx, y0[idx] + dy)
    for i in np.flatnonzero(live & (span > SMALL_CLASSES[-1])):
        gx, gy = np.meshgrid(np.arange(max(x0[i], 0), min(x1[i], w - 1) + 1),
                             np.arange(max(y0[i], 0), min(y1[i], h - 1) + 1))
        cover(np.full(gx.size, i), gx.ravel(), gy.ravel())

    if not pix_all:
        empty = np.zeros(0)
        return empty.astype(np.int64), empty, empty.astype(np.int64), empty, empty
    pix = np.concatenate(pix_all)
    dep = np.concatenate(dep_all)
    tri = np.concatenate(tri_all)
    w0 = np.concatenate(w0_all)
    w1 = np.concatenate(w1_all)
    order = np.lexsort((-dep, pix))                    # по пикселю, ближнее первым
    first = np.ones(order.size, bool)
    first[1:] = pix[order][1:] != pix[order][:-1]
    keep = order[first]
    return pix[keep], dep[keep], tri[keep], w0[keep], w1[keep]


def _rasterize(sx, sy, depth, faces, shade, size) -> tuple[np.ndarray, np.ndarray]:
    """Z-буфер (больше — ближе) и цвет каждого пикселя — плоская заливка."""
    w, h = size
    zbuf = np.full(w * h, -np.inf)
    color = np.zeros((w * h, 3))
    pix, dep, tri, _, _ = _cover_all(sx, sy, depth, faces, size)
    zbuf[pix] = dep
    color[pix] = shade[tri]
    return zbuf, color


def _draw_edges(image: np.ndarray, zbuf: np.ndarray, sx, sy, depth, edges, size, eps):
    """Рёбра поверх заливки — только видимые (тест глубины по z-буферу)."""
    if len(edges) == 0:
        return
    w, h = size
    a, b = edges[:, 0], edges[:, 1]
    length = np.hypot(sx[b] - sx[a], sy[b] - sy[a])
    counts = np.maximum(2, np.ceil(length * 1.5).astype(np.int64) + 1)
    owner = np.repeat(np.arange(len(edges)), counts)
    start = np.repeat(np.cumsum(counts) - counts, counts)
    t = (np.arange(owner.size) - start) / np.repeat(counts - 1, counts)
    px = sx[a][owner] + t * (sx[b] - sx[a])[owner]
    py = sy[a][owner] + t * (sy[b] - sy[a])[owner]
    pd = depth[a][owner] + t * (depth[b] - depth[a])[owner]
    ix, iy = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
    ok = (ix >= 0) & (iy >= 0) & (ix < w) & (iy < h)
    ix, iy, pd = ix[ok], iy[ok], pd[ok]
    flat = iy * w + ix
    visible = pd >= zbuf[flat] - eps
    image[flat[visible]] = EDGE


def _tile(verts, faces, adjacency, adj_edges, adj_angles, normals, camera: _Camera,
          centre, scale, size, base, creases=None) -> np.ndarray:
    w, h = size
    rel = verts - centre
    sx = rel @ camera.right * scale + w / 2
    sy = h / 2 - rel @ camera.up * scale
    depth = rel @ camera.toward * scale
    facing = normals @ camera.toward
    # Свет — от камеры и чуть сверху-слева: грани, смотрящие на зрителя,
    # светлее, и соседние грани коробки различимы по тону.
    # У камеры СНИЗУ верх экрана смотрит в мировой +Z, и свет «сверху экрана»
    # не доставал до граней, глядящих вниз, — то есть ровно до нависаний.
    lift = camera.up if camera.toward[2] >= -0.2 else -camera.up
    light = camera.toward * 0.8 + lift * 0.45 - camera.right * 0.25
    light /= np.linalg.norm(light)
    lum = 0.28 + 0.72 * np.clip(normals @ light, 0.0, 1.0)
    # Цвет — один на деталь или свой у каждой грани (вид стола: детали разного цвета).
    shade = np.clip((base if base.ndim == 2 else base[None, :]) * lum[:, None], 0, 255)
    zbuf, color = _rasterize(sx, sy, depth, faces, shade, size)
    image = np.where(np.isfinite(zbuf)[:, None], color, np.array(BACKGROUND, float))
    crease = adj_angles > math.radians(EDGE_ANGLE_DEG)
    if creases is not None:
        # Изломы — только там, где обе грани их хотят: у образца импорта
        # (скульптура) каждое ребро — «излом», и лист выходил в ряби.
        crease &= creases[adjacency[:, 0]] & creases[adjacency[:, 1]]
    outline = (facing[adjacency[:, 0]] > 0) != (facing[adjacency[:, 1]] > 0)
    edges = adj_edges[crease | outline]
    eps = 1.5 + 1e-6 * scale
    _draw_edges(image, zbuf, sx, sy, depth, edges, size, eps)
    # Край силуэта — по самому z-буферу. Ребро на краю лежит ровно на границе
    # пикселей и на прямых видах уходило на фон: коробка «спереди» выходила
    # плоским прямоугольником без обводки.
    from scipy import ndimage

    mask = np.isfinite(zbuf).reshape(h, w)
    border = mask & ~ndimage.binary_erosion(mask)
    image[border.ravel()] = EDGE
    return image.reshape(h, w, 3).astype(np.uint8)


def _wrap(text: str, font, width: float) -> list[str]:
    """Перенос по словам под ширину в пикселях."""
    lines, current = [], ""
    for word in text.split(" "):
        trial = word if not current else f"{current} {word}"
        if current and font.getlength(trial) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + [current] if current else lines or [""]


# ============================================================ вид с освещением
#: Излом, по которому нормаль НЕ сглаживается: рёбра CAD остаются резкими, а
#: огранка сетки поля (соседние грани отличаются на единицы градусов) — нет.
SMOOTH_CREASE_DEG = 40.0


def _corner_normals(verts, faces, normals, area, crease_deg: float = SMOOTH_CREASE_DEG):
    """Нормаль в каждом углу грани (n×3×3): среднее по соседям у вершины без излома.

    Плоская заливка на сетке поля показывает огранку треугольников, а не форму
    — так «зерно» и путали: черта на картинке, которой нет на детали. Здесь
    у каждого угла — среднее нормалей граней при вершине, отличающихся от
    своей меньше чем на `crease_deg`, с весом площади.
    """
    corner_face = np.repeat(np.arange(len(faces)), 3)
    corner_vert = faces.ravel()
    order = np.argsort(corner_vert, kind="stable")
    starts = np.searchsorted(corner_vert[order], np.arange(len(verts)))
    counts = np.bincount(corner_vert, minlength=len(verts))
    limit = math.cos(math.radians(crease_deg))
    acc = np.zeros((len(corner_vert), 3))
    # Пары (угол, грань при той же вершине) — по deg² на вершину, поэтому
    # кусками: на сетке в 1.7 млн вершин их ~60 млн, и разом это гигабайты.
    chunk = 400_000
    for lo in range(0, len(corner_vert), chunk):
        corners = np.arange(lo, min(lo + chunk, len(corner_vert)))
        deg = counts[corner_vert[corners]]
        owner = np.repeat(corners, deg)
        offset = np.arange(owner.size) - np.repeat(np.cumsum(deg) - deg, deg)
        other = corner_face[order[starts[corner_vert[owner]] + offset]]
        cos = np.einsum("ij,ij->i", normals[corner_face[owner]], normals[other])
        weight = area[other] * (cos >= limit)
        np.add.at(acc, owner, normals[other] * weight[:, None])
    norm = np.linalg.norm(acc, axis=1)
    acc = np.where(norm[:, None] > 1e-12, acc / np.maximum(norm, 1e-12)[:, None],
                   normals[corner_face])
    return acc.reshape(len(faces), 3, 3)


def _light_dirs(cam: _Camera):
    key = -0.35 * cam.right + 0.55 * cam.up + 0.75 * cam.toward
    fill = 0.6 * cam.right - 0.15 * cam.up + 0.55 * cam.toward
    return key / np.linalg.norm(key), fill / np.linalg.norm(fill)


def _shadow(verts, faces, centre, points, key, normals=None) -> np.ndarray:
    """Доля света ключевой лампы в точках (0…1): карта глубины от лампы, 3×3.

    Против «прыщей» (поверхность затеняет сама себя полосами) точка сдвигается
    вдоль нормали на две клетки карты, а порог растёт с наклоном к лампе: на
    скользящем свете глубина меняется в пределах одной клетки сильнее всего.
    """
    cam = _camera(tuple(key), (0.0, 0.0, 1.0) if abs(key[2]) < 0.95 else (0.0, 1.0, 0.0))
    size = 1024
    rel = verts - centre
    extent = max(float(np.abs(rel @ cam.right).max()), float(np.abs(rel @ cam.up).max()), 1e-9)
    scale = 0.48 * size / extent
    sx = rel @ cam.right * scale + size / 2
    sy = size / 2 - rel @ cam.up * scale
    dz = rel @ cam.toward * scale
    pix, dep, _, _, _ = _cover_all(sx, sy, dz, faces, (size, size))
    zbuf = np.full(size * size, -np.inf)
    zbuf[pix] = dep
    zbuf = zbuf.reshape(size, size)
    pr = points - centre
    bias = np.full(len(points), 1.5)
    if normals is not None:
        pr = pr + normals * (2.0 / scale)
        cos = np.clip(np.abs(normals @ key), 0.05, 1.0)
        bias = bias + np.minimum(2.0 * np.sqrt(1 - cos ** 2) / cos, 8.0)
    px = pr @ cam.right * scale + size / 2
    py = size / 2 - pr @ cam.up * scale
    pd = pr @ cam.toward * scale
    lit = np.zeros(len(points))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            ix = np.clip(np.floor(px).astype(int) + dx, 0, size - 1)
            iy = np.clip(np.floor(py).astype(int) + dy, 0, size - 1)
            lit += pd >= zbuf[iy, ix] - bias
    return lit / 9.0


def _tile_render(verts, faces, corner_n, area_colors, camera: _Camera, centre, scale,
                 size) -> np.ndarray:
    """Плитка с освещением: гладкие нормали, ключевой свет с тенью, блик, контур."""
    w, h = size
    rel = verts - centre
    sx = rel @ camera.right * scale + w / 2
    sy = h / 2 - rel @ camera.up * scale
    depth = rel @ camera.toward * scale
    pix, dep, tri, w0, w1 = _cover_all(sx, sy, depth, faces, size)
    image = np.tile(np.array(BACKGROUND, float), (w * h, 1))
    if pix.size:
        w2 = 1.0 - w0 - w1
        n = (corner_n[tri, 0] * w0[:, None] + corner_n[tri, 1] * w1[:, None]
             + corner_n[tri, 2] * w2[:, None])
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
        # Нормаль, отвернувшаяся от камеры (тонкая кромка), — к камере.
        flip = n @ camera.toward < 0
        n[flip] *= -1
        key, fill = _light_dirs(camera)
        px, py = pix % w, pix // w
        points = (centre + ((px + 0.5 - w / 2) / scale)[:, None] * camera.right
                  + ((h / 2 - (py + 0.5)) / scale)[:, None] * camera.up
                  + (dep / scale)[:, None] * camera.toward)
        shadow = _shadow(verts, faces, centre, points, key, n)
        diff = np.clip(n @ key, 0.0, 1.0) * shadow
        soft = np.clip(n @ fill, 0.0, 1.0)
        half = key + camera.toward
        half /= np.linalg.norm(half)
        spec = np.clip(n @ half, 0.0, 1.0) ** 40 * shadow
        rim = (1.0 - np.clip(n @ camera.toward, 0.0, 1.0)) ** 3
        base = area_colors[tri]
        lum = 0.14 + 0.70 * diff + 0.22 * soft
        image[pix] = np.clip(base * lum[:, None] + 255 * (0.30 * spec + 0.10 * rim)[:, None],
                             0, 255)
    from scipy import ndimage

    mask = np.zeros(w * h, bool)
    mask[pix] = True
    mask = mask.reshape(h, w)
    border = mask & ~ndimage.binary_erosion(mask)
    image[border.ravel()] = EDGE
    return image.reshape(h, w, 3).astype(np.uint8)


#: Цвет стрелок вставки: зелёный, отличный и от деталей, и от отметок замера.
ARROW = (120, 214, 140)


def _arrow(draw, cam, centre, scale, origin, tile, start, end, label, font) -> None:
    """Стрелка в плитке вида: линия с наконечником или кружок вдоль взгляда."""
    x, y = origin

    def px(point):
        rel = np.asarray(point, float) - centre
        return (x + tile / 2 + float(rel @ cam.right) * scale,
                y + tile / 2 - float(rel @ cam.up) * scale)

    (ax, ay), (bx, by) = px(start), px(end)
    length = math.hypot(bx - ax, by - ay)
    along = np.asarray(end, float) - np.asarray(start, float)
    full = float(np.linalg.norm(along)) * scale
    if length < 0.25 * full or length < 8:
        # Почти вдоль взгляда: к зрителю — точка в круге, от зрителя — крест.
        toward = float(along @ cam.toward) > 0
        r = 9
        draw.ellipse([bx - r, by - r, bx + r, by + r], outline=ARROW, width=2)
        if toward:
            draw.ellipse([bx - 3, by - 3, bx + 3, by + 3], fill=ARROW)
        else:
            draw.line([bx - 6, by - 6, bx + 6, by + 6], fill=ARROW, width=2)
            draw.line([bx - 6, by + 6, bx + 6, by - 6], fill=ARROW, width=2)
        draw.text((bx + r + 4, by - 8), label, font=font, fill=ARROW)
        return
    draw.line([ax, ay, bx, by], fill=ARROW, width=3)
    ux, uy = (bx - ax) / length, (by - ay) / length
    head = min(16.0, 0.35 * length)
    for side in (1, -1):
        hx = bx - head * ux + side * head * 0.45 * -uy
        hy = by - head * uy + side * head * 0.45 * ux
        draw.line([bx, by, hx, hy], fill=ARROW, width=3)
    draw.text((ax + 6, ay - 16), label, font=font, fill=ARROW)


#: Высота строки легенды в шапке листа, пикселей.
LEGEND_ROW = 18


def _legend_rows(legend, font, *, first: float, rest: float) -> list[list[tuple[str, str]]]:
    """Разложить легенду по строкам: первая — шириной `first` (правее
    заголовка), следующие — `rest`. Порядок имён сохраняется; имя, которое
    не влезает и одно, стоит в своей строке."""
    rows: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    used = 0.0
    for item in legend:
        w = font.getlength(item[0]) + 30              # образец 16 + отступ 14
        room = first if not rows else rest
        if current and used + w > room:
            rows.append(current)
            current, used = [], 0.0
        current.append(item)
        used += w
    if current:
        rows.append(current)
    return rows


def _clip_to_box(verts, faces, lo, hi):
    """Обрезать сетку коробкой [lo, hi]: грани снаружи уходят, пересекающие
    границу РЕЖУТСЯ по ней. Возвращает (вершины, грани, родитель каждой грани).

    Прежде крупный план оставлял грань, только если её ВЕРШИНА в коробке. У
    детали CAD плоская грань — два треугольника с вершинами в дальних углах:
    середина грани давала «в коробке нет ни одной грани», а угол — стенку с
    диагональной дырой, которой в детали нет (аудит 2026-11-21). Вершины среза
    сливаются по координатам: иначе шов между двумя треугольниками одной
    плоской грани рисовался бы ребром.
    """
    verts = np.asarray(verts, float)
    faces = np.asarray(faces, np.int64)
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    tri = verts[faces]                                   # (n, 3, 3)
    t_lo, t_hi = tri.min(1), tri.max(1)
    outside = (t_hi < lo).any(1) | (t_lo > hi).any(1)
    inside = (t_lo >= lo).all(1) & (t_hi <= hi).all(1)
    keep_idx = np.flatnonzero(inside)
    cut_idx = np.flatnonzero(~inside & ~outside)
    new_verts = [verts]
    extra: list[np.ndarray] = []
    index: dict[tuple, int] = {}
    base = len(verts)
    out_faces = [faces[keep_idx]]
    parents = [keep_idx]
    cut_faces: list[tuple[int, int, int]] = []
    cut_parent: list[int] = []

    def vid(p) -> int:
        key = (round(float(p[0]), 6), round(float(p[1]), 6), round(float(p[2]), 6))
        if key not in index:
            index[key] = base + len(extra)
            extra.append(np.asarray(p, float))
        return index[key]

    planes = [(ax, sign) for ax in range(3) for sign in (1, -1)]
    for f in cut_idx:
        poly = [verts[i] for i in faces[f]]
        ids: list[int | None] = [int(i) for i in faces[f]]
        for ax, sign in planes:
            bound = lo[ax] if sign == 1 else hi[ax]
            if not poly:
                break
            nxt, nid = [], []
            for k in range(len(poly)):
                a, b = poly[k], poly[(k + 1) % len(poly)]
                ia, ib = ids[k], ids[(k + 1) % len(poly)]
                da = (a[ax] - bound) * sign
                db = (b[ax] - bound) * sign
                if da >= 0:
                    nxt.append(a)
                    nid.append(ia)
                if (da >= 0) != (db >= 0):
                    t = da / (da - db)
                    nxt.append(a + (b - a) * t)
                    nid.append(None)
            poly, ids = nxt, nid
        if len(poly) < 3:
            continue
        final = [i if i is not None else vid(p) for p, i in zip(poly, ids)]
        for k in range(1, len(final) - 1):
            tri_ids = (final[0], final[k], final[k + 1])
            if len(set(tri_ids)) == 3:
                cut_faces.append(tri_ids)
                cut_parent.append(int(f))
    if extra:
        new_verts.append(np.asarray(extra, float))
    if cut_faces:
        out_faces.append(np.asarray(cut_faces, np.int64))
        parents.append(np.asarray(cut_parent, np.int64))
    return (np.concatenate(new_verts), np.concatenate(out_faces).reshape(-1, 3),
            np.concatenate(parents))


def render_views(verts: np.ndarray, faces: np.ndarray, *, title: str, note: str = "",
                 color: str = "#9aa4b2", tile: int = 440,
                 views: tuple[str, ...] = ("изометрия", "спереди", "справа", "сверху"),
                 face_colors: np.ndarray | None = None,
                 legend: tuple[tuple[str, str], ...] = (),
                 cameras: dict[str, tuple] | None = None,
                 creases: np.ndarray | None = None,
                 marks: tuple[tuple[tuple[float, float, float], str], ...] = (),
                 arrows: tuple[tuple[tuple[float, float, float],
                                     tuple[float, float, float], str], ...] = (),
                 style: str = "technical",
                 frame: tuple[tuple[float, float, float],
                              tuple[float, float, float]] | None = None,
                 cutaway: dict[str, np.ndarray] | None = None,
                 ) -> bytes:
    """Лист видов в PNG. Масштаб — один на все виды.

    face_colors — цвет каждой грани (n×3, 0…255) вместо одного `color`;
    legend — подписи цветов в шапке; cameras — свои виды сверх `VIEWS`
    (направление на камеру и верх экрана), например «из окна»; creases —
    у каких граней рисовать изломы (у остальных — только контур); marks —
    отметки поверх всех видов (точка и подпись), например выбранное в окне;
    arrows — стрелки (откуда, куда, подпись) поверх всех видов, например
    направление вставки. Стрелка, смотрящая почти в камеру, рисуется
    кружком: «⊙» — к зрителю, «⊗» — от него, иначе её не было бы видно.

    style — «technical» (плоская заливка и рёбра: видно грани и размеры) или
    «render» (гладкие нормали, свет с тенью, блик: видно форму и рельеф —
    зернистость скульптуры на технических видах не разглядеть). frame —
    крупный план: коробка (низ, верх), по которой кадрируется лист; грани,
    не задевающие её, не рисуются, чтобы ближние стенки не закрывали место.
    cutaway — вид → маска граней, которые в нём рисуются (вид в разрезе:
    например, снизу без дна, чтобы было видно нависание внутри коробки).
    """
    if style not in ("technical", "render"):
        raise ValueError(f"стиль {style!r}: technical или render")
    verts = np.asarray(verts, float)
    faces = np.asarray(faces, np.int64)
    known = {**VIEWS, **(cameras or {})}
    if faces.size == 0:
        raise ValueError(f"{title}: пустое тело — рисовать нечего")
    if frame is not None:
        box_lo = np.minimum(frame[0], frame[1]) - 1.0
        box_hi = np.maximum(frame[0], frame[1]) + 1.0
        verts, faces, keep = _clip_to_box(verts, faces, box_lo, box_hi)
        if not len(faces):
            raise ValueError(f"{title}: в коробке крупного плана нет ни одной грани")
        if face_colors is not None:
            face_colors = np.asarray(face_colors, float)[keep]
        if creases is not None:
            creases = np.asarray(creases, bool)[keep]
        # Маски разреза — по тем же граням, иначе вид в разрезе падал бы.
        if cutaway:
            cutaway = {k: np.asarray(m, bool)[keep] for k, m in cutaway.items()}
    # Рамка — по деталям и по стрелкам: стрелка вставки начинается за деталью,
    # и без этого уходила бы за край плитки.
    framed = verts[np.unique(faces)] if frame is None else np.asarray(
        [[x, y, z] for x in (frame[0][0], frame[1][0]) for y in (frame[0][1], frame[1][1])
         for z in (frame[0][2], frame[1][2])], float)
    if arrows:
        framed = np.concatenate(
            [framed, np.asarray([p for a, b, _ in arrows for p in (a, b)], float)])
    lo, hi = framed.min(0), framed.max(0)
    centre = (lo + hi) / 2
    # Масштаб ОДИН на лист — по самой большой проекции среди выбранных видов:
    # на всех видах миллиметр один и тот же, и каждый вид влезает в плитку.
    # По диагонали габарита было проще, но вытянутая деталь занимала треть.
    widest = 1e-9
    for name in views:
        cam = _camera(*known[name])
        rel = framed - centre
        for axis in (cam.right, cam.up):
            # Вдвое от дальней точки, а не размах: вид центрирован по центру
            # габарита, и проекция вокруг него не обязана быть симметричной.
            widest = max(widest, 2 * float(np.abs(rel @ axis).max()))
    scale = 0.88 * tile / widest
    base = _hex(color) if face_colors is None else np.asarray(face_colors, float)
    if base.ndim == 2 and len(base) != len(faces):
        raise ValueError(f"{title}: цветов граней {len(base)}, а граней {len(faces)}")
    mask = None if creases is None else np.asarray(creases, bool)
    if mask is not None and len(mask) != len(faces):
        raise ValueError(f"{title}: отметок изломов {len(mask)}, а граней {len(faces)}")
    adjacency, adj_edges, adj_angles, normals = _feature_edges(verts, faces)
    if style == "render":
        tri = verts[faces]
        tri_area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]),
                                  axis=1) / 2
        corner_n = _corner_normals(verts, faces, np.asarray(normals, float), tri_area)
        face_rgb = base if base.ndim == 2 else np.tile(base, (len(faces), 1))

    cols = min(2, len(views))
    rows = math.ceil(len(views) / cols)
    gap = 8
    width = cols * tile + (cols + 1) * gap
    big, small = _font(18), _font(13)
    # Легенда — справа в шапке, строками: при 18 деталях одной строкой она
    # уходила за левый край и ложилась на заголовок (scene_view Gear Cube).
    # Первая строка — правее заголовка, следующие — во всю ширину.
    legend_rows = _legend_rows(legend, small,
                               first=width - gap - (gap + 4 + big.getlength(title) + 24),
                               rest=width - 2 * gap - 4)
    head = 44 + LEGEND_ROW * max(0, len(legend_rows) - 1)
    used = verts[np.unique(faces)]
    ext = used.max(0) - used.min(0)                # габарит — деталей, не стрелок
    footer = (f"габарит {ext[0]:.2f} × {ext[1]:.2f} × {ext[2]:.2f} мм (X × Y × Z), "
              f"{len(faces)} треуг." + (f" · {note}" if note else ""))
    # Длинная подпись переносится по словам: карта печатности пишет итоги и
    # пороги, и одной строкой они уходили за край листа.
    foot_lines = _wrap(footer, _font(13), width - 2 * gap - 8)
    foot = 40 + 18 * (len(foot_lines) - 1)
    height = head + rows * tile + (rows + 1) * gap + foot
    sheet = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    draw.text((gap + 4, 12), title, font=big, fill=TEXT)
    # Каждая строка легенды — от правого края: образец цвета и имя.
    for row, items in enumerate(legend_rows):
        lx, ly = width - gap, LEGEND_ROW * row
        for name, hex_color in reversed(items):
            lx -= draw.textlength(name, font=small)
            draw.text((lx, 16 + ly), name, font=small, fill=TEXT)
            lx -= 16
            draw.rectangle([lx, 17 + ly, lx + 11, 28 + ly],
                           fill=tuple(int(c) for c in _hex(hex_color)), outline=(57, 64, 78))
            lx -= 14
    for k, name in enumerate(views):
        toward, up = known[name]
        keep = (cutaway or {}).get(name)
        if keep is not None:
            # Вид в разрезе: только свои грани — со своими рёбрами и нормалями.
            keep = np.asarray(keep, bool)
            sub = faces[keep]
            sub_base = base[keep] if base.ndim == 2 else base
            if style == "render":
                pixels = _tile_render(verts, sub, corner_n[keep], face_rgb[keep],
                                      _camera(toward, up), centre, scale, (tile, tile))
            else:
                s_adj, s_edges, s_angles, s_normals = _feature_edges(verts, sub)
                pixels = _tile(verts, sub, s_adj, s_edges, s_angles, s_normals,
                               _camera(toward, up), centre, scale, (tile, tile), sub_base,
                               None if mask is None else mask[keep])
        elif style == "render":
            pixels = _tile_render(verts, faces, corner_n, face_rgb, _camera(toward, up),
                                  centre, scale, (tile, tile))
        else:
            pixels = _tile(verts, faces, adjacency, adj_edges, adj_angles, normals,
                           _camera(toward, up), centre, scale, (tile, tile), base, mask)
        x = gap + (k % cols) * (tile + gap)
        y = head + gap + (k // cols) * (tile + gap)
        sheet.paste(Image.fromarray(pixels), (x, y))
        cam = _camera(toward, up)
        for point, label in marks:
            rel = np.asarray(point, float) - centre
            mx = x + tile / 2 + float(rel @ cam.right) * scale
            my = y + tile / 2 - float(rel @ cam.up) * scale
            if x <= mx < x + tile and y <= my < y + tile:
                draw.ellipse([mx - 5, my - 5, mx + 5, my + 5], fill=MARK, outline=(28, 18, 3))
                draw.text((mx + 7, my - 9), label, font=small, fill=MARK)
        for start, end, label in arrows:
            _arrow(draw, cam, centre, scale, (x, y), tile, start, end, label, small)
        draw.rectangle([x, y, x + tile - 1, y + tile - 1], outline=(57, 64, 78))
        draw.text((x + 8, y + 6), name, font=small, fill=DIM)
    for i, line in enumerate(foot_lines):
        draw.text((gap + 4, height - foot + 12 + 18 * i), line, font=small, fill=DIM)
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_turntable(verts: np.ndarray, faces: np.ndarray, *, title: str,
                     color: str = "#9aa4b2", face_colors: np.ndarray | None = None,
                     frames: int = 24, tile: int = 360,
                     elevation_deg: float = 25.0) -> tuple[bytes, bytes]:
    """Поворот вокруг вертикали со светом: GIF и лист из восьми ракурсов (PNG).

    GIF — для человека; агент картинку с движением не прочтёт, ему — лист.
    Масштаб один на все кадры (по описанной сфере), чтобы деталь не «дышала».
    """
    verts = np.asarray(verts, float)
    faces = np.asarray(faces, np.int64)
    if faces.size == 0:
        raise ValueError(f"{title}: пустое тело — рисовать нечего")
    if frames < 8:
        raise ValueError(f"кадров {frames}: нужно хотя бы 8")
    lo, hi = verts.min(0), verts.max(0)
    centre = (lo + hi) / 2
    radius = max(float(np.linalg.norm(verts - centre, axis=1).max()), 1e-9)
    scale = 0.9 * tile / (2 * radius)
    _, _, _, normals = _feature_edges(verts, faces)
    tri = verts[faces]
    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
    corner_n = _corner_normals(verts, faces, np.asarray(normals, float), area)
    base = _hex(color) if face_colors is None else np.asarray(face_colors, float)
    face_rgb = base if base.ndim == 2 else np.tile(base, (len(faces), 1))
    e = math.radians(elevation_deg)
    images = []
    for k in range(frames):
        a = 2 * math.pi * k / frames - math.pi / 2          # первый кадр — спереди (−Y)
        toward = (math.cos(a) * math.cos(e), math.sin(a) * math.cos(e), math.sin(e))
        cam = _camera(toward, (0.0, 0.0, 1.0))
        images.append(Image.fromarray(
            _tile_render(verts, faces, corner_n, face_rgb, cam, centre, scale, (tile, tile))))
    gif = io.BytesIO()
    images[0].save(gif, format="GIF", save_all=True, append_images=images[1:],
                   duration=120, loop=0, optimize=True)
    # Лист: восемь ракурсов через 45°, подписаны углом.
    picks = [images[round(i * frames / 8) % frames] for i in range(8)]
    gap, head = 8, 40
    sheet = Image.new("RGB", (4 * tile + 5 * gap, head + 2 * tile + 3 * gap), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    draw.text((gap + 4, 12), f"{title} — поворот", font=_font(18), fill=TEXT)
    for i, img in enumerate(picks):
        x = gap + (i % 4) * (tile + gap)
        y = head + gap + (i // 4) * (tile + gap)
        sheet.paste(img, (x, y))
        draw.text((x + 8, y + 6), f"{45 * i}°", font=_font(13), fill=DIM)
    out = io.BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return gif.getvalue(), out.getvalue()
