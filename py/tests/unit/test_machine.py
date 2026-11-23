"""Детали машин: каждая формула — на примере, ответ которого известен заранее."""

from __future__ import annotations

import math

import pytest

from datum.checks.machine import (beam_deflection, bearing_static, pin_joint,
                                  redundant_constraints, screw_drive)
from datum.geom import box, union
from datum.geom.measure import section_moments

KGF = 9.80665


def _by_tail(results):
    return {r.id.rsplit(".", 1)[-1]: r for r in results}


# ------------------------------------------------------------- ось в вилке
@pytest.mark.parametrize("fork, bending", [(8, 325), (10, 375)])
def test_pin_shear_passes_while_bending_breaks_it(fork, bending):
    """Разобранный пример: 1000 кгс, ось ⌀10, проушина 10, щёки 8…10.

    По срезу 62 МПа — «проходит» против [τ] 75. МКЭ показал 340 МПа в
    середине оси, и это не «сопромат врёт»: не посчитали изгиб, а он даёт
    ровно 325…375. Проверка обязана поймать его, а не только срез."""
    r = _by_tail(pin_joint(force=1000 * KGF, d=10, lug=10, fork=fork,
                           shear_allow=75, bearing_allow=150, bending_allow=220))
    assert r["срез"].status == "pass" and r["срез"].value == pytest.approx(62.4, abs=0.1)
    assert r["изгиб"].status == "fail" and r["изгиб"].value == pytest.approx(bending, rel=0.01)
    assert r["смятие"].value == pytest.approx(98.1, abs=0.1)        # F/(d·b) в проушине


def test_a_tight_pin_bends_less():
    loose = _by_tail(pin_joint(force=5000, d=8, lug=6, fork=4, gap=0.5, shear_allow=100,
                               bearing_allow=200, bending_allow=300))["изгиб"].value
    tight = _by_tail(pin_joint(force=5000, d=8, lug=6, fork=4, gap=0.5, fit="tight",
                               shear_allow=100, bearing_allow=200,
                               bending_allow=300))["изгиб"].value
    assert tight / loose == pytest.approx((6 + 2) / (8 + 6 + 2))


def test_bearing_picks_the_weaker_side():
    """Проушина печатная, щёки металлические — каждое против своего [σсм]."""
    r = _by_tail(pin_joint(force=2000, d=5, lug=10, fork=3, shear_allow=100,
                           bearing_allow=20, fork_bearing_allow=200, bending_allow=500))
    assert r["смятие"].status == "fail" and "проушине" in r["смятие"].message


# ------------------------------------------------------------- винт
def test_m10_efficiency_is_a_quarter():
    """М10×1,5, f = 0,13: ψ 3,03°, φ' 8,54°, КПД ≈ 26 %, самотормозит."""
    r = _by_tail(screw_drive(d=10, pitch=1.5, force=1000, friction=0.13))
    assert r["кпд"].value == pytest.approx(0.26, abs=0.005)
    assert "самотормозит" in r["кпд"].message and "НЕ самотормозит" not in r["кпд"].message


def test_torque_equals_work_over_efficiency():
    """T = F·Ph/(2π·η) — та же величина, что через тангенсы."""
    force, pitch = 2000.0, 4.0
    r = _by_tail(screw_drive(d=16, pitch=pitch, force=force, friction=0.1,
                             profile="trapezoid", max_torque=1e6, safety=1.0))
    eta = r["кпд"].value
    assert r["момент"].value == pytest.approx(force * pitch / (2 * math.pi * eta), rel=1e-9)


def test_multistart_screws_do_not_self_lock():
    r = _by_tail(screw_drive(d=8, pitch=2, starts=4, force=200, friction=0.13,
                             profile="trapezoid", self_locking=True))
    assert r["самоторможение"].status == "fail"
    assert "груз сам крутит винт" in r["кпд"].message


def test_not_enough_torque_fails():
    r = _by_tail(screw_drive(d=16, pitch=4, force=300 * KGF, friction=0.13,
                             profile="trapezoid", max_torque=5000))
    assert r["момент"].status == "fail" and "НЕ ХВАТАЕТ" in r["момент"].message


# ------------------------------------------------------------- балка
def test_section_moments_of_known_shapes():
    from shapely.geometry import Point
    from shapely.geometry import box as sbox

    a, cu, cv, iuu, ivv, iuv = section_moments(sbox(0, 0, 50, 5))
    assert (a, cu, cv) == pytest.approx((250, 25, 2.5))
    assert (iuu, ivv, iuv) == pytest.approx((5 * 50 ** 3 / 12, 50 * 5 ** 3 / 12, 0), abs=1e-6)
    tube = Point(0, 0).buffer(17.5, quad_segs=512).difference(Point(0, 0).buffer(15, quad_segs=512))
    assert section_moments(tube)[3] == pytest.approx(math.pi * (35 ** 4 - 30 ** 4) / 64, rel=1e-4)


