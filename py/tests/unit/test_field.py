"""Поле на сетке (`datum.geom.field`): точность, замкнутость, печатность.

Числа в порогах — из замеров при написании модуля, с запасом; где замер
записан в докстринге модуля, он повторён здесь проверкой.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.ndimage import map_coordinates
from shapely.geometry import Polygon

from datum.geom import field as F
from datum.geom.convert import vertices_faces
from datum.geom.topology import analyze
from datum.io.precision import coincident_pairs


def _grid(h=0.4, r=20.0):
    return F.Grid.around((-r, -r, -r), (r, r, r), h)


# ------------------------------------------------------------- поверхность
@pytest.mark.parametrize("h", [1.0, 0.5, 0.25])
def test_a_sphere_is_closed_and_its_volume_converges(h):
    g = _grid(h, 11)
    rep = analyze(F.mesh(F.sphere(g, 20.0, 0.3, 0.1, 0.2)).manifold)
    exact = 4 / 3 * math.pi * 1000
    assert rep.status == "NoError" and rep.bodies == 1 and rep.genus == 0
    # Второй порядок: 0.50 % при h=1, 0.125 % при 0.5, 0.031 % при 0.25.
    assert abs(rep.volume - exact) / exact < 0.6 * h * h / 100


def test_a_torus_keeps_its_hole():
    g = F.Grid.around((-15, -15, -5), (15, 15, 5), 0.4)
    X, Y, Z = g.mesh()
    q = np.sqrt(X ** 2 + Y ** 2) - 10
    rep = analyze(F.mesh(F.Field(g, (4 - np.sqrt(q ** 2 + Z ** 2)).astype(np.float32))).manifold)
    assert rep.genus == 1
    assert rep.volume == pytest.approx(2 * math.pi ** 2 * 10 * 16, rel=3e-3)


def test_a_sealed_void_survives_meshing_for_the_guard_to_judge():
    g = _grid(0.4, 6)
    rep = analyze(F.mesh(F.diff(F.sphere(g, 9), F.sphere(g, 4))).manifold)
    assert rep.bodies == 1 and rep.sealed_voids == 1


def test_crumbs_are_dropped_but_small_voids_are_not():
    g = _grid(0.25, 10)
    body = F.union(F.sphere(g, 12), F.sphere(g, 0.9, 8, 8, 8))     # крупинка 0.38 мм³
    tiny_void = F.diff(body, F.sphere(g, 0.9))                       # полость 0.38 мм³
    rep = analyze(F.mesh(tiny_void, min_body_mm3=1.0).manifold)
    assert rep.bodies == 1, "крупинка меньше порога выброшена"
    assert rep.sealed_voids == 1, "полость любого размера остаётся стражу"


def test_the_field_mesh_has_no_vertices_that_merge_in_a_file():
    g = _grid(0.4, 12)
    shape = F.smooth_union(F.box(g, -8, 8, -8, 8, -8, 0), F.sphere(g, 12, 0, 0, 2), 3)
    verts, _ = vertices_faces(F.mesh(shape).manifold)
    assert len(coincident_pairs(verts)) == 0


def test_a_surface_reaching_the_grid_edge_is_closed_by_it():
    g = F.Grid.around((0, 0, 0), (10, 10, 10), 0.5, margin=0)
    rep = analyze(F.mesh(F.box(g, -5, 5, -5, 5, -5, 5)).manifold)    # шире решётки
    assert rep.status == "NoError" and rep.bodies == 1
    assert rep.volume == pytest.approx(125.0, rel=0.05)


# --------------------------------------------------------------- примитивы
def test_primitives_are_exact_distances():
    g = _grid(0.5, 10)
    X, Y, Z = np.broadcast_arrays(*g.mesh())
    b = F.box(g, -2, 4, -3, 3, -1, 5).values
    i = np.argmin(np.abs(X[:, 0, 0] - 7)), np.argmin(np.abs(Y[0, :, 0])), np.argmin(np.abs(Z[0, 0, :] - 2))
    assert b[i] == pytest.approx(-3.0, abs=1e-5)                       # 3 мм справа от грани
    corner = np.argmin(np.abs(X[:, 0, 0] - 6)), np.argmin(np.abs(Y[0, :, 0] - 5)), np.argmin(np.abs(Z[0, 0, :] - 7))
    assert b[corner] == pytest.approx(-math.sqrt(12), abs=1e-5)       # у угла — до угла
    c = F.cyl_z(g, 8, -5, 5, 1, 0).values
    j = np.argmin(np.abs(X[:, 0, 0] - 1)), np.argmin(np.abs(Y[0, :, 0])), np.argmin(np.abs(Z[0, 0, :]))
    assert c[j] == pytest.approx(4.0, abs=1e-5)


def test_sdf2d_is_exact_at_the_boundary():
    g = F.Grid.around((-10, -10, 0), (10, 10, 1), 0.4)
    square = Polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)])
    d = F.sdf2d(g, square)
    x, y, _ = g.axes()
    X, Y = np.meshgrid(x, y, indexing="ij")
    true = np.minimum(5 - np.abs(X), 5 - np.abs(Y))
    band = np.abs(true) < 0.7
    outside_corner = (np.abs(X) > 5) & (np.abs(Y) > 5)
    band &= ~outside_corner
    assert np.abs(d[band] - true[band]).max() < 1e-4


def _reference_sdf(g, poly):
    """Эталон: `shapely.distance` до границы, перебором всех отрезков."""
    import shapely

    x, y, _ = g.axes()
    X, Y = np.meshgrid(x, y, indexing="ij")
    d = shapely.distance(poly.boundary, shapely.points(X.ravel(), Y.ravel())).reshape(X.shape)
    return np.where(shapely.contains_xy(poly, X, Y), d, -d)


def _awkward_contours():
    """Контуры, на которых индекс легко ошибся бы: тысячи коротких отрезков,
    центр круга (всё равноудалено), дыры, части, длинные отрезки, щель уже клетки."""
    import shapely

    t = np.linspace(0, 2 * np.pi, 3000, endpoint=False)
    r = 30 + 3 * np.sin(13 * t) + 1.5 * np.sin(41 * t)
    wave = Polygon(np.c_[r * np.cos(t), r * np.sin(t)]).buffer(0.65)
    circle = shapely.Point(0, 0).buffer(20, quad_segs=256)
    rng = np.random.default_rng(7)
    holes = shapely.box(-30, -30, 30, 30).difference(
        shapely.MultiPoint(rng.uniform(-22, 22, (25, 2))).buffer(2.5))
    parts = shapely.MultiPolygon([shapely.box(-30, -30, -20, -20), shapely.box(10, -5, 11, 30)])
    # щель 0.1 мм — уже клетки 0.4: маска её не видит
    slit = shapely.box(-20, -20, 20, 20).difference(shapely.box(-0.05, -15, 0.05, 15))
    return {"волна": wave, "круг": circle, "дыры": holes, "части": parts, "щель": slit}


@pytest.mark.parametrize("name", list(_awkward_contours()))
def test_sdf2d_matches_brute_force_everywhere(name):
    """Индекс (KD по кускам границы) обязан давать то же, что перебор: это
    расстояние точное по построению, а не оценка."""
    poly = _awkward_contours()[name]
    g = F.Grid.around((-35, -35, 0), (35, 35, 1), 0.4)
    ref = _reference_sdf(g, poly)
    d = F.sdf2d(g, poly)
    assert np.abs(d - ref).max() < 2e-5           # float32 на 50 мм
    sign_ok = (np.sign(d) == np.sign(ref)) | (ref == 0)
    assert sign_ok.all()


@pytest.mark.parametrize("name", list(_awkward_contours()))
def test_sdf2d_band_is_exact_inside_and_honest_beyond(name):
    """С полосой — точно в ней (и у щели уже клетки, которую маска не видит),
    дальше — не меньше полосы, знак верный, ошибка не больше двух клеток."""
    poly = _awkward_contours()[name]
    h, band = 0.4, 3.0
    g = F.Grid.around((-35, -35, 0), (35, 35, 1), h)
    ref = _reference_sdf(g, poly)
    d = F.sdf2d(g, poly, band=band)
    inside = np.abs(ref) <= band
    assert np.abs(d - ref)[inside].max() < 2e-5
    assert (np.abs(d[~inside]) >= band - 1e-6).all()
    assert np.abs(d - ref)[~inside].max() <= 2 * h
    assert ((np.sign(d) == np.sign(ref)) | (ref == 0)).all()


def test_extrude_and_loft_volumes():
    g = F.Grid.around((-8, -8, 0), (8, 8, 10), 0.25)
    sq = Polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)])
    small = Polygon([(-2, -2), (2, -2), (2, 2), (-2, 2)])
    prism = analyze(F.mesh(F.extrude(g, sq, 1, 9)).manifold)
    # Острые рёбра срезаны примерно на h/2: замер −0.23 %.
    assert prism.volume == pytest.approx(800.0, rel=4e-3)
    from datum.geom.measure import section_xy

    lofted = F.mesh(F.loft(g, [(sq, 1), (sq, 5), (small, 9)]))
    # Между одинаковыми сечениями — ровно они (рёбра крышек срезаны, поэтому
    # меряем в середине).
    assert section_xy(lofted, 3.0).area == pytest.approx(sq.area, rel=3e-3)
    # Дальше площадь убывает монотонно, без ступеней.
    areas = [section_xy(lofted, z).area for z in np.linspace(5.2, 8.5, 12)]
    assert all(a > b for a, b in zip(areas, areas[1:]))
    assert areas[0] < sq.area and areas[-1] > small.area


def test_fields_on_different_grids_are_refused():
    with pytest.raises(ValueError, match="разных сетках"):
        F.union(F.sphere(_grid(0.4, 5), 4), F.sphere(_grid(0.5, 5), 4))


# ----------------------------------------------------------- переизмерение
def test_redistance_is_exact_near_the_surface_and_keeps_it_in_place():
    g = _grid(0.4, 14)
    scaled = F.sphere(g, 20) * 2.0                 # тот же шар, но не расстояние
    out = F.redistance(scaled).values
    X, Y, Z = np.broadcast_arrays(*g.mesh())
    true = 10 - np.sqrt(X ** 2 + Y ** 2 + Z ** 2)
    band = np.abs(true) < 1.5
    assert np.abs(out[band] - true[band]).max() < 0.01      # замер: 0.006 мм
    before = analyze(F.mesh(scaled).manifold).volume
    after = analyze(F.mesh(F.redistance(scaled)).manifold).volume
    assert after == pytest.approx(before, rel=5e-4)


def test_an_offset_after_redistance_lands_where_it_should():
    """Зазор 0.3 мм на переизмеренном поле — ради него точность у поверхности."""
    g = _grid(0.4, 14)
    grown = F.offset(F.redistance(F.sphere(g, 20) * 3.0), 0.3)
    rep = analyze(F.mesh(grown).manifold)
    assert rep.volume == pytest.approx(4 / 3 * math.pi * 10.3 ** 3, rel=2e-3)


# ---------------------------------------------------------- предел уклона
def _mushroom(g, cap_z=18.0):
    return F.union(F.cyl_z(g, 10, 0, 20), F.cyl_z(g, 36, cap_z, cap_z + 6))


def _worst_step(field: F.Field, z_lo: float, z_hi: float) -> float:
    """Наибольшее сужение опоры за слой по нескольким лучам, мм."""
    g = field.grid
    _, _, z = g.axes()
    worst = 0.0
    for ang in (0.0, math.pi / 8, math.pi / 4, 0.3):
        rs = np.arange(4, 19.5, 0.002)
        px = (rs * math.cos(ang) - g.origin[0]) / g.h
        py = (rs * math.sin(ang) - g.origin[1]) / g.h
        prev = None
        for k in range(len(z)):
            if not z_lo <= z[k] <= z_hi:
                continue
            vals = map_coordinates(field.values[:, :, k].astype(np.float64), [px, py], order=1)
            ins = np.nonzero(vals > 0)[0]
            r = rs[ins.max()] if len(ins) else None
            if prev is not None and r is not None:
                worst = max(worst, r - prev)
            prev = r
    return worst


@pytest.mark.parametrize("cap_z", [18.0, 18.1])
def test_limit_overhang_makes_a_45_degree_support(cap_z):
    """Замер: 0.268 мм на слой при 0.250 (47°) — и с потолком на слое сетки, и между."""
    g = F.Grid.around((-20, -20, 0), (20, 20, 30), 0.25)
    out = F.limit_overhang(_mushroom(g, cap_z), 45)
    step = _worst_step(out, 5.5, cap_z + 0.3)
    assert step <= 0.25 * 1.15, f"{step:.3f} мм за слой — {math.degrees(math.atan(step / 0.25)):.1f}°"


@pytest.mark.parametrize("t", [0.8, 1.0, 2.0])
def test_a_thin_shelf_gets_its_support_too(t):
    """Пропуск слоя по значению поля оставлял полку тоньше ~2·shrink без опоры."""
    g = F.Grid.around((-16, -16, 0), (16, 16, 20), 0.4)
    shape = F.union(F.cyl_z(g, 6, 0, 14), F.box(g, -15, 15, -15, 15, 14, 14 + t))
    out = F.limit_overhang(shape, 45)
    assert int(((out.values > 0) & ~(shape.values > 0)).sum()) > 50_000


def test_limit_overhang_adds_nothing_below_the_part():
    g = F.Grid.around((-20, -20, 0), (20, 20, 30), 0.5)
    out = F.limit_overhang(_mushroom(g), 45)
    verts, _ = vertices_faces(F.mesh(out).manifold)
    assert verts[:, 2].min() > -0.01, "ниже дна детали — стол"


def test_limit_overhang_leaves_a_printable_part_alone():
    g = _grid(0.4, 12)
    cone = F.Field(g, np.minimum(F.cyl_z(g, 20, -8, 8).values,
                                 F.plane(g, (0, 0, 8), (0.7071, 0, 0.7071)).values))
    before = F.mesh(cone).manifold.volume()
    after = F.mesh(F.limit_overhang(cone, 45)).manifold.volume()
    assert after == pytest.approx(before, rel=1e-6)


@pytest.mark.parametrize("up", ["+X", "-Z"])
def test_limit_overhang_follows_the_print_orientation(up):
    g = _grid(0.4, 20)
    if up == "+X":
        shape = F.union(F.cyl_x(g, 10, -15, 5), F.cyl_x(g, 30, 3, 8))
    else:
        shape = F.union(F.cyl_z(g, 10, -5, 15), F.cyl_z(g, 30, -8, -3))
    ref = F.union(F.cyl_z(g, 10, -15, 5), F.cyl_z(g, 30, 3, 8))
    got = F.mesh(F.limit_overhang(shape, 45, up=up)).manifold.volume()
    want = F.mesh(F.limit_overhang(ref, 45, up="+Z")).manifold.volume()
    assert got == pytest.approx(want, rel=1e-6)
    assert got > F.mesh(shape).manifold.volume() * 1.3, "опора добавлена"


# ------------------------------------------------------------------- узор
def test_down_share_is_positive_under_an_overhang():
    g = _grid(0.5, 12)
    share = F.down_share(F.sphere(g, 20)).values
    x, y, z = g.axes()
    i = np.argmin(np.abs(x))
    bottom = share[i, i, np.argmin(np.abs(z + 9.5))]
    top = share[i, i, np.argmin(np.abs(z - 9.5))]
    assert bottom > 0.9 and top < -0.9


def test_a_pattern_on_a_wall_cuts_in_and_leaves_no_voids():
    g = F.Grid.around((-20, -20, -2), (20, 20, 32), 0.4)
    wall = F.redistance(F.cyl_z(g, 30, 0, 30))
    pattern = F.diagrid(8.0, axis_xy=(0, 0, 15))
    out = F.surface_pattern(wall, pattern, 1.0)
    rep = analyze(F.mesh(out).manifold)
    plain = analyze(F.mesh(wall).manifold)
    assert rep.sealed_voids == 0 and rep.bodies == 1
    assert rep.volume < plain.volume
    assert float((wall.values - out.values).max()) <= 1.0 + 1e-5, "не глубже заданного"


def test_a_pattern_fades_where_the_surface_looks_down():
    g = _grid(0.4, 14)
    ball = F.redistance(F.sphere(g, 24))
    out = F.surface_pattern(ball, F.honeycomb(6.0, axis_xy=(0, 0, 12)), 1.0)
    x, y, z = g.axes()
    i = np.argmin(np.abs(x))
    k = np.argmin(np.abs(z + 11.6))                  # низ шара — потолок
    assert out.values[i, i, k] == pytest.approx(ball.values[i, i, k], abs=1e-6)


# ------------------------------------------------------------------ пример
def test_the_sculpt_example_builds_and_passes_its_checks(tmp_path):
    """`examples/sculpt` — тот же конвейер, что у подставки под мышь, в малом."""
    import shutil
    from pathlib import Path

    from datum.doc.project import Project

    src = Path(__file__).resolve().parents[3] / "examples" / "sculpt"
    dst = tmp_path / "sculpt"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".datum", "output"))
    pr = Project.open(dst)
    res = pr.build()["подставка"]
    assert res.ok, res.line()
    assert res.topology.sealed_voids == 0 and res.topology.bodies == 1
    run = pr.run_checks()
    assert run.ok, run.report()
    rough = Project.open(dst).build(draft=True)["подставка"]
    assert rough.ok and rough.draft
    # Замер: 27 тыс. треугольников за 0.5 с против 63 тыс. за 3.8 с.
    assert rough.topology.triangles < res.topology.triangles / 2, "черновик грубее"
