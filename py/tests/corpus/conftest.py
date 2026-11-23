"""Тесты на реальном корпусе прежних проектов.

Где он лежит — `DATUM_CORPUS`; без неё — папка `CAD` рядом с репозиторием.

Каталог читается ТОЛЬКО на чтение и никогда не изменяется. Если его нет —
тесты пропускаются, а не падают.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

CAD = Path(os.environ.get("DATUM_CORPUS")
           or Path(__file__).resolve().parents[4] / "CAD").expanduser()


@pytest.fixture(scope="session")
def cad_root() -> Path:
    if not CAD.is_dir():
        pytest.skip(f"корпус {CAD} недоступен")
    return CAD


def load_assembly(path: Path) -> dict:
    """Детали сборки 3MF в МИРОВЫХ координатах."""
    import numpy as np
    import trimesh

    scene = trimesh.load(str(path), force="scene")
    out: dict = {}
    for name, geom in scene.geometry.items():
        node = next((n for n in scene.graph.nodes_geometry
                     if scene.graph[n][1] == name), None)
        transform = scene.graph[node][0] if node else np.eye(4)
        mesh = geom.copy()
        mesh.apply_transform(transform)
        out[name] = mesh
    return out
