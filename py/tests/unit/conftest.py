"""Общие приспособления для модульных тестов.

Главное здесь — чистая копия примера. Сборка создаёт в проекте `.datum/cache`,
и если копировать пример вместе с ним, «холодный» прогон оказывается тёплым:
тест кеша начинает врать. Поэтому копия всегда без `.datum` и `snapshots`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LATCH = ROOT / "examples" / "latch"

#: То, что порождается работой и не должно переезжать в свежую копию.
GENERATED = shutil.ignore_patterns(".datum", "snapshots", "__pycache__", "output")


def copy_project(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst, ignore=GENERATED)
    return dst


@pytest.fixture
def latch(tmp_path) -> Path:
    """Чистая копия примера `latch`."""
    return copy_project(LATCH, tmp_path / "latch")


#: Кубик-стакан — бывшая деталь шаблона `init`. Шаблон теперь пустой (решение
#: владельца), а тестам по-прежнему нужен самый простой проект С деталью:
#: в центре стола (`bed()`), с параметрами трёх видов и четырьмя проверками.
CUBE_PARAMS = """
SIZE:   {value: 20.0, unit: mm, provenance: chosen, reason: "габарит кубика"}
NOZZLE: {value: 0.4, unit: mm, provenance: standard, ref: "сопло принтера"}
WALL:   {provenance: derived, expr: "4 * NOZZLE", unit: mm}
"""

CUBE_PART = '''\
"""Кубик-стакан: коробка SIZE со стенками WALL и открытым верхом, в центре стола."""

from datum.build import P, bed, part
from datum.geom import box, diff


@part("кубик", color="#8a93a5", print_up="+Z")
def build():
    bx, by, _ = bed()
    x0, y0 = bx / 2 - P.SIZE / 2, by / 2 - P.SIZE / 2      # угол у центра стола
    outer = box(x0, x0 + P.SIZE, y0, y0 + P.SIZE, 0, P.SIZE)
    pocket = box(x0 + P.WALL, x0 + P.SIZE - P.WALL, y0 + P.WALL, y0 + P.SIZE - P.WALL,
                 P.WALL, P.SIZE + 1)
    return diff(outer, [pocket])
'''

CUBE_CHECKS = """
- id: кубик.целостность
  use: single_body
  args: {part: "@кубик"}

- id: кубик.без_пузырей
  use: no_sealed_void
  args: {part: "@кубик"}

- id: кубик.стенка
  use: min_wall
  args: {part: "@кубик", wall: "${params.WALL}", samples: 8}

- id: кубик.на_столе
  use: bbox_fits
  args: {part: "@кубик"}
"""


def cube_project(root: Path) -> Path:
    """Пустой проект `datum init` плюс кубик: деталь, её параметры и проверки.
    Тестам — фикстурой `cube`."""
    from datum.doc.scaffold import init_project

    init_project(root)
    for rel, text in (("params/base.yaml", CUBE_PARAMS), ("checks/checks.yaml", CUBE_CHECKS)):
        path = root / rel
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
    (root / "parts" / "cube.py").write_text(CUBE_PART, encoding="utf-8")
    return root


@pytest.fixture
def cube(tmp_path) -> Path:
    """Проект с кубиком в центре стола (`cube_project`)."""
    return cube_project(tmp_path / "кубик")
