"""Объёмный растр детали.

Нужен тому, что рассуждает слоями: поддержкам, мостам, прилипанию. Поверхность
для них не тот язык — вопрос «есть ли что-то под этой точкой» через грани не
задать.
"""

from __future__ import annotations

import math

import pytest

from datum.geom.booleans import diff
from datum.geom.measure import vol
from datum.geom.prims import box, cyl_z
from datum.geom.voxel import voxelize


def test_a_box_is_rasterised_exactly():
    """У коробки грани лежат по ячейкам, и ошибке взяться неоткуда. Если она
    появится — сломана сама укладка сетки, а не дискретизация."""
    for step in (2.0, 1.0, 0.5, 0.25):
        grid = voxelize(box(0, 20, 0, 20, 0, 20), step)
        assert grid.volume == pytest.approx(8000.0, rel=1e-9), step


def test_the_volume_converges_on_a_curved_body():
    """У тела с дыркой ошибка живёт на границе и обязана убывать с шагом."""
    holed = diff(box(0, 20, 0, 20, 0, 10), [cyl_z(8, -1, 11, x=10, y=10)])
    exact = vol(holed)
    errors = []
    for step in (1.0, 0.5, 0.25):
        errors.append(abs(voxelize(holed, step).volume - exact) / exact)
    assert errors[0] > errors[1] > errors[2], errors
    assert errors[-1] < 0.02


def test_the_grid_is_centred_on_cells_not_on_nodes():
    """Слой у самого дна не должен попадать РОВНО на границу тела: тогда ответ
    «занято или нет» зависел бы от округления."""
    grid = voxelize(box(0, 10, 0, 10, 0, 10), 1.0)
    assert grid.z_of(0) == pytest.approx(0.5)
    assert grid.filled[0].all(), "нижний слой обязан быть полным"


def test_holes_are_punched_and_not_painted_over():
    """Сквозное отверстие обязано остаться пустым во всех слоях."""
    holed = diff(box(0, 20, 0, 20, 0, 10), [cyl_z(8, -1, 11, x=10, y=10)])
    grid = voxelize(holed, 0.5)
    middle = grid.filled[:, grid.shape[1] // 2, grid.shape[2] // 2]
    assert not middle.any(), "ось отверстия оказалась занятой"


def test_a_grid_that_would_not_fit_in_memory_is_refused():
    """Отказ с числом понятнее съеденной памяти."""
    with pytest.raises(ValueError, match="больше предела"):
        voxelize(box(0, 200, 0, 200, 0, 200), 0.05, max_cells=1_000_000)


@pytest.mark.parametrize("step", [0.0, -1.0])
def test_a_nonsensical_step_is_refused(step):
    with pytest.raises(ValueError, match="шаг"):
        voxelize(box(0, 10, 0, 10, 0, 10), step)


def test_a_part_thinner_than_the_step_is_refused():
    """Листок 0.2 мм при шаге 1 мм давал ПУСТУЮ сетку, а проверки по ней бодро
    отвечали «поддержек нет». Молча такое проходить не должно: отказ с числом
    и с подсказкой, какой шаг взять."""
    with pytest.raises(ValueError, match="тоньше шага"):
        voxelize(box(0, 10, 0, 10, 0, 0.2), 1.0)
    # А при подходящем шаге та же деталь описывается.
    grid = voxelize(box(0, 10, 0, 10, 0, 0.2), 0.05)
    assert grid.filled.any() and grid.layer_count() >= 4
