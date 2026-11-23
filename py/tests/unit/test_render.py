"""Вид детали в картинке: растеризатор проверяется на ответах, известных заранее."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from datum.geom import box, union
from datum.geom.convert import vertices_faces
from datum.viz.render import _camera, _rasterize, render_views
from datum.viz.section import BACKGROUND


def test_the_nearer_triangle_wins():
    """Два треугольника друг над другом: пиксель берёт ближний, и порядок в
    списке роли не играет."""
    sx = np.array([0, 10, 0, 0, 10, 0], float)
    sy = np.array([0, 0, 10, 0, 0, 10], float)
    depth = np.array([1, 1, 1, 5, 5, 5], float)          # второй ближе
    shade = np.array([[255, 0, 0], [0, 255, 0]], float)
    for faces in (np.array([[0, 1, 2], [3, 4, 5]]), np.array([[3, 4, 5], [0, 1, 2]])):
        order = shade if faces[0, 0] == 0 else shade[::-1]
        zbuf, color = _rasterize(sx, sy, depth, faces, order, (12, 12))
        assert zbuf[2 * 12 + 2] == pytest.approx(5.0)
        assert color[2 * 12 + 2].tolist() == [0, 255, 0]


def test_coverage_matches_the_projected_area():
    """Квадрат 8×8 пикселей в плоскости кадра — 64 пикселя, и не 63 или 72:
    правило центра пикселя не удваивает общих рёбер."""
    sx = np.array([2, 10, 10, 2], float)
    sy = np.array([2, 2, 10, 10], float)
    depth = np.zeros(4)
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    zbuf, _ = _rasterize(sx, sy, depth, faces, np.ones((2, 3)), (16, 16))
    assert np.isfinite(zbuf).sum() == 64


def test_large_triangles_go_the_per_triangle_way():
    """Треугольник больше самой крупной пачки рисуется своей сеткой — и так же
    точно: прямоугольный на 100 пикселей по катетам даёт ≈ 5000 пикселей."""
    sx = np.array([0, 100, 0], float)
    sy = np.array([0, 0, 100], float)
    zbuf, _ = _rasterize(sx, sy, np.zeros(3), np.array([[0, 1, 2]]), np.ones((1, 3)),
                         (128, 128))
    assert np.isfinite(zbuf).sum() == pytest.approx(5000, rel=0.02)


def test_cameras_look_the_way_their_names_say():
    front = _camera((0, -1, 0), (0, 0, 1))
    assert front.right.tolist() == pytest.approx([1, 0, 0])
    assert front.up.tolist() == pytest.approx([0, 0, 1])
    top = _camera((0, 0, 1), (0, 1, 0))
    assert top.right.tolist() == pytest.approx([1, 0, 0])
    assert top.up.tolist() == pytest.approx([0, 1, 0])


def _sheet(solid, **kw):
    verts, faces = vertices_faces(solid.manifold)
    return np.asarray(Image.open(io.BytesIO(render_views(verts, faces, title="t", **kw))))


def test_the_top_view_shows_a_hole_through_the_part():
    """Сквозное отверстие видно насквозь на виде сверху: в его центре — фон."""
    from datum.geom import cyl_z, diff

    plate = diff(box(-20, 20, -20, 20, 0, 4), [cyl_z(10, -1, 5)])
    img = _sheet(plate, tile=200)
    # Лист 2×2: плитка «сверху» — правая нижняя, её центр виден по геометрии листа.
    gap, head = 8, 44
    cx = gap + 200 + gap + 100
    cy = head + gap + 200 + gap + 100
    assert tuple(img[cy, cx]) == BACKGROUND
    assert tuple(img[cy, cx + 50]) != BACKGROUND            # сама плита


def test_the_one_scale_keeps_sizes_comparable():
    """Брусок 40 мм и брусок 20 мм на одном листе: ширина силуэта вдвое."""
    bars = union([box(0, 40, 0, 2, 0, 2), box(0, 20, 10, 12, 0, 2)])
    img = _sheet(bars, tile=240, views=("сверху",))
    gap, head = 8, 44
    inner = img[head + gap + 3: head + gap + 237, gap + 3: gap + 237].astype(int)
    solid = np.abs(inner - BACKGROUND).sum(2) > 30
    # Ширина каждого бруска — самая частая длина строки силуэта; крайние
    # строки у обводки короче и в счёт не идут.
    counts = solid.sum(1)
    values, freq = np.unique(counts[counts > 10], return_counts=True)
    long_bar, short_bar = values[freq >= 3].max(), values[freq >= 3].min()
    assert short_bar == pytest.approx(long_bar / 2, rel=0.08)


def test_an_empty_body_is_refused():
    with pytest.raises(ValueError, match="пустое"):
        render_views(np.zeros((0, 3)), np.zeros((0, 3), int), title="пусто")


def test_arrows_are_drawn_and_do_not_change_the_extents():
    import io

    from PIL import Image

    from datum.geom import box
    from datum.geom.convert import vertices_faces
    from datum.viz.render import ARROW, render_views

    v, f = vertices_faces(box(0, 10, 0, 10, 0, 4).manifold)
    plain = render_views(v, f, title="брусок")
    marked = render_views(v, f, title="брусок",
                          arrows=(((5, 5, 20), (5, 5, 2), "брусок -Z"),))
    img = np.asarray(Image.open(io.BytesIO(marked)).convert("RGB"))
    assert (np.abs(img.astype(int) - ARROW).sum(axis=2) < 30).sum() > 50, "стрелки нет"
    assert plain != marked


def test_a_long_legend_wraps_into_rows_instead_of_running_off_the_sheet():
    """`scene_view` Gear Cube: 18 деталей одной строкой уходили за левый край
    листа, и первые имена ложились на заголовок. Теперь — строками; короткая
    легенда — как прежде, лист той же высоты."""
    from datum.viz.render import LEGEND_ROW, _font, _legend_rows

    small = _font(13)
    legend = tuple((f"шестерня_{i}", "#c9772e") for i in range(18))
    rows = _legend_rows(legend, small, first=600, rest=880)
    assert [item for row in rows for item in row] == list(legend)     # порядок тот же
    for k, row in enumerate(rows):
        width = sum(small.getlength(n) + 30 for n, _ in row)
        assert width <= (600 if k == 0 else 880)
    assert len(_legend_rows(legend[:3], small, first=600, rest=880)) == 1

    verts, faces = vertices_faces(box(0, 10, 0, 20, 0, 5).manifold)
    short = Image.open(io.BytesIO(render_views(verts, faces, title="стол",
                                               legend=legend[:3])))
    long = Image.open(io.BytesIO(render_views(verts, faces, title="стол", legend=legend)))
    assert long.width == short.width
    assert long.height - short.height == LEGEND_ROW * (len(rows) - 1) > 0
