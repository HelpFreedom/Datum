"""Конвертация manifold ↔ trimesh.

Проверяем не «есть ли ремап», а то, что конвертация сохраняет геометрию: ремап
в 3.5.3 — пустышка, но код обязан быть корректен и при непустых массивах.
"""

from __future__ import annotations

import numpy as np
import pytest
from manifold3d import Manifold as M

from datum.geom.convert import from_trimesh, to_trimesh


def _grooved_cylinder(n: int = 168) -> M:
    """Много мелких граней — сценарий, на котором ломался мешер OCCT."""
    body = M.cylinder(15.0, 20.0, circular_segments=256)
    groove = M.cube((1.2, 3.0, 17.0)).translate((-0.6, 18.5, -1.0))
    cuts = M.batch_boolean([groove.rotate((0, 0, i * 360.0 / n)) for i in range(n)],
                           __import__("manifold3d").OpType.Add)
    return body - cuts


CASES = {
    "cube": lambda: M.cube((10, 10, 10)),
    "difference": lambda: M.cube((10, 10, 10)) - M.cube((4, 4, 4)).translate((3, 3, 3)),
    "grooves_168": _grooved_cylinder,
    "union_50_spheres": lambda: M.batch_boolean(
        [M.sphere(3.0, circular_segments=32).translate((i % 10 * 4, i // 10 * 4, 0))
         for i in range(50)],
        __import__("manifold3d").OpType.Add),
}


@pytest.mark.parametrize("name", list(CASES))
def test_roundtrip_preserves_volume(name):
    man = CASES[name]()
    mesh = to_trimesh(man)
    assert abs(mesh.volume - man.volume()) / max(abs(man.volume()), 1e-9) < 1e-6


@pytest.mark.parametrize("name", list(CASES))
def test_converted_mesh_is_watertight(name):
    mesh = to_trimesh(CASES[name]())
    assert mesh.is_watertight, f"{name}: меш после конвертации обязан быть замкнут"


@pytest.mark.parametrize("name", list(CASES))
def test_manifold_trimesh_manifold_is_stable(name):
    man = CASES[name]()
    back = from_trimesh(to_trimesh(man))
    assert str(back.status()) == "Error.NoError"
    assert abs(back.volume() - man.volume()) / max(abs(man.volume()), 1e-9) < 1e-6


def test_empty_manifold_converts_to_empty_mesh():
    mesh = to_trimesh(M.cube((1, 1, 1)) - M.cube((5, 5, 5)).translate((-2, -2, -2)))
    assert len(mesh.faces) == 0
