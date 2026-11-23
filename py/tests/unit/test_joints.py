"""Шарниры: объявление в assembly.yaml и выведенная из него проверка хода.

Против точного ответа: крышка 40×40×2 на петле по кромке и стойка за петлёй.
Первым стойки касается дальний верхний угол крышки, и угол касания решается
уравнением 40·cos θ − 2·sin θ = −5 — проверка обязана пройти на ходе до него и
упасть на ходе за него, назвав стойку.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.optimize import brentq

from datum.checks.motion import JOINT_KINDS as CHECK_KINDS
from datum.checks.motion import joint_matrix, joint_sweep
from datum.doc.assembly import JOINT_KINDS, Assembly, AssemblyError
from datum.geom import box
from datum.geom.measure import inter_vol

HINGE_Z = 20.2
#: Угол, при котором верхний дальний угол крышки доходит до стойки (x = −5).
CONTACT = math.degrees(brentq(lambda t: 40 * math.cos(t) - 2 * math.sin(t) + 5, 1.5, 2.5))


def _scene():
    base = box(0, 40, 0, 40, 0, 20).renamed("коробка")
    lid = box(0, 40, 0, 40, HINGE_Z, HINGE_Z + 2).renamed("крышка")
    post = box(-10, -5, 0, 40, 20, 80).renamed("стойка")
    return base, lid, post


# ------------------------------------------------------------------ движение
def test_the_rotation_follows_the_right_hand_rule_about_its_own_point():
    m = joint_matrix("revolute", "+Z", (1, 1, 0), 90)
    assert m @ np.array([2, 1, 0, 1]) == pytest.approx([1, 2, 0, 1])     # +X → +Y
    m = joint_matrix("revolute", (0, 0, 2), (0, 0, 0), 90)               # ось вектором
    assert m @ np.array([1, 0, 5, 1]) == pytest.approx([0, 1, 5, 1])
    assert joint_matrix("prismatic", "-Y", (0, 0, 0), 7)[:3, 3] == pytest.approx([0, -7, 0])


def test_a_lid_swings_free_until_the_post_and_the_check_names_the_post():
    base, lid, post = _scene()
    assert 93 < CONTACT < 96
    free = joint_sweep(lid, [base, post], kind="revolute", axis="-Y",
                       origin=(0, 0, HINGE_Z), limits=(0, CONTACT - 1.0))
    assert free.status == "pass", free.message
    hit = joint_sweep(lid, [base, post], kind="revolute", axis="-Y",
                      origin=(0, 0, HINGE_Z), limits=(0, CONTACT + 1.0))
    assert hit.status == "fail"
    assert "Мешает: стойка" in hit.message and "коробка" not in hit.message.split("Мешает")[1]
    assert "°" in hit.message
    worst = hit.evidence[0].data["worst_u"]
    assert worst == pytest.approx(CONTACT + 1.0)       # хуже всего — в конце хода
    # Окну — положение для кнопки «к худшему»: оно уходит в отчёт целиком.
    pose = hit.as_dict()["evidence"][-1]
    assert pose["kind"] == "pose" and pose["data"] == {"part": "крышка", "u": pytest.approx(worst)}
    assert all("data" not in e for e in free.as_dict()["evidence"])


def test_a_short_swing_through_a_thin_stop_is_not_skipped():
    """Тонкая планка 0.6 мм поперёк пути дальней кромки: шаг по пути самой
    быстрой точки (0.25 мм), а не по углу, — и планка не проскакивает."""
    base, lid, _ = _scene()
    t = math.radians(30)
    # Планка на дуге дальнего нижнего угла крышки (r = 40) при 30°.
    x, z = 40 * math.cos(t), HINGE_Z + 40 * math.sin(t)
    plank = box(x - 0.3, x + 0.3, 0, 40, z - 0.3, z + 0.3).renamed("планка")
    res = joint_sweep(lid, [base, plank], kind="revolute", axis="-Y",
                      origin=(0, 0, HINGE_Z), limits=(0, 90), max_inter_vol=0.01)
    assert res.status == "fail" and "планка" in res.message


def test_a_slider_travels_along_its_axis():
    rail = box(0, 100, 0, 10, 0, 2).renamed("направляющая")
    stop = box(60, 62, 0, 10, 2, 10).renamed("упор")
    slider = box(0, 10, 0, 10, 2, 8).renamed("ползун")
    ok = joint_sweep(slider, [rail, stop], kind="prismatic", axis="+X", limits=(0, 49.9))
    assert ok.status == "pass" and "мм" in ok.message
    bad = joint_sweep(slider, [rail, stop], kind="prismatic", axis="+X", limits=(0, 55))
    assert bad.status == "fail" and "упор" in bad.message
    assert inter_vol(slider, rail) == 0


# ------------------------------------------------------------------ объявление
def _asm(**joints) -> Assembly:
    return Assembly.from_dict({"order": ["коробка", "крышка", "стойка"], "joints": joints})


def test_a_declared_joint_becomes_a_blocking_check_against_the_other_parts():
    a = _asm(крышка={"kind": "revolute", "axis": "-y", "origin": [0, 0, 20.2],
                     "range": [0, 100]})
    decl = [d for d in a.check_decls() if d["use"] == "joint_sweep"][0]
    assert decl["id"] == "крышка.шарнир" and decl["severity"] == "blocking"
    assert decl["args"]["others"] == ["@коробка", "@стойка"], "все ДРУГИЕ детали, и после неё"
    assert decl["args"]["axis"] == "-Y" and decl["args"]["limits"] == [0.0, 100.0]
    only = _asm(крышка={"kind": "prismatic", "axis": [1, 0, 0], "range": [0, 5],
                        "against": "стойка"}).check_decls()
    joint = [d for d in only if d["use"] == "joint_sweep"][0]
    assert joint["id"] == "крышка.ход" and joint["args"]["others"] == ["@стойка"]


@pytest.mark.parametrize("spec, words", [
    ({"axis": "+Y", "origin": [0, 0, 0], "range": [0, 90]}, "нужен kind"),
    ({"kind": "hinge", "axis": "+Y", "origin": [0, 0, 0], "range": [0, 90]}, "вероятно, revolute"),
    ({"kind": "revolute", "axis": "+Y", "range": [0, 90]}, "нужен origin"),
    ({"kind": "revolute", "axis": "+W", "origin": [0, 0, 0], "range": [0, 90]}, "ось — одно из"),
    ({"kind": "revolute", "axis": [0, 0, 0], "origin": [0, 0, 0], "range": [0, 90]}, "нулевым"),
    ({"kind": "revolute", "axis": "+Y", "origin": [0, 0], "range": [0, 90]}, "три числа"),
    ({"kind": "revolute", "axis": "+Y", "origin": [0, 0, 0], "range": [90, 0]}, "больше начала"),
    ({"kind": "revolute", "axis": "+Y", "origin": [0, 0, 0], "range": [0, 400]}, "оборота"),
    ({"kind": "prismatic", "axis": "+Y", "origin": [0, 0, 0], "range": [0, 9]}, "ни на что не влияет"),
    ({"kind": "revolute", "axis": "+Y", "origin": [0, 0, 0], "limits": [0, 9]}, "range: \\[от, до\\]"),
    ({"kind": "revolute", "axis": "+Y", "origin": [0, 0, 0], "range": [0, 9],
      "against": ["крышка"]}, "сама себе"),
    ({"kind": "revolute", "axis": "+Y", "origin": [0, 0, 0], "range": [0, 9],
      "against": ["стойко"]}, "'стойко' → 'стойка'"),
])
def test_a_wrong_joint_is_refused_with_a_hint(spec, words):
    with pytest.raises(AssemblyError, match=words):
        _asm(крышка=spec)


def test_a_joint_outside_the_order_and_a_misspelt_section_are_refused():
    with pytest.raises(AssemblyError, match="нет в order.*'крышко' → 'крышка'"):
        _asm(крышко={"kind": "revolute", "axis": "+Y", "origin": [0, 0, 0], "range": [0, 9]})
    with pytest.raises(AssemblyError, match="'joint' → 'joints'"):
        Assembly.from_dict({"order": ["a"], "joint": {}})


def test_the_kind_list_matches_the_one_the_check_understands():
    assert JOINT_KINDS == CHECK_KINDS


# ------------------------------------------------------------------ в проекте
PART = '''from datum.build import part
from datum.geom import box


@part("{name}", print_up="+Z")
def build():
    return box({box})
'''


def test_a_hinged_lid_in_a_project_is_checked_and_a_hand_declared_check_overrides(tmp_path):
    from datum.doc.project import Project
    from datum.doc.scaffold import init_project

    root = tmp_path / "петля"
    init_project(root)
    for name, b in (("коробка", "0, 40, 0, 40, 0, 20"), ("крышка", "0, 40, 0, 40, 20.2, 22.2"),
                    ("стойка", "-10, -5, 0, 40, 20, 80")):
        (root / "parts" / f"{name}.py").write_text(PART.format(name=name, box=b),
                                                   encoding="utf-8")
    joint = f"{{kind: revolute, axis: '-Y', origin: [0, 0, 20.2], range: [0, {CONTACT + 2:.2f}]}}"
    (root / "assembly.yaml").write_text(
        "order: [коробка, крышка, стойка]\n"
        "parts:\n  крышка: {insert: '-Z'}\n  стойка: {insert: '-Z'}\n"
        f"joints:\n  крышка: {joint}\n", encoding="utf-8")
    run = Project.open(root).run_checks()
    by_id = {r.id: r for r in run.results}
    assert by_id["крышка.шарнир"].status == "fail", by_id["крышка.шарнир"].message
    assert "стойка" in by_id["крышка.шарнир"].message
    assert by_id["крышка.шарнир"].severity == "blocking"

    # Ход до касания — проверка зелёная.
    (root / "assembly.yaml").write_text(
        (root / "assembly.yaml").read_text(encoding="utf-8").replace(
            f"{CONTACT + 2:.2f}", f"{CONTACT - 2:.2f}"), encoding="utf-8")
    run = Project.open(root).run_checks()
    assert {r.id: r for r in run.results}["крышка.шарнир"].status == "pass"

    # Своя проверка с тем же id перебивает выведенную: здесь — без стойки.
    with (root / "checks" / "checks.yaml").open("a", encoding="utf-8") as f:
        f.write("- id: крышка.шарнир\n  use: joint_sweep\n  args:\n"
                "    part: \"@крышка\"\n    others: [\"@коробка\"]\n    kind: revolute\n"
                "    axis: \"-Y\"\n    origin: [0, 0, 20.2]\n    limits: [0, 180]\n")
    pr = Project.open(root)
    assert [d for d in pr.derived_decls() if d["id"] == "крышка.шарнир"] == []
    mine = {r.id: r for r in pr.run_checks().results}["крышка.шарнир"]
    assert mine.status == "pass" and "0…180°" in mine.message



def test_the_window_gets_the_joints_with_the_axis_as_a_vector(tmp_path):
    """Окну — ось вектором (имя направления окно не разбирает), пределы и id
    проверки хода: по нему окно найдёт худшее положение."""
    from datum.doc.project import Project
    from datum.doc.scaffold import init_project

    root = tmp_path / "петля"
    init_project(root)
    for name, b in (("коробка", "0, 40, 0, 40, 0, 20"), ("крышка", "0, 40, 0, 40, 20.2, 22.2")):
        (root / "parts" / f"{name}.py").write_text(PART.format(name=name, box=b),
                                                   encoding="utf-8")
    (root / "assembly.yaml").write_text(
        "order: [коробка, крышка]\njoints:\n"
        "  крышка: {kind: revolute, axis: '-Y', origin: [0, 0, 20.2], range: [2, 105]}\n",
        encoding="utf-8")
    joints = Project.open(root).summary()["assembly"]["joints"]
    assert joints == {"крышка": {"kind": "revolute", "axis": [0.0, -1.0, 0.0],
                                 "origin": [0.0, 0.0, 20.2], "range": [2.0, 105.0],
                                 "check": "крышка.шарнир"}}
