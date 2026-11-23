"""Крупный план режет сетку коробкой, а не отбирает грани по вершинам.

Аудит 2026-11-21: у детали CAD плоская грань — два треугольника с вершинами в
дальних углах. Середина грани давала «в коробке нет ни одной грани», угол —
стенку с диагональной дырой: агент видел дыру, которой в детали нет.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from datum.geom import box
from datum.geom.convert import vertices_faces
from datum.viz.render import BACKGROUND, _clip_to_box, render_views


def _area(v, f) -> float:
    t = v[f]
    return float(np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum() / 2)


@pytest.fixture(scope="module")
def slab():
    return vertices_faces(box(0, 100, 0, 100, 0, 20).manifold)


def test_clip_cuts_faces_at_the_box_exactly(slab):
    v, f = slab
    cv, cf, parent = _clip_to_box(v, f, (40, 40, 15), (60, 60, 25))
    assert _area(cv, cf) == pytest.approx(400.0)            # середина верхней грани
    cv, cf, parent = _clip_to_box(v, f, (80, 80, 10), (101, 101, 21))
    assert _area(cv, cf) == pytest.approx(400.0 + 2 * 200.0)   # верх и две стенки
    assert parent.max() < len(f) and len(parent) == len(cf)
    cv, cf, parent = _clip_to_box(v, f, (-5, -5, -5), (105, 105, 25))
    assert np.array_equal(cf, f) and len(cv) == len(v), "всё внутри — сетка не тронута"
    assert len(_clip_to_box(v, f, (200, 200, 200), (210, 210, 210))[1]) == 0


def test_zoom_on_the_middle_of_a_flat_face_is_a_picture_not_a_refusal(slab):
    v, f = slab
    png = render_views(v, f, title="середина", frame=((40, 40, 15), (60, 60, 25)),
                       views=("сверху",))
    img = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    lit = (np.abs(img - np.asarray(BACKGROUND)).sum(axis=2) > 60)
    assert lit.mean() > 0.3, "грань занимает кадр, а не отсутствует"


def test_zoom_on_a_corner_has_no_diagonal_hole(slab):
    v, f = slab
    png = render_views(v, f, title="угол", frame=((80, 80, 10), (101, 101, 21)),
                       views=("сверху",))
    img = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    lit = (np.abs(img - np.asarray(BACKGROUND)).sum(axis=2) > 60)
    # Строки и столбцы самой грани: длиннее подписей, короче рамки плитки.
    per_row, per_col = lit.sum(axis=1), lit.sum(axis=0)
    rows = np.flatnonzero((per_row > 150) & (per_row < 400))
    cols = np.flatnonzero((per_col > 150) & (per_col < 400))
    patch = lit[rows.min() + 4:rows.max() - 4, cols.min() + 4:cols.max() - 4]
    assert patch.size > 10_000 and patch.mean() > 0.99, "верхняя грань — сплошной квадрат"


def test_far_away_frame_is_still_refused(slab):
    v, f = slab
    with pytest.raises(ValueError, match="нет ни одной грани"):
        render_views(v, f, title="мимо", frame=((500, 500, 500), (510, 510, 510)))