def test_cantilever_strip_from_the_part_itself():
    """Полоса 50×5 плашмя, консоль 500, 1 кгс: F·L³/(3·E·I), I = b·h³/12."""
    strip = box(0, 500, -25, 25, 0, 5)
    expected = KGF * 500 ** 3 / (3 * 200000 * 50 * 5 ** 3 / 12)
    r = _by_tail(beam_deflection(strip, length=500, load=KGF, modulus=200000,
                                 support="cantilever", along="x", toward="-z",
                                 max_deflection=10, allow_stress=200))
    assert r["прогиб"].value == pytest.approx(expected, rel=1e-9)
    assert r["напряжение"].value == pytest.approx(KGF * 500 * 2.5 / (50 * 5 ** 3 / 12))


def test_the_same_mass_on_edge_is_far_stiffer():
    """Та же полоса на ребро — в (50/5)² = 100 раз жёстче: материал ушёл от
    нейтрального слоя. Направление силы решает, не только площадь."""
    strip = box(0, 500, -25, 25, 0, 5)
    kw = dict(length=500, load=KGF, modulus=200000, support="cantilever", along="x",
              max_deflection=100)
    flat = beam_deflection(strip, toward="-z", **kw)[0].value
    edge = beam_deflection(strip, toward="-y", **kw)[0].value
    assert flat / edge == pytest.approx(100, rel=1e-6)


def test_ribs_count_because_the_section_is_the_real_one():
    """Рёбра на плите — в моменте инерции, потому что сечение берётся из детали."""
    plate = box(0, 300, 0, 40, 0, 3)
    ribbed = union([plate, box(0, 300, 5, 7, 3, 13), box(0, 300, 33, 35, 3, 13)])
    kw = dict(length=300, load=50, modulus=2500, support="pinned", along="x",
              toward="-z", max_deflection=100)
    assert (beam_deflection(ribbed, **kw)[0].value
            < beam_deflection(plate, **kw)[0].value / 10)


def test_unknown_supports_give_a_fork():
    """Заделка и шарнир — в 4 раза по прогибу; порог внутри вилки — warn."""
    kw = dict(length=900, load=10 * KGF, modulus=200000,
              second_moment=math.pi * (25 ** 4 - 23 ** 4) / 64, fiber=12.5)
    fixed = beam_deflection(support="fixed", max_deflection=10, **kw)[0].value
    pinned = beam_deflection(support="pinned", max_deflection=10, **kw)[0].value
    assert pinned == pytest.approx(1.37, abs=0.01) and pinned / fixed == pytest.approx(4)
    assert beam_deflection(support="between", max_deflection=1.0, **kw)[0].status == "warn"
    assert beam_deflection(support="between", max_deflection=2.0, **kw)[0].status == "pass"
    assert beam_deflection(support="between", max_deflection=0.1, **kw)[0].status == "fail"


def test_an_asymmetric_section_is_named():
    angle = union([box(0, 400, 0, 20, 0, 3), box(0, 400, 0, 3, 0, 20)])
    msg = beam_deflection(angle, length=400, load=10, modulus=70000, along="x",
                          toward="-z", max_deflection=5)[0].message
    assert "НЕСИММЕТРИЧНО" in msg


@pytest.mark.parametrize("kw, words", [
    (dict(), "задайте max_deflection"),
    (dict(max_deflection=1), "нужно сечение"),
    (dict(max_deflection=1, second_moment=10, fiber=1, support="free"), "опоры"),
])
def test_beam_refuses_what_it_cannot_answer(kw, words):
    with pytest.raises(ValueError, match=words):
        beam_deflection(length=100, load=10, modulus=2000, **kw)


def test_axial_load_is_not_bending():
    with pytest.raises(ValueError, match="не изгиб"):
        beam_deflection(box(0, 100, 0, 5, 0, 5), length=100, load=10, modulus=2000,
                        along="x", toward="+x", max_deflection=1)


# ------------------------------------------------------------- подшипник
def test_a_small_bearing_is_stronger_than_it_looks():
    """6000 (10×26×8): C₀ ≈ 1,96 кН — 100 кгс держит с запасом 2."""
    r = _by_tail(bearing_static(c0=1960, radial=100 * KGF, min_s0=2.0))
    assert r["статика"].value == pytest.approx(2.0, abs=0.01)


def test_axial_force_limit_is_tighter_for_small_bearings():
    ok = _by_tail(bearing_static(c0=1960, radial=200, axial=800, bore=15))
    small = _by_tail(bearing_static(c0=1960, radial=200, axial=800, bore=10))
    assert ok["осевая"].status == "pass" and small["осевая"].status == "fail"
    assert small["осевая"].threshold == pytest.approx(0.25 * 1960)


