"""Приёмочный тест на реальном провале: phone-case-gears v3 против v4.

История из NOTES.md проекта. Версия v3 была напечатана и развалилась в руках:

    «хорошо вышло только кольцо»
    Причина: check3 считал «держит» по касанию (перекрытие > 0.1 мм³ при малом
    сдвиге), а не по зацепу после зазора.

Замеренные потом зацепы v3: клин шторки 0.8 − 0.57 = 0.23 мм, эксцентрики
крышки 0.00, кулачки 0.10. Версия v4 с исправленной методикой проверки была
напечатана и принята.

Этот тест берёт ОБЕ выгруженные сборки и прогоняет по ним проверку `escape`.
Если она работает, v3 обязана провалиться, а v4 — пройти. Это и есть смысл
существования Datum: поймать такое до печати, а не после.

Оговорка о честности. Обман `check3` воспроизвести на выгруженных файлах
НЕЛЬЗЯ: в них печатные зазоры уже заложены, детали друг друга не касаются, и
проверка по касанию не проходит ни на v3, ни на v4. Обман происходил на
номинальной геометрии внутри скрипта сборки, до внесения зазоров.
"""

from __future__ import annotations

import pytest

from tests.corpus.conftest import load_assembly

DIRS = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

#: Печатные зазоры шторки из NOTES.md: v3 — 0.57 мм, v4 — 0.400 мм.
GAP_V3, GAP_V4 = 0.57, 0.400


def _shutter_vs_rest(parts: dict, shutter_key: str, gap: float):
    """Прогнать `escape` для шторки против всех прочих деталей сборки."""
    from manifold3d import Manifold, OpType

    from datum.checks.motion import escape
    from datum.checks.result import CheckRun
    from datum.geom.convert import from_trimesh
    from datum.geom.solid import Solid

    rest = Manifold.batch_boolean(
        [from_trimesh(m) for k, m in parts.items()
         if k != shutter_key and "phone" not in k],
        OpType.Add)
    holder = Solid.wrap(rest, name="всё остальное", op="union")
    shutter = Solid.wrap(from_trimesh(parts[shutter_key]), name="шторка", op="import")
    return CheckRun(tuple(escape(shutter, holder, DIRS, gap=gap)))


@pytest.fixture(scope="module")
def v3(cad_root):
    path = cad_root / "projects/phone-case-gears/output/v3/phone_case_v3_closed.3mf"
    if not path.is_file():
        pytest.skip(f"нет файла {path}")
    return _shutter_vs_rest(load_assembly(path), "2_shutter", GAP_V3)


@pytest.fixture(scope="module")
def v4(cad_root):
    path = cad_root / "projects/phone-case-gears/output/phone_case_v4_closed.3mf"
    if not path.is_file():
        pytest.skip(f"нет файла {path}")
    return _shutter_vs_rest(load_assembly(path), "shutter", GAP_V4)


def _z(run):
    return next(r for r in run.results if r.id.endswith("+Z"))


def test_v3_fails_every_direction(v3):
    """Версия, развалившаяся в руках, не должна пройти ни по одному направлению."""
    assert not v3.ok
    assert len(v3.failed) == len(DIRS), v3.report()


def test_v3_critical_direction_is_the_one_that_popped_out(v3):
    """+Z — наружу из корпуса, именно так шторка и выскакивала."""
    z = _z(v3)
    assert z.status == "fail"
    assert "НЕ ДЕРЖИТ" in z.message
    assert z.value < 1.0, f"зацеп {z.value} мм³ — должен быть ниже порога"


def test_v3_also_jams(v3):
    """«Всё хрустит» из отзыва — деталь не выбирает даже свой печатный зазор."""
    jammed = [r for r in v3.results if "КЛИНИТ" in r.message]
    assert len(jammed) >= 3, v3.report()


def test_v4_passes_every_direction(v4):
    """Версия, которая работает, должна пройти целиком."""
    assert v4.ok, v4.report()


def test_v4_has_a_large_margin_where_v3_failed(v3, v4):
    """Главное число теста: на том же направлении разница в десятки раз."""
    z3, z4 = _z(v3), _z(v4)
    assert z3.value < 1.0 < z4.value
    assert z4.value > 20 * z3.value, (
        f"v3 зацеп {z3.value:.2f} мм³, v4 {z4.value:.2f} мм³ — "
        f"ожидалась разница на порядок")


def test_naive_touch_cannot_be_fooled_by_exported_files(cad_root):
    """Честная оговорка, закреплённая тестом.

    В выгруженных файлах зазоры уже заложены, поэтому проверка по касанию не
    проходит и на v3. Обман check3 жил на номинальной геометрии внутри скрипта.
    """
    from datum.checks.motion import naive_touch
    from datum.checks.result import CheckRun
    from datum.geom.convert import from_trimesh
    from datum.geom.solid import Solid

    path = cad_root / "projects/phone-case-gears/output/v3/phone_case_v3_closed.3mf"
    if not path.is_file():
        pytest.skip(f"нет файла {path}")
    parts = load_assembly(path)
    shell = Solid.wrap(from_trimesh(parts["1_shell"]), name="корпус", op="import")
    shutter = Solid.wrap(from_trimesh(parts["2_shutter"]), name="шторка", op="import")
    run = CheckRun(tuple(naive_touch(shutter, shell, DIRS)))
    assert not run.ok, "на выгрузке касания нет — обман check3 отсюда не воспроизводится"
