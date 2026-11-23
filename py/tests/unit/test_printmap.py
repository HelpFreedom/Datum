"""Карта печатности (`checks/printmap.py`) и виды `part_view`: свет, крупный план, поворот.

Числа карты обязаны совпадать с проверками: она считает теми же функциями.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from datum.checks.dfam import bridge_span
from datum.checks.printing import min_wall, overhang
from datum.checks.printmap import CLASSES, print_map
from datum.geom import box, union


def _table():
    """Свод между ножками (мост 30 мм), консоль у ножки и тонкое ребро 1 мм."""
    legs = union([box(0, 5, 0, 10, 0, 10), box(35, 40, 0, 10, 0, 10), box(0, 40, 0, 10, 10, 13)])
    return union([legs, box(40, 48, 0, 10, 8, 10), box(10, 11, 12, 18, 0, 8)])


def test_the_map_agrees_with_the_checks():
    part = _table()
    pm = print_map(part, wall=1.6)
    # Площадь нависаний — та же маска, что у `overhang`: свод 300 + консоль 80.
    assert overhang(part).evidence[0].data["area_mm2"] == pytest.approx(pm.overhang_mm2)
    assert pm.overhang_mm2 == pytest.approx(380.0)
    # Мост — свод между ножками, консоль мостом не считается.
    assert pm.bridge_mm2 == pytest.approx(300.0)
    assert pm.bridge_span_mm == pytest.approx(bridge_span(part).value)
    # Тонкое ребро 1 мм: его боковые грани, кусок сечения 1×6.
    assert pm.thin_mm2 == pytest.approx(2 * 6 * 8 + 2 * 1 * 8)
    assert pm.thin_piece_mm2 == pytest.approx(6.0, rel=1e-6)
    assert min_wall(part, wall=1.6).status == "fail"


def test_a_part_without_trouble_has_an_empty_map():
    pm = print_map(box(0, 20, 0, 20, 0, 5))
    assert not pm.labels.any() and pm.overhang_mm2 == 0 and pm.thin_mm2 == 0


def test_every_class_has_its_colour():
    assert {v[0] for v in CLASSES.values()} == {"нависание", "мост", "тонкая стенка"}


# --------------------------------------------------------------- операции
async def _call(name, args):
    from datum.tools.registry import OPS

    out = await OPS[name].fn(**args)
    return out.get("error"), out


@pytest.fixture
def latch_copy(tmp_path):
    import shutil
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "examples" / "latch"
    dst = tmp_path / "latch"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".datum", "output"))
    return dst


def _png(project, path):
    return np.asarray(Image.open(project / path).convert("RGB"))


async def test_part_view_draws_the_print_map_with_a_cutaway(latch_copy):
    """Губка корпуса — внутри коробки: снизу её закрывает дно, нужен разрез."""
    from datum.tools.ops import clear_project_cache

    clear_project_cache()
    err, d = await _call("part_view", {"project": str(latch_copy), "part": "корпус",
                                       "color_by": "print"})
    assert not err, err
    assert d["pose"] == "print"
    assert d["print_map"]["overhang_mm2"] == pytest.approx(145.0, rel=0.05)
    img = _png(latch_copy, d["image"]["path"])
    red = np.array([0xE5, 0x53, 0x4B])
    assert (np.abs(img.astype(int) - red).sum(axis=2) < 60).sum() > 100, "красного не видно"


async def test_part_view_render_zoom_and_turntable(latch_copy):
    from datum.tools.ops import clear_project_cache

    clear_project_cache()
    err, lit = await _call("part_view", {"project": str(latch_copy), "part": "корпус",
                                         "style": "render"})
    assert not err, err
    err, plain = await _call("part_view", {"project": str(latch_copy), "part": "корпус"})
    assert lit["image"]["path"] != plain["image"]["path"]
    err, close = await _call("part_view", {"project": str(latch_copy), "part": "корпус",
                                           "zoom": [0, 0, 15, 20, 20, 20]})
    assert not err, err
    err, bad = await _call("part_view", {"project": str(latch_copy), "part": "корпус",
                                         "zoom": [0, 0, 1]})
    assert bad["error"] and "шесть чисел" in bad["error"]
    err, turn = await _call("part_view", {"project": str(latch_copy), "part": "корпус",
                                          "turntable": True})
    assert not err, err
    gif = Image.open(latch_copy / turn["animation"]["path"])
    assert gif.format == "GIF" and gif.n_frames == 24
    sheet = Image.open(latch_copy / turn["image"]["path"])
    assert sheet.format == "PNG" and sheet.width > sheet.height


def test_the_server_serves_a_gif():
    from datum.server.app import OBJECT_NAME

    assert OBJECT_NAME.match("a" * 64 + ".gif")
    assert not OBJECT_NAME.match("a" * 64 + ".gif/../x")


def test_render_style_has_light_and_shadow():
    from datum.geom.convert import vertices_faces
    from datum.viz.render import render_views

    v, f = vertices_faces(union([box(0, 20, 0, 20, 0, 2), box(5, 15, 5, 15, 2, 12)]).manifold)
    img = np.asarray(Image.open(io.BytesIO(
        render_views(v, f, title="ступень", style="render", views=("изометрия",)))).convert("L"))
    part = img[img > 40]
    # Свет, тень и блик: в заливке есть и тёмное, и светлое.
    assert part.max() - np.percentile(part, 5) > 120
    with pytest.raises(ValueError, match="стиль"):
        render_views(v, f, title="x", style="мультик")


def test_a_zoomed_print_map_with_a_cutaway_draws():
    """Крупный план вместе с разрезом падал: маски разреза были по всем граням."""
    from datum.checks.printmap import colors
    from datum.geom import box, diff, union
    from datum.viz.render import render_views

    lid = diff(box(0, 30, 0, 20, 0, 10), [box(2, 28, 2, 18, 2, 10)])
    lid = union([lid, box(2, 28, 2, 4, 7, 8)])                 # губка внутри
    pm = print_map(lid)
    tri = pm.verts[pm.faces]
    keep = tri[:, :, 2].mean(axis=1) >= 6.5
    png = render_views(pm.verts, pm.faces, title="x", face_colors=colors(pm, "#9aa4b2"),
                       views=("изометрия", "снизу в разрезе"),
                       cutaway={"снизу в разрезе": keep}, frame=((0, 0, 5), (30, 10, 10)))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
