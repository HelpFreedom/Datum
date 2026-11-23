"""Замок на инвариант manifold3d, на котором стоит страж CSG.

Если апстрим изменит знак объёма компонент или поведение decompose(), упадёт
этот тест, а не страж молча пропустит дефект.
"""

from __future__ import annotations

import pytest
from manifold3d import Manifold as M

from datum.geom.topology import analyze


@pytest.fixture(scope="module")
def cases() -> dict[str, M]:
    return {
        "solid": M.cube((10, 10, 10)),
        "two_bodies": M.cube((10, 10, 10)) + M.cube((10, 10, 10)).translate((20, 0, 0)),
        "sealed_void": M.cube((10, 10, 10)) - M.cube((4, 4, 4)).translate((3, 3, 3)),
        "through_hole": M.cube((10, 10, 10))
        - M.cylinder(20, 2.0, circular_segments=64).translate((5, 5, -5)),
    }


def test_solid_is_one_body(cases):
    r = analyze(cases["solid"])
    assert r.ok and r.bodies == 1 and r.sealed_voids == 0
    assert r.genus == 0
    assert r.volume == pytest.approx(1000.0)


def test_disjoint_bodies_are_counted(cases):
    r = analyze(cases["two_bodies"])
    assert r.bodies == 2, "два отдельных куба должны быть двумя телами"
    assert r.sealed_voids == 0, "у разделённых тел нет полостей"
    assert all(v > 0 for v in r.body_volumes)


def test_sealed_void_detected_by_negative_volume(cases):
    """Главный инвариант: замкнутая полость = компонента с отрицательным объёмом."""
    r = analyze(cases["sealed_void"])
    assert r.bodies == 1, "снаружи это по-прежнему одно тело"
    assert r.sealed_voids == 1, "внутренняя полость должна быть обнаружена"
    assert r.void_volumes[0] == pytest.approx(-64.0)
    assert r.volume == pytest.approx(936.0)


def test_through_hole_is_not_a_void(cases):
    """Сквозное отверстие — норма, а не дефект."""
    r = analyze(cases["through_hole"])
    assert r.bodies == 1 and r.sealed_voids == 0
    assert r.genus == 1, "сквозное отверстие поднимает genus"


def test_genus_alone_cannot_distinguish(cases):
    """Обоснование конструкции: genus непригоден как единственный признак."""
    assert analyze(cases["two_bodies"]).genus == analyze(cases["sealed_void"]).genus == -1


def test_error_propagates_through_chain():
    """Жёсткая ошибка доживает до контрольной точки — поэтому пооперационный
    страж не нужен (см. план, §3.1)."""
    import numpy as np
    from manifold3d import Mesh

    nan_mesh = M(Mesh(
        vert_properties=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, np.nan]], dtype=np.float32),
        tri_verts=np.array([[0, 1, 2], [0, 1, 3], [1, 2, 3], [0, 2, 3]], dtype=np.uint32),
    ))
    assert analyze(nan_mesh).status == "NonFiniteVertex"

    chained = ((nan_mesh + M.cube((10, 10, 10)).translate((20, 0, 0)))
               - M.cube((2, 2, 2))).translate((1, 1, 1))
    r = analyze(chained)
    assert r.status == "NonFiniteVertex", "ошибка обязана пережить цепочку операций"
    assert r.is_empty


def test_slivers_are_separated_from_bodies():
    """Лезвия (мусор булевых) не должны считаться телами."""
    big = M.cube((10, 10, 10))
    sliver = M.cube((0.01, 0.01, 0.01)).translate((50, 0, 0))   # 1e-6 мм³
    r = analyze(big + sliver)
    assert r.bodies == 1, "лезвие не тело"
    assert r.slivers == 1


def test_a_sealed_void_brings_a_section_through_it():
    from datum.checks.topology import no_sealed_void
    from datum.geom import box, diff

    part = diff(box(0, 20, 0, 20, 0, 10), [box(12, 15, 12, 16, 2, 8)])
    result = no_sealed_void(part, name="куб")
    assert result.status == "fail"
    assert "у (13.50, 14.00, 5.00)" in result.message
    (picture,) = result.evidence
    assert picture.kind == "png" and picture.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_part_without_voids_brings_no_picture():
    from datum.checks.topology import no_sealed_void
    from datum.geom import box

    assert no_sealed_void(box(0, 5, 0, 5, 0, 5), name="куб").evidence == ()
