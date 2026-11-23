"""Объявление детали и сборка."""

from __future__ import annotations

import pytest

from datum.build import (NoParamsError, P, PARTS, build_part, clear_parts, part,
                         use_params)
from datum.doc.params import ParamRegistry
from datum.geom import Expect, box, cyl_z, diff, move, union
from datum.geom.solid import Solid

PARAMS = {
    "W": {"value": 40.0, "unit": "mm", "provenance": "chosen", "reason": "ширина"},
    "H": {"value": 20.0, "unit": "mm", "provenance": "chosen", "reason": "высота"},
    "T": {"value": 5.0, "unit": "mm", "provenance": "chosen", "reason": "толщина"},
    "NOZZLE": {"value": 0.4, "unit": "mm", "provenance": "standard", "ref": "сопло"},
    "WALL": {"provenance": "derived", "expr": "4 * NOZZLE", "unit": "mm"},
    "HOLE_D": {"value": 6.0, "unit": "mm", "provenance": "chosen", "reason": "под винт"},
}


@pytest.fixture(autouse=True)
def clean():
    clear_parts()
    yield
    clear_parts()


@pytest.fixture
def reg():
    return ParamRegistry.from_dict(PARAMS)


def declare_plate():
    @part("плита", color="#c44", print_up="-Y")
    def build():
        """Плита с отверстием."""
        return diff(box(0, P.W, 0, P.H, 0, P.T),
                    [cyl_z(P.HOLE_D, -1, P.T + 1, P.W / 2, P.H / 2)])
    return PARTS["плита"]


# ------------------------------------------------------------------ сборка
def test_part_builds_and_is_measured(reg):
    res = build_part(declare_plate(), reg)
    assert res.ok, res.error
    assert res.topology.bodies == 1 and res.topology.sealed_voids == 0
    assert res.solid.name == "плита"
    assert res.ops > 0, "журнал операций должен заполниться"
    assert res.ms >= 0


def test_depends_on_includes_derived_chain(reg):
    @part("стенка")
    def build():
        return box(0, P.W, 0, P.WALL, 0, P.T)

    res = build_part(PARTS["стенка"], reg)
    assert res.ok
    # WALL прочитан напрямую, NOZZLE — через формулу
    assert {"W", "T", "WALL", "NOZZLE"} <= set(res.depends_on)


def test_overrides_reach_the_geometry(reg):
    pdef = declare_plate()
    base = build_part(pdef, reg).topology.volume
    wide = build_part(pdef, reg, overrides={"W": 80.0}).topology.volume
    assert wide > 1.9 * base


def test_part_can_take_a_motion_argument(reg):
    @part("ползун")
    def build(u: float = 0.0):
        return move(box(0, 10, 0, 10, 0, 10), 20.0 * u, 0, 0)

    a = build_part(PARTS["ползун"], reg, u=0.0)
    b = build_part(PARTS["ползун"], reg, u=1.0)
    assert a.ok and b.ok
    assert b.solid.manifold.bounding_box()[0] == pytest.approx(20.0)


# ------------------------------------------------------------------ ошибки
def test_guard_error_is_captured_not_raised(reg):
    @part("развалится")
    def build():
        return union([box(0, 10, 0, 10, 0, 10), box(40, 50, 0, 10, 0, 10)])

    res = build_part(PARTS["развалится"], reg)
    assert not res.ok
    assert res.error.reason == "body_count"
    assert "распалась" in res.error.message
    assert "СБОЙ" in res.line()


def test_sealed_void_is_caught_at_the_checkpoint(reg):
    @part("с пузырём")
    def build():
        return diff(box(0, 20, 0, 20, 0, 20), [box(5, 15, 5, 15, 5, 15)])

    res = build_part(PARTS["с пузырём"], reg)
    assert not res.ok and res.error.reason == "sealed_void"


def test_intentional_void_can_be_declared(reg):
    @part("бак", expect=Expect(allow_sealed_voids=True))
    def build():
        return diff(box(0, 20, 0, 20, 0, 20), [box(5, 15, 5, 15, 5, 15)])

    res = build_part(PARTS["бак"], reg)
    assert res.ok and res.topology.sealed_voids == 1


def test_wrong_return_type_is_explained(reg):
    @part("не тело")
    def build():
        return 42

    res = build_part(PARTS["не тело"], reg)
    assert not res.ok and "должна вернуть Solid" in str(res.error)