def test_life_needs_an_equivalent_load_under_axial_force():
    with pytest.raises(ValueError, match="из каталога"):
        bearing_static(c0=1960, radial=200, axial=100, c=4750, rpm=1000, min_hours=1000)
    r = _by_tail(bearing_static(c0=1960, radial=500, c=4750, rpm=1000, min_hours=1000))
    assert r["ресурс"].value == pytest.approx((4750 / 500) ** 3 * 1e6 / 60000)


# ------------------------------------------------------------- лишние связи
def test_two_rods_two_bushings_have_three_redundancies():
    """Каретка на двух валах с двумя втулками: q = 1 − 6 + 4 + 4 = 3."""
    r = redundant_constraints(links=1, mobility=1, joints=[
        {"name": "втулка 1", "kind": "cylindrical"}, {"name": "втулка 2", "kind": "cylindrical"}])
    assert r.status == "fail" and r.value == 3


def test_one_base_and_a_roller_self_align():
    """Втулка на одном валу и ролик на втором: q = 0 — самоустанавливается."""
    r = redundant_constraints(links=1, mobility=1, joints=[
        {"name": "втулка", "kind": "cylindrical"}, {"name": "ролик", "kind": "point"}])
    assert r.status == "pass" and r.value == 0


def test_an_intentional_elastic_redundancy_is_allowed():
    """Второй ролик на упругой балке с поджимным винтом — лишняя связь намеренно."""
    joints = [{"name": "втулка", "kind": "cylindrical"},
              {"name": "ролик сверху", "kind": "point"}, {"name": "ролик снизу", "removes": 1}]
    assert redundant_constraints(links=1, mobility=1, joints=joints).status == "fail"
    assert redundant_constraints(links=1, mobility=1, joints=joints, allowed=1).status == "pass"


def test_too_few_constraints_are_named():
    r = redundant_constraints(links=1, mobility=1, joints=[{"name": "ролик", "kind": "point"}])
    assert r.status == "warn" and "подвижностей" in r.message


# ------------------------------------------------------------- из checks.yaml
def test_declared_in_a_project(tmp_path):
    """Сквозь проект: параметры с происхождением, три результата с хвостами id."""
    from datum.cli import main as cli
    from datum.doc.project import Project

    root = tmp_path / "шарнир"
    assert cli(["init", str(root)]) == 0
    (root / "params" / "base.yaml").write_text(
        (root / "params" / "base.yaml").read_text(encoding="utf-8") + """
LOAD:      {value: 300, unit: N, provenance: assumed, range: [200, 400], reason: "рука"}
TAU_ALLOW: {value: 75, unit: MPa, provenance: standard, ref: "Ст3, статика"}
BEAR_PLA:  {value: 20, unit: MPa, provenance: assumed, range: [15, 30], reason: "печатный PLA"}
BEND_ALLOW: {value: 160, unit: MPa, provenance: standard, ref: "Ст3"}
""", encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text("""
- id: шарнир.ось
  use: pin_joint
  args:
    force: "${params.LOAD}"
    d: 5
    lug: 6
    fork: 3
    shear_allow: "${params.TAU_ALLOW}"
    bearing_allow: "${params.BEAR_PLA}"
    bending_allow: "${params.BEND_ALLOW}"
""", encoding="utf-8")
    run = Project.open(root).run_checks()
    ids = {r.id: r for r in run.results}
    assert set(ids) == {"шарнир.ось.срез", "шарнир.ось.смятие", "шарнир.ось.изгиб"}
    assert "LOAD" in ids["шарнир.ось.смятие"].depends_on
    # Смятие в проушине 300/(5·6) = 10 МПа при [σсм] 20 — в допуске; изгиб
    # 300·(2·3 + 6)/8 = 450 Н·мм на W = π·5³/32 — 36.7 МПа.
    assert ids["шарнир.ось.смятие"].value == pytest.approx(10.0)
    assert ids["шарнир.ось.изгиб"].value == pytest.approx(450 / (math.pi * 125 / 32))
    assert run.ok


def test_whole_numbers_may_come_from_params_as_floats():
    """Параметры проекта — числа с плавающей точкой: 4.0 заходов — это 4."""
    a = _by_tail(screw_drive(d=8, pitch=2, starts=4.0, force=200, friction=0.13))["кпд"]
    b = _by_tail(screw_drive(d=8, pitch=2, starts=4, force=200, friction=0.13))["кпд"]
    assert a.value == b.value
    assert redundant_constraints(links=1.0, mobility=1.0, joints=[
        {"name": "в", "kind": "cylindrical"}, {"name": "р", "removes": 1.0}]).value == 0
    with pytest.raises(ValueError, match="целое"):
        screw_drive(d=8, pitch=2, starts=2.5, force=200, friction=0.13)
