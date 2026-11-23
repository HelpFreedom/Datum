"""Расстановка новых деталей на столе: как в файле, иначе рядами, и мимо занятого."""

from __future__ import annotations

import itertools

import pytest

from datum.doc.bedlayout import GAP, arrange

BED = (256.0, 256.0)


def _rects(result, footprints):
    out = []
    for (cx, cy), r in zip(result.centres, footprints):
        w, d = r[2] - r[0], r[3] - r[1]
        out.append((cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2))
    return out


def _apart(rects, gap=0.0):
    for a, b in itertools.combinations(rects, 2):
        if a[0] < b[2] + gap - 1e-9 and b[0] < a[2] + gap - 1e-9 \
                and a[1] < b[3] + gap - 1e-9 and b[1] < a[3] + gap - 1e-9:
            return False
    return True


def test_one_object_stands_in_the_middle():
    r = arrange([(500, 500, 520, 530)], BED)
    assert r.centres == [pytest.approx((128, 128))] and r.fits


def test_the_authors_layout_is_kept_and_centred():
    """Расставленное в слайсере стоит так же относительно друг друга."""
    feet = [(0, 0, 10, 10), (30, 0, 36, 6), (60, 0, 64, 4)]
    r = arrange(feet, BED)
    assert r.kept_file_layout and r.fits
    (ax, ay), (bx, by), (cx, cy) = r.centres
    assert (bx - ax, by - ay) == pytest.approx((28, -2))
    assert (cx - ax, cy - ay) == pytest.approx((57, -3))
    # Группа — в центре стола.
    xs = [x for rect in _rects(r, feet) for x in (rect[0], rect[2])]
    assert (min(xs) + max(xs)) / 2 == pytest.approx(128)


def test_objects_piled_in_one_spot_are_laid_out_in_rows():
    """Экспорт сборки: всё в начале координат. Рядами, с зазором, компактно."""
    feet = [(0, 0, 40, 30)] * 3 + [(0, 0, 10, 10)] * 4
    r = arrange(feet, BED)
    rects = _rects(r, feet)
    assert not r.kept_file_layout and r.fits
    assert _apart(rects, GAP)
    assert all(0 <= x0 and 0 <= y0 and x1 <= 256 and y1 <= 256 for x0, y0, x1, y1 in rects)
    width = max(x[2] for x in rects) - min(x[0] for x in rects)
    depth = max(x[3] for x in rects) - min(x[1] for x in rects)
    assert width < 200 and depth < 200, "группа компактная, а не полоса через стол"


def test_occupied_middle_is_avoided_but_the_group_stays_close():
    taken = [(100, 100, 156, 156)]
    r = arrange([(0, 0, 30, 30)], BED, occupied=taken)
    (rect,) = _rects(r, [(0, 0, 30, 30)])
    assert r.fits and _apart([rect, taken[0]], GAP)
    distance = ((r.centres[0][0] - 128) ** 2 + (r.centres[0][1] - 128) ** 2) ** 0.5
    assert distance < 60, "ближайшее свободное место, а не край стола"
    assert "занят" in r.note


def test_what_does_not_fit_says_so():
    r = arrange([(0, 0, 300, 20)], BED)
    assert not r.fits and "НЕ ПОМЕСТИЛОСЬ" in r.note
    assert r.centres == [pytest.approx((128, 128))]