def test_unknown_parameter_is_explained(reg):
    @part("опечатка")
    def build():
        return box(0, P.WIDTH, 0, 10, 0, 10)      # правильное имя — W

    res = build_part(PARTS["опечатка"], reg)
    assert not res.ok and "нет в реестре" in str(res.error)


# ------------------------------------------------------------------ контекст
def test_params_are_unavailable_outside_a_build():
    with pytest.raises(NoParamsError, match="только во время сборки"):
        _ = P.W


def test_use_params_context(reg):
    with use_params(reg.proxy()):
        assert P.W == pytest.approx(40.0)
        assert P["H"] == pytest.approx(20.0)
    with pytest.raises(NoParamsError):
        _ = P.W


def test_params_cannot_be_written(reg):
    with use_params(reg.proxy()):
        with pytest.raises(AttributeError, match="менять"):
            P.W = 1.0


def test_declaration_registers_metadata(reg):
    pdef = declare_plate()
    assert pdef.color == "#c44" and pdef.print_up == "-Y"
    assert pdef.doc == "Плита с отверстием."


def test_silent_errors_get_an_explanation(reg):
    """У MemoryError пустой str(), и «СБОЙ деталь:» выходил без объяснения."""
    from datum.build.part import describe_error
    from datum.geom.guard import GuardError

    assert describe_error(MemoryError()) == "не хватило памяти при сборке детали"
    assert describe_error(RecursionError()) == "слишком глубокая рекурсия в скрипте детали"
    assert describe_error(ValueError("так вышло")) == "ValueError: так вышло"
    assert describe_error(None) == ""

    @part("развалится")
    def build():
        return union([box(0, 10, 0, 10, 0, 10), box(40, 50, 0, 10, 0, 10)])

    res = build_part(PARTS["развалится"], reg)
    assert isinstance(res.error, GuardError)
    # У ошибки стража своё сообщение — тип к нему не приписывается.
    assert "распалась" in describe_error(res.error)
    assert not describe_error(res.error).startswith("GuardError")


@pytest.mark.parametrize("up", ["плашмя", "Z", "+z", "+Z "])
def test_print_up_accepts_only_axes(up):
    """Свободная строка ориентации ничего не говорила проверкам — и однажды была
    прочитана неверно. Теперь словарь закрытый, опечатка — отказ сразу."""
    with pytest.raises(ValueError, match="print_up"):
        part("брусок", print_up=up)


def test_print_up_vocabulary_is_what_the_checks_can_turn():
    from datum.build.part import PRINT_UP
    from datum.checks.dfam import _UPRIGHT

    assert set(PRINT_UP) == set(_UPRIGHT)


# ------------------------------------------------------------- деталь на столе
def test_centre_on_bed_puts_a_part_in_the_middle_and_on_the_bed():
    """Построенное от угла (box(0, …)) встаёт в центр стола и опускается на него."""
    from datum.build import bed_centre, centre_on_bed, use_project
    from datum.geom import box
    from datum.geom.measure import bbox

    with use_project("/нет", bed=(200, 300, 250)):
        (x0, y0, z0), (x1, y1, _) = bbox(centre_on_bed(box(0, 40, 0, 20, 5, 15)))
        assert ((x0 + x1) / 2, (y0 + y1) / 2, z0) == pytest.approx((100, 150, 0))
        (x0, _, _), _ = bbox(centre_on_bed(box(0, 40, 0, 20, 0, 5), dx=30))
        assert x0 == pytest.approx(110)
        _, (_, _, z1) = bbox(centre_on_bed(box(0, 4, 0, 4, 10, 12), drop=False))
        assert z1 == pytest.approx(12)
        assert bed_centre() == (100, 150)


def test_bed_warnings_name_the_edge_and_the_overhang():
    from datum.build.place import bed_warnings

    bed = (256, 256, 256)
    corner = {"корпус": ((0, 0, 0), (60, 40, 20))}
    assert any("у края стола" in w for w in bed_warnings(corner, bed, whole=True))
    # Не все детали собраны — о группе молчим: узел мог стоять нарочно сбоку.
    assert bed_warnings(corner, bed, whole=False) == []
    centred = {"корпус": ((98, 108, 0), (158, 148, 20))}
    assert bed_warnings(centred, bed, whole=True) == []
    over = {"балка": ((-10, 100, 0), (300, 120, 5))}
    assert any("выходит за стол" in w for w in bed_warnings(over, bed, whole=False))
