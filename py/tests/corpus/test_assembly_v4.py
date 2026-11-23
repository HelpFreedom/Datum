"""Собираемость на реальной сборке: phone-case-gears v4.

Претензия №1 — «каждая деталь по отдельности хороша, а вместе не собрать».
В корпусе это проверялось руками, в `check4.check_body`:

    worst = max(inter_vol(move(phone, dy=dy), case) for dy in range(1, 176, 2))
    gate(worst <= TOL, "телефон вдвигается сверху без планки")

    worst = max(inter_vol(topbar(dx=d, springs=False), case)
                for d in [x * 0.5 for x in range(6, 181)])
    gate(worst <= TOL, "планка надевается сбоку при вставленном телефоне")

Тест берёт ВЫГРУЖЕННУЮ сборку v4 и прогоняет по ней `assembly_path`. Ответы
обязаны совпасть с тем, что записано в NOTES.md: телефон входит сверху, планка
надвигается сбоку.
"""

from __future__ import annotations

import pytest

from tests.corpus.conftest import load_assembly

V4 = "projects/phone-case-gears/output/phone_case_v4_closed.3mf"

#: Натяг планки пружинящими балками, из NOTES.md: «поджим планки балками в
#: языке: натяг 0.30…3.00 мм³». Оригинальная проверка снимала балки вовсе
#: (`springs=False`); здесь деталь взята как напечатана, поэтому допуск
#: поднят до верхней границы задуманного натяга.
SPRING_PRELOAD_MM3 = 3.0


@pytest.fixture(scope="module")
def v4(cad_root):
    path = cad_root / V4
    if not path.is_file():
        pytest.skip(f"нет сборки {path}")
    return load_assembly(path)


def _solid(parts, name):
    from datum.geom.convert import from_trimesh
    from datum.geom.solid import Solid

    return Solid.wrap(from_trimesh(parts[name]), name=name, op="import")


def _merged(parts, names, label):
    from manifold3d import Manifold, OpType

    from datum.geom.convert import from_trimesh
    from datum.geom.solid import Solid

    return Solid.wrap(
        Manifold.batch_boolean([from_trimesh(parts[n]) for n in names], OpType.Add),
        name=label, op="union")


def test_the_phone_goes_in_from_above(v4):
    """NOTES.md: «телефон вдвигается сверху без планки».

    Оригинал перебирал `move(phone, dy=1..175)`, то есть уводил телефон ВВЕРХ:
    в этом проекте верх — +Y (планка, названная в NOTES «Верх», стоит на
    Y 164…170, телефон занимает Y 0…164). Вставка, значит, идёт ВНИЗ, в −Y, — и
    проверка обязана называть именно её, потому что собирают, а не разбирают.
    """
    from datum.checks.assembly import assembly_path

    result = assembly_path(_solid(v4, "phone_not_print"), _solid(v4, "case"),
                           part_name="телефон", distance=180.0, samples=30)
    assert result.status == "pass", result.message
    assert "сдвигом -Y" in result.message
    assert result.value == pytest.approx(0.0, abs=0.05)


def test_the_bar_slides_in_sideways_with_the_phone_already_in(v4):
    """NOTES.md: «планка надевается сбоку СПРАВА при вставленном телефоне».

    Справа — это со стороны +X, значит планка движется в −X. Порядок сборки
    здесь и проверяется: телефон входит первым и становится препятствием.
    """
    from datum.checks.assembly import assembly_path

    result = assembly_path(
        _solid(v4, "topbar"),
        _merged(v4, ["case", "phone_not_print"], "корпус с телефоном"),
        part_name="планка", distance=95.0, samples=30,
        max_inter_vol=SPRING_PRELOAD_MM3)
    assert result.status == "pass", result.message
    assert "сдвигом -X" in result.message


def test_the_spring_preload_is_what_the_notes_say(v4):
    """Число из NOTES.md проверяется числом, а не на веру: наибольшее задевание
    планки на пути обязано лежать внутри задуманного натяга 0.30…3.00 мм³."""
    from datum.checks.assembly import AssemblyProbe
    from datum.checks.motion import direction

    # AssemblyProbe — примитив: ему даётся направление, в котором деталь
    # УВОДЯТ от посадки. Планка вставляется в −X, значит уводят её в +X.
    probe = AssemblyProbe(_solid(v4, "topbar"),
                          [_merged(v4, ["case", "phone_not_print"], "корпус")],
                          direction("+X"))
    worst = max(probe.at(d) for d in (0.5, 1.0, 2.0, 5.0, 10.0, 30.0))
    assert 0.30 <= worst <= SPRING_PRELOAD_MM3, (
        f"натяг {worst:.2f} мм³ вне записанного в NOTES.md 0.30…3.00")


def test_a_tight_tolerance_explains_itself_instead_of_just_refusing(v4):
    """Если натяг не учли, проверка обязана не просто отказать, а сказать, что
    посмотреть. Урок из корпуса: там эту проверку гоняли с `springs=False`, и
    без этого знания «1.3 мм³» выглядит затором."""
    from datum.checks.assembly import assembly_path

    result = assembly_path(
        _solid(v4, "topbar"),
        _merged(v4, ["case", "phone_not_print"], "корпус с телефоном"),
        part_name="планка", distance=95.0, samples=25, max_inter_vol=0.05)
    assert result.status == "fail"
    assert "% пути" in result.message
    assert "пружинящими" in result.message
    # И назвала лучшее направление, а не просто «нет». Проверяется вместе с
    # «Ближе всех»: просто «-X» есть в перечислении всех шести и ничего бы не
    # доказало.
    assert "Ближе всех -X" in result.message


def test_the_wrong_direction_is_two_orders_worse(v4):
    """Различать «натяг» и «затор» по доле пути нельзя — померено, не
    разделяется. Различает величина: тут два порядка."""
    from datum.checks.assembly import AssemblyProbe
    from datum.checks.motion import direction

    obstacles = [_merged(v4, ["case", "phone_not_print"], "корпус")]
    bar = _solid(v4, "topbar")
    # Уводим планку туда, откуда её и надевали (+X), и в противоположную.
    right = max(AssemblyProbe(bar, obstacles, direction("+X")).at(d)
                for d in (0.5, 2.0, 10.0, 30.0))
    wrong = max(AssemblyProbe(bar, obstacles, direction("-X")).at(d)
                for d in (0.5, 2.0, 10.0, 30.0))
    assert wrong > right * 50
