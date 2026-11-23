"""Симуляция механизмов (MuJoCo): физика по формулам, проверки, проект, операции.

Каждый ответ здесь сверяется с тем, что известно без MuJoCo: m·g·l маятника,
τ = I·α диска, статика двухзвенника, центр давления по рычагу. Ловушка,
пойманная этими тестами: MjSpec по умолчанию читает углы в ГРАДУСАХ — ход ±2π
рад становился ±6.28°, и упор давал «момент» 3.4 Н·м вместо 0.00025.
"""

from __future__ import annotations

import asyncio
import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from datum.fab.settings import FALLBACK, resolve
from datum.geom import box, cyl_z
from datum.sim import Mechanism, MechanismError, drive, revolute
from datum.sim.compile import compile_mechanism
from datum.sim.run import run_scenario

G = 9.81
ROOT = Path(__file__).resolve().parents[3]


def _solid(_name):
    s = resolve({**FALLBACK, "flow": 1.0, "density_g_cm3": 1.0}, {k: "тест" for k in FALLBACK},
                layer=0.2, part={"infill": 1.0})
    return s, "+Z"


def _run(m: Mechanism, scenario: str, tmp_path, **kw):
    comp = compile_mechanism(m, part_settings=_solid, cache_dir=tmp_path)
    return comp, run_scenario(comp, scenario, geometry=kw.pop("geometry", False), **kw)


def _pendulum(limit=5.0, load_g=300.0):
    m = Mechanism(base="clamped")
    m.link("стойка").add(box(-5, 5, -5, 5, -10, 0), mass_g=50)
    arm = m.link("рычаг", parent="стойка",
                 joint=revolute("+Y", [0, 0, 0], [-180, 180], drive=drive(limit, 60)))
    arm.add(box(195, 205, -5, 5, -5, 5), mass_g=load_g)
    return m


# -------------------------------------------------------------- физика
def test_a_pendulum_needs_m_g_l_and_is_held_without_droop(tmp_path):
    m = _pendulum()
    m.scenario("держать", [{"рычаг": 0}], hold_s=0.5)
    _, r = _run(m, "держать", tmp_path)
    assert np.abs(r.required[:, 0]).max() == pytest.approx(0.3 * G * 0.2, rel=1e-6)
    assert np.abs(r.tracking).max() < 0.01           # упреждение держит без просадки


def test_a_disc_spins_up_with_i_alpha_and_angles_are_radians(tmp_path):
    """Разгон диска вокруг оси: τ/α = ½·m·r². Здесь же ловушка градусов: на 45°
    упор сустава, прочитанный в градусах, давал момент в 14 000 раз больше."""
    m = Mechanism(base="clamped")
    m.link("стол").add(box(-5, 5, -5, 5, -10, -5), mass_g=10)
    m.link("диск", parent="стол", joint=revolute("+Z", [0, 0, 0], [-360, 360],
                                                drive=drive(1.0, 90, accel=180))).add(
        cyl_z(100, 0, 10), part="диск")
    m.scenario("поворот", [{"диск": 0}, {"диск": 90}], hold_s=0.1)
    comp, r = _run(m, "поворот", tmp_path)
    i_disc = comp.links["диск"].inertia_kgm2[2, 2]
    assert i_disc == pytest.approx(0.5 * comp.links["диск"].mass_kg * 0.05 ** 2, rel=0.002)
    alpha = np.radians(r.plan.qdd[:, 0])
    k = int(np.argmax(np.abs(alpha)))
    assert r.required[k, 0] / alpha[k] == pytest.approx(i_disc, rel=1e-6)


def test_two_links_hold_their_textbook_static_torques(tmp_path):
    m = Mechanism(base="clamped")
    m.link("база").add(box(-5, 5, -5, 5, -10, 0), mass_g=50)
    m.link("плечо", parent="база", joint=revolute("-Y", [0, 0, 0], [-90, 90], drive=drive(10, 60))
           ).add(box(70, 80, -5, 5, -5, 5), mass_g=200)
    m.link("локоть", parent="плечо", joint=revolute("-Y", [150, 0, 0], [-150, 150],
                                                    drive=drive(10, 60))
           ).add(box(195, 205, -5, 5, -5, 5), mass_g=100)
    m.payload("груз", link="локоть", mass_g=300, at=[250, 0, 0])
    m.scenario("горизонт", [{"плечо": 0, "локоть": 0}], hold_s=0.2, payloads=["груз"])
    _, r = _run(m, "горизонт", tmp_path)
    assert abs(r.required[0, 0]) == pytest.approx(G * (0.2 * .075 + 0.1 * .2 + 0.3 * .25), rel=1e-6)
    assert abs(r.required[0, 1]) == pytest.approx(G * (0.1 * .05 + 0.3 * .1), rel=1e-6)


def test_a_resting_base_has_its_centre_of_pressure_where_the_lever_rule_puts_it(tmp_path):
    m = Mechanism(base="resting")
    m.link("база").add(box(-50, 50, -50, 50, 0, 20), mass_g=1000)
    m.link("рычаг", parent="база", joint=revolute("+Z", [0, 0, 20], [-180, 180],
                                                  drive=drive(5, 60))
           ).add(box(195, 205, -5, 5, 20, 30), mass_g=300)
    m.scenario("стоять", [{"рычаг": 0}], hold_s=0.2)
    _, r = _run(m, "стоять", tmp_path)
    x = 0.3 * 200 / 1.3
    assert r.cop[0] == pytest.approx([x, 0.0], abs=1e-6)
    assert r.tip_margin_mm[0] == pytest.approx(50 - x, abs=1e-6)


def test_a_weak_drive_falls_behind_and_says_so(tmp_path):
    """Привод слабее тяжести рычага: упреждение срезано пределом — звено
    отстаёт и падает. Отставание — следствие нехватки, а не настройки."""
    from datum.checks.sim import sim_drive, sim_tracking

    m = _pendulum(limit=0.3)                         # нужно 0.59 Н·м
    m.scenario("держать", [{"рычаг": 0}, {"рычаг": 20}], hold_s=0.5)
    comp, r = _run(m, "держать", tmp_path)
    (drv,) = sim_drive((comp, r))
    (trk,) = sim_tracking((comp, r))
    assert drv.status == "fail" and "НЕ ХВАТИТ" in drv.message
    assert trk.status == "fail" and "НЕ УСПЕВАЕТ" in trk.message
    assert np.mean(np.abs(r.applied[:, 0]) >= 0.2999) > 0.5     # большую часть времени на пределе


def test_clearance_finds_a_link_swinging_into_its_post(tmp_path):
    from datum.checks.sim import sim_clearance

    m = Mechanism(base="clamped")
    m.link("стойка").add(box(-10, 10, -10, 10, -40, -6), mass_g=100)
    # хвост рычага за осью: при подъёме конца он опускается в стойку
    m.link("рычаг", parent="стойка", joint=revolute("-Y", [0, 0, 0], [-10, 90], drive=drive(5, 60))
           ).add(box(-15, 100, -5, 5, -5, 5), mass_g=50)
    m.scenario("подъём", [{"рычаг": 0}, {"рычаг": 60}], hold_s=0.1)
    comp, r = _run(m, "подъём", tmp_path, geometry=True)
    res = sim_clearance((comp, r))
    assert res.status == "fail" and "рычаг" in res.message and "стойка" in res.message


# -------------------------------------------------------------- объявление
@pytest.mark.parametrize("build, words", [
    (lambda m: m.link("a").add(box(0, 1, 0, 1, 0, 1)), "масса не указана"),
    (lambda m: (m.link("a"), m.link("b")), "корень уже есть"),
    (lambda m: m.link("b", parent="нет"), "ещё не объявлен"),
    (lambda m: revolute("+Q", [0, 0, 0], [0, 1]), "направление"),
    (lambda m: revolute("+Z", [0, 0, 0], [5, 1]), "конец больше начала"),
    (lambda m: drive(-1, 10), "больше нуля"),
])
def test_a_mechanism_that_cannot_be_computed_is_refused_in_words(build, words):
    with pytest.raises(MechanismError, match=words):
        build(Mechanism())


def test_a_scenario_pose_outside_the_joint_range_is_refused():
    m = _pendulum()
    with pytest.raises(MechanismError, match="вне хода"):
        m.scenario("x", [{"рычаг": 500}])


def test_only_the_model_module_imports_mujoco():
    import ast

    pkg = ROOT / "py" / "datum"
    offenders = []
    for path in pkg.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        if "mujoco" in names and path != pkg / "sim" / "model.py":
            offenders.append(str(path.relative_to(pkg)))
    assert not offenders, f"mujoco импортирует не sim/model.py: {offenders}"


# -------------------------------------------------------------- проект
@pytest.fixture
def arm(tmp_path):
    root = tmp_path / "arm"
    shutil.copytree(ROOT / "examples" / "arm", root,
                    ignore=shutil.ignore_patterns(".datum", "output", "snapshots"))
    return root


def test_the_arm_example_passes_and_depends_on_what_it_reads(arm):
    from datum.doc.project import Project

    run = Project.open(arm).run_checks()
    assert run.ok, run.report()
    by_id = {r.id: r for r in run.results}
    drv = by_id["рука.приводы.плечо"]
    assert {"SERVO_T", "L1", "L2", "LOAD_G", "SERVO_G"} <= set(drv.depends_on)
    assert by_id["рука.основание.опрокидывание"].value > 5.0


def test_without_ballast_the_arm_tips_over(arm):
    """Ровно то, что записано в параметре BALLAST_G: без шайб рука с грузом
    опрокидывается."""
    from datum.doc.project import Project

    params = arm / "params" / "base.yaml"
    text = params.read_text(encoding="utf-8")
    params.write_text(text.replace("value: 250.0", "value: 1.0"), encoding="utf-8")
    run = Project.open(arm).run_checks()
    tip = {r.id: r for r in run.results}["рука.основание.опрокидывание"]
    assert tip.status == "fail" and "ОПРОКИНЕТСЯ" in tip.message


def test_sim_checks_name_a_declared_mechanism(arm):
    from datum.doc.project import Project, ProjectError

    (arm / "checks" / "checks.yaml").write_text(
        "- id: x\n  use: sim_drive\n  args: {mechanism: нет, scenario: перенос}\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="нет механизма"):
        Project.open(arm)


def test_the_ops_list_run_and_weigh(arm):
    from datum.tools.ops import clear_project_cache
    from datum.tools.ops_sim import part_mass, sim_list, sim_run

    clear_project_cache()
    lst = asyncio.run(sim_list(project=str(arm)))
    (mech,) = lst["mechanisms"]
    assert mech["name"] == "рука" and [lk["name"] for lk in mech["links"]] == \
        ["основание", "плечо", "предплечье"]
    assert any("покупное" in p["source"] for p in mech["links"][1]["pieces"])
    out = asyncio.run(sim_run(project=str(arm), mechanism="рука", scenario="перенос"))
    assert out["ok"] and not out.get("error"), out.get("report")
    assert (arm / out["images"]["filmstrip"]["path"]).is_file()
    assert out["events"][0]["t"] == "sim.result" and out["events"][0]["url"].endswith(".json")
    record = arm / ".datum" / "cache" / "objects"
    assert any(record.rglob("*.json"))
    mass = asyncio.run(part_mass(project=str(arm)))
    assert mass["parts"]["плечо"]["slicer_overrides"] == {"wall_loops": "3",
                                                          "sparse_infill_density": "15%"}
    assert mass["parts"]["основание"]["slicer_overrides"] == {"wall_loops": "3",
                                                              "sparse_infill_density": "20%"}


def test_the_playback_record_has_links_with_meshes(arm):
    """Окно отказывает записи «не той формы»: кадры несли свой ключ `links`
    (имена) и затирали звенья с сетками — поймано живым сценарием окна."""
    import json

    from datum.tools.ops import clear_project_cache
    from datum.tools.ops_sim import sim_run

    clear_project_cache()
    out = asyncio.run(sim_run(project=str(arm), mechanism="рука", scenario="перенос",
                              geometry=False))
    digest = out["playback"]["url"].split("/")[-1].split(".")[0]
    raw = json.loads(next((arm / ".datum").rglob(f"{digest}.json")).read_text(encoding="utf-8"))
    assert all(isinstance(lk, dict) and lk["url"].startswith("/obj/") for lk in raw["links"])
    assert len(raw["poses"]) == len(raw["t"]) and len(raw["poses"][0]) == len(raw["links"])
    assert math.isclose(raw["t"][-1], out["duration_s"], rel_tol=0.01)


def test_a_coupled_chain_with_ample_torque_never_hits_the_limit(tmp_path):
    """Своя жёсткость каждому суставу по диагонали матрицы масс раскачивала
    связанную цепь: на руке v4 плечо и локоть с первого шага дрались ±предел
    при ошибке в сотые градуса. С полной матрицей (вычисляемый момент) при
    хватающем моменте привод в предел не упирается."""
    m = Mechanism(base="clamped")
    m.link("база").add(box(-20, 20, -20, 20, -30, 0), mass_g=500)
    m.link("плечо", parent="база", joint=revolute("+Y", [0, 0, 0], [-120, 120], drive=drive(4.5, 66))
           ).add(box(0, 160, -15, 15, -15, 15), mass_g=450)
    m.link("локоть", parent="плечо", joint=revolute("+Y", [160, 0, 0], [-150, 150], drive=drive(4.5, 66))
           ).add(box(160, 320, -10, 10, -10, 10), mass_g=130)
    m.link("кисть", parent="локоть", joint=revolute("+Y", [320, 0, 0], [-120, 120], drive=drive(0.98, 120))
           ).add(box(320, 340, -8, 8, -8, 8), mass_g=40)
    m.payload("груз", link="кисть", mass_g=300, at=[400, 0, 0])
    m.scenario("подъём", [{"плечо": 0}, {"плечо": -60}, {"плечо": 0}], payloads=["груз"])
    comp, r = _run(m, "подъём", tmp_path)
    lim = np.array([4.5, 4.5, 0.98])
    need = np.abs(r.required).max(0)
    assert np.all(need < lim), need                       # момента хватает
    assert not np.any(np.abs(r.applied) >= 0.999 * lim), "привод упёрся в предел"
    assert np.abs(r.tracking).max() < 0.5


def _geared(ratio=0.5, limit=1.0):
    """Ведущий вал (вокруг Z, тяжести не чувствует) и ведомый рычаг с грузом
    300 г на 200 мм (вокруг Y), связанные передачей «ведомый = ratio × ведущий»."""
    m = Mechanism(base="clamped")
    m.link("база").add(box(-20, 20, -20, 20, -30, -20), mass_g=500)
    m.link("вал", parent="база", joint=revolute("+Z", [0, 0, 0], [-720, 720], drive=drive(limit, 60))
           ).add(cyl_z(10, -20, 0), mass_g=20)
    m.link("рычаг", parent="база", joint=revolute("-Y", [0, 0, 50], [-360, 360], follows=("вал", ratio))
           ).add(box(195, 205, -5, 5, 45, 55), mass_g=300)
    return m


def test_a_geared_follower_loads_its_leader_by_the_ratio(tmp_path):
    """Возможная работа идеальной передачи: ведущий держит ratio × момент
    тяжести ведомого. Без приведения ведущий «не чувствовал» бы рычаг вовсе."""
    m = _geared(ratio=0.5)
    m.scenario("держать", [{"вал": 0}], hold_s=0.3)
    _, r = _run(m, "держать", tmp_path)
    assert r.plan.drivers == ["вал"] and r.required.shape[1] == 1
    assert abs(r.required[0, 0]) == pytest.approx(0.5 * 0.3 * G * 0.2, rel=1e-6)


def test_a_geared_follower_moves_with_its_leader_in_the_forward_run(tmp_path):
    m = _geared(ratio=-1.5, limit=2.0)
    m.scenario("поворот", [{"вал": 0}, {"вал": 40}], hold_s=0.3)
    comp, r = _run(m, "поворот", tmp_path)
    k = r.plan.joints.index("рычаг")
    assert r.plan.q[-1, k] == pytest.approx(-60.0)             # ведомый = −1.5 × 40°
    assert np.abs(r.tracking).max() < 0.5                       # ведущий идёт по плану
    assert not np.any(np.abs(r.applied) >= 0.999 * 2.0)


@pytest.mark.parametrize("build, words", [
    (lambda m: m.link("б", parent="а", joint=revolute("+Z", [0, 0, 0], [0, 1], follows=("нет", 1))),
     "нет сустава"),
    (lambda m: revolute("+Z", [0, 0, 0], [0, 1], follows=("а", 0)), "отношение 0"),
    (lambda m: revolute("+Z", [0, 0, 0], [0, 1], drive=drive(1, 1), follows=("а", 1)),
     "своего привода"),
])
def test_a_follower_that_cannot_work_is_refused(build, words):
    m = Mechanism()
    m.link("а").add(box(0, 1, 0, 1, 0, 1), mass_g=1)
    with pytest.raises(MechanismError, match=words):
        build(m)


def test_a_scenario_cannot_set_a_follower():
    m = _geared()
    with pytest.raises(MechanismError, match="ведомый"):
        m.scenario("x", [{"рычаг": 10}])


def test_the_reduced_forward_run_agrees_with_mujoco_stepping(tmp_path):
    """Прогон в независимых координатах (для ведомых суставов) при единичной
    связи обязан повторять шаги MuJoCo — на слабом приводе, где есть что сравнивать."""
    from datum.sim.model import build_model
    from datum.sim.run import _forward, _forward_reduced, _inverse, plan_motion

    m = _pendulum(limit=0.45)                        # нужно 0.59: отстаёт и проседает
    m.scenario("подъём", [{"рычаг": 0}, {"рычаг": 30}], hold_s=0.2)
    comp = compile_mechanism(m, part_settings=_solid, cache_dir=tmp_path)
    plan = plan_motion(comp, m.scenarios["подъём"])
    model, joints = build_model(comp)
    ff, _, _ = _inverse(model, comp, plan, joints, free=False)
    a, _ = _forward(model, comp, plan, joints, ff)
    b, _ = _forward_reduced(model, comp, plan, joints, ff)
    assert np.abs(a).max() > 5.0                     # отставание настоящее
    assert np.abs(a - b).max() < 0.05 * np.abs(a).max()


# ------------------------------------------------- трение, КПД, ротор, люфт
def _lever(drv, *, friction=None, cap=None, payload=False):
    """Рычаг 300 г на 200 мм вокруг горизонтальной оси, 0° — горизонтально."""
    from datum.sim import revolute as rv

    m = Mechanism(base="clamped")
    m.link("стойка").add(box(-5, 5, -5, 5, -10, 0), mass_g=50)
    arm = m.link("рычаг", parent="стойка", joint=rv("-Y", [0, 0, 0], [-180, 180], drive=drv,
                                                     friction=friction, capacity=cap))
    arm.add(box(195, 205, -5, 5, -5, 5), mass_g=300)
    if payload:
        m.payload("груз", link="рычаг", mass_g=1, at=[200, 0, 0])
    return m


MGL = 0.3 * G * 0.2


def test_bearing_friction_helps_holding_and_opposes_motion(tmp_path):
    from datum.sim import bearing

    m = _lever(drive(5, 30), friction=bearing(mu=0.1, radius_mm=10))
    m.scenario("подъём", [{"рычаг": 0}, {"рычаг": 40}], hold_s=0.3)
    _, r = _run(m, "подъём", tmp_path)
    f0 = 0.1 * 0.3 * G * 0.010                         # μ·m·g·r в покое
    assert abs(r.required[0, 0]) == pytest.approx(MGL - f0, rel=1e-6)
    moving = np.abs(r.plan.qd[:, 0]) > 1e-9
    k = int(np.argmax(moving))                          # первый отсчёт движения
    fric = 0.1 * r.reactions[k, 0, 0] * 0.010
    assert r.required[k, 0] - r.required_rigid[k, 0] == pytest.approx(fric, rel=1e-6)


def test_a_self_locking_worm_holds_by_itself_and_pushes_to_lower(tmp_path):
    m = _lever(drive(5, 30, efficiency=0.3, back_efficiency=-0.5))
    m.scenario("вверх-вниз", [{"рычаг": 0}, {"рычаг": 30}, {"рычаг": 0}], hold_s=0.3)
    _, r = _run(m, "вверх-вниз", tmp_path)
    still = np.abs(r.plan.qd[:, 0]) < 1e-9
    assert np.all(r.required[still, 0] == 0.0)          # стоит — держит червяк
    up = r.plan.qd[:, 0] > 1e-9
    down = r.plan.qd[:, 0] < -1e-9
    assert np.allclose(np.abs(r.required[up, 0]), np.abs(r.required_rigid[up, 0]))
    assert np.allclose(np.abs(r.required[down, 0]), np.abs(r.required_rigid[down, 0]) * 0.5 * 0.3)


def test_a_backdrivable_gear_holds_with_its_back_efficiency(tmp_path):
    m = _lever(drive(5, 30, efficiency=0.8, back_efficiency=0.8))
    m.scenario("держать", [{"рычаг": 0}], hold_s=0.3)
    _, r = _run(m, "держать", tmp_path)
    assert abs(r.required[0, 0]) == pytest.approx(MGL * 0.64, rel=1e-6)


def test_rotor_inertia_adds_to_spin_up(tmp_path):
    m = Mechanism(base="clamped")
    m.link("стол").add(box(-5, 5, -5, 5, -10, -5), mass_g=10)
    m.link("диск", parent="стол", joint=revolute("+Z", [0, 0, 0], [-360, 360],
                                                drive=drive(1.0, 90, accel=180, rotor_inertia=2e-4))
           ).add(cyl_z(100, 0, 10), part="диск")
    m.scenario("поворот", [{"диск": 0}, {"диск": 90}], hold_s=0.1)
    comp, r = _run(m, "поворот", tmp_path)
    alpha = np.radians(r.plan.qdd[:, 0])
    k = int(np.argmax(np.abs(alpha)))
    i_disc = comp.links["диск"].inertia_kgm2[2, 2]
    assert r.required[k, 0] / alpha[k] == pytest.approx(i_disc + 2e-4, rel=1e-6)


def test_backlash_reports_the_reversal_and_the_lost_motion(tmp_path):
    """Рычаг проходит вертикаль: момент тяжести меняет знак — зазор перескакивается;
    потерянный ход в точке груза на 200 мм при люфте 1° — 200·π/180 мм."""
    m = _lever(drive(5, 30, backlash=1.0), payload=True)
    m.scenario("через верх", [{"рычаг": 60}, {"рычаг": 120}], hold_s=0.2, payloads=["груз"])
    _, r = _run(m, "через верх", tmp_path)
    times = [e["t"] for e in r.backlash["reversals"]]
    assert len(times) == 1
    # Ноль момента — не на вертикали: на ходу к тяжести m·g·l·cos q добавлен разгон I·α.
    tau = r.required[:, 0]
    zero = int(np.flatnonzero(np.sign(tau[1:]) != np.sign(tau[:-1]))[0]) + 1
    assert times[0] == pytest.approx(r.plan.t[zero], abs=0.02)
    i_arm = 0.3 * 0.2 ** 2
    k = zero
    resid = MGL * math.cos(math.radians(r.plan.q[k, 0])) + i_arm * math.radians(r.plan.qdd[k, 0])
    assert abs(resid) < 0.02 * MGL
    assert r.backlash["lost_motion_mm"]["груз"] == pytest.approx(200 * math.pi / 180, rel=1e-3)


def test_utilization_is_load_over_limit(tmp_path):
    from datum.sim import capacity

    m = _lever(drive(5, 30), cap=capacity(radial=10.0))
    m.scenario("держать", [{"рычаг": 0}], hold_s=0.2)
    _, r = _run(m, "держать", tmp_path)
    u = r.utilization["рычаг"][0]
    assert u == pytest.approx(max(MGL / 5, 0.3 * G / 10.0), rel=1e-6)   # опора 2.94 из 10 Н
    assert r.utilization["стойка"][0] == 0.0                           # закреплено


def test_a_follower_loads_its_leader_through_mesh_efficiency(tmp_path):
    """Ведомый поглощает мощность — ведущий даёт c/η зацепления."""
    from datum.sim import revolute as rv

    m = Mechanism(base="clamped")
    m.link("база").add(box(-20, 20, -20, 20, -30, -20), mass_g=500)
    m.link("вал", parent="база", joint=rv("+Z", [0, 0, 0], [-720, 720], drive=drive(1.0, 60))
           ).add(cyl_z(10, -20, 0), mass_g=20)
    m.link("рычаг", parent="база", joint=rv("-Y", [0, 0, 50], [-360, 360], follows=("вал", 0.5),
                                             mesh_efficiency=0.9)
           ).add(box(195, 205, -5, 5, 45, 55), mass_g=300)
    m.scenario("подъём", [{"вал": 0}, {"вал": 60}], hold_s=0.2)
    _, r = _run(m, "подъём", tmp_path)
    up = r.plan.qd[:, 0] > 1e-9
    assert np.allclose(r.required[up, 0], r.required_rigid[up, 0] / 0.9, rtol=1e-6, atol=1e-9)


def test_a_printed_part_with_a_known_mass_keeps_the_mass_and_stays_printed(tmp_path):
    """part= и mass_g= вместе — печатная деталь, масса которой известна (весы, G-code):
    масса — заданная, а не по модели слоёв, и МКЭ (`sim_stress`) видит её печатной.
    Прежде так было нельзя, и у Gear Cube (массы из G-code) напряжения «не
    посчитались: нет печатных звеньев»."""
    from datum.sim.stress import stress_candidates

    m = _pendulum()
    m.links["рычаг"].pieces.clear()
    m.links["рычаг"].add(box(195, 205, -5, 5, -5, 5), part="рычаг", mass_g=123.0)
    comp = compile_mechanism(m, part_settings=_solid, cache_dir=tmp_path)
    assert comp.links["рычаг"].mass_kg == pytest.approx(0.123)
    assert "масса задана" in comp.links["рычаг"].pieces[0]["source"]
    assert stress_candidates(comp) == ["рычаг"]


# ------------------------------------------------- задевания: шаг от геометрии
@pytest.mark.parametrize("wall_deg", [60, 75, 97, 110, 130])
def test_clearance_does_not_step_over_a_thin_wall(tmp_path, wall_deg):
    """Аудит 2026-11-21: поз было 24 на любой сценарий, и рычаг, машущий на
    180°, «проходил» сквозь стенку 2 мм — под 60°, 75° и 110° проверка
    отвечала «не задевают», под 97° и 130° находила. Стенка на пути — всегда
    задевание, где бы она ни стояла."""
    import math

    from datum.checks.sim import sim_clearance
    from datum.geom import rotate

    m = Mechanism(base="clamped")
    wall = rotate(box(80, 100, -1, 1, -3, 3), rz=wall_deg)
    m.link("стойка").add(box(-8, 8, -8, 8, -30, -6), mass_g=100).add(wall, mass_g=5)
    m.link("рычаг", parent="стойка",
           joint=revolute("+Z", [0, 0, 0], [0, 180], drive=drive(5, 60))
           ).add(box(20, 100, -2, 2, -2, 2), mass_g=20)
    m.scenario("мах", [{"рычаг": 0}, {"рычаг": 180}], hold_s=0.1)
    comp, r = _run(m, "мах", tmp_path, geometry=True)
    res = sim_clearance((comp, r))
    assert res.status == "fail", res.message
    hit = r.clearance[0]
    assert abs(hit["q"]["рычаг"] - wall_deg) < 6.0, "задевание — там, где стоит стенка"
    assert r.clearance_info["step_mm"] == pytest.approx(1.0)
    assert math.isfinite(hit["t"])


def test_clearance_step_follows_the_far_end_of_the_long_link(tmp_path):
    """Стойка у оси коротка, и в рамке рычага почти не движется, — шаг обязан
    считаться и по дальнему концу рычага: препятствие стоит отдельным звеном
    на его пути."""
    from datum.checks.sim import sim_clearance
    from datum.geom import rotate

    m = Mechanism(base="clamped")
    m.link("стойка").add(box(-8, 8, -8, 8, -30, -6), mass_g=100)
    m.link("рычаг", parent="стойка",
           joint=revolute("+Z", [0, 0, 0], [0, 180], drive=drive(5, 60))
           ).add(box(20, 100, -2, 2, -2, 2), mass_g=20)
    # Неподвижный столбик 2 мм — своим звеном с ходом, который сценарий не трогает.
    m.link("столбик", parent="стойка",
           joint=revolute("+Z", [0, 0, 0], [-1, 1], drive=drive(5, 60))
           ).add(rotate(box(90, 100, -1, 1, -3, 3), rz=75), mass_g=5)
    m.scenario("мах", [{"рычаг": 0, "столбик": 0}, {"рычаг": 180, "столбик": 0}], hold_s=0.1)
    comp, r = _run(m, "мах", tmp_path, geometry=True)
    res = sim_clearance((comp, r))
    assert res.status == "fail" and "столбик" in res.message, res.message


def test_clearance_says_how_it_was_swept_and_warns_when_coarse(tmp_path, monkeypatch):
    from datum.checks.sim import sim_clearance
    from datum.sim import run as sim_run

    m = Mechanism(base="clamped")
    m.link("стойка").add(box(-8, 8, -8, 8, -30, -6), mass_g=100)
    m.link("рычаг", parent="стойка",
           joint=revolute("+Z", [0, 0, 0], [0, 180], drive=drive(5, 60))
           ).add(box(20, 100, -2, 2, -2, 2), mass_g=20)
    m.scenario("мах", [{"рычаг": 0}, {"рычаг": 180}], hold_s=0.1)
    comp, r = _run(m, "мах", tmp_path, geometry=True)
    res = sim_clearance((comp, r))
    assert res.status == "pass" and "сверено с шагом 1 мм" in res.message
    # Предел сверок на пару мал — шаг огрубляется, и вердикт уже не «прошло».
    monkeypatch.setattr(sim_run, "CLEARANCE_MAX_PER_PAIR", 20)
    comp, r = _run(m, "мах", tmp_path, geometry=True)
    res = sim_clearance((comp, r))
    assert r.clearance_info["step_mm"] > 5
    assert res.status == "warn" and "шаг огрублён" in res.message
    assert any("шаг сверки огрублён" in n for n in r.notes)


# ---------------------------------------------- трение ползуна, старт сустава
def test_slider_friction_is_mu_times_normal_force(tmp_path):
    """Аудит 2026-11-21: у ползуна считалось μ·F·r/1000, как у шарнира, —
    1 кг на bearing(mu=0.3) давал 0.0 Н вместо μ·m·g = 2.94 Н."""
    from datum.sim.mechanism import bearing, prismatic

    m = Mechanism(base="clamped")
    m.link("рельс").add(box(-50, 150, -5, 5, -10, -5), mass_g=200)
    m.link("каретка", parent="рельс",
           joint=prismatic("+X", [0, 100], drive=drive(50, 50),
                           friction=bearing(mu=0.3))
           ).add(box(-10, 10, -10, 10, -5, 15), mass_g=1000)
    m.scenario("ход", [{"каретка": 0}, {"каретка": 80}], hold_s=0.2)
    _, r = _run(m, "ход", tmp_path)
    assert float(np.abs(r.friction).max()) == pytest.approx(0.3 * 1.0 * G, rel=0.02)


def test_revolute_friction_without_a_radius_is_refused():
    from datum.sim.mechanism import bearing

    with pytest.raises(MechanismError, match="без radius_mm"):
        revolute("+Z", [0, 0, 0], [0, 90], friction=bearing(mu=0.3))
    with pytest.raises(MechanismError, match="без axial_radius_mm"):
        revolute("+Z", [0, 0, 0], [0, 90], friction=bearing(mu_axial=0.3))
    revolute("+Z", [0, 0, 0], [0, 90], friction=bearing(mu=0.3, radius_mm=4))
    revolute("+Z", [0, 0, 0], [0, 90], friction=bearing(drag=0.01))


def test_a_joint_left_out_of_the_first_pose_must_have_zero_in_range(tmp_path):
    """Упор сустава отдавал свою силу как «нужный момент»: 316 Н·м вместо 0.61."""
    m = Mechanism(base="clamped")
    m.link("стойка").add(box(-5, 5, -5, 5, -10, 0), mass_g=50)
    m.link("рычаг", parent="стойка",
           joint=revolute("+Y", [0, 0, 0], [30, 180], drive=drive(5, 60))
           ).add(box(95, 105, -5, 5, -5, 5), mass_g=100)
    m.link("кисть", parent="рычаг",
           joint=revolute("+Y", [100, 0, 0], [-90, 90], drive=drive(5, 60))
           ).add(box(100, 130, -3, 3, -3, 3), mass_g=20)
    m.scenario("кисть", [{"кисть": 0}, {"кисть": 45}], hold_s=0.1)
    with pytest.raises(MechanismError, match="рычаг не назван в первой позе"):
        _run(m, "кисть", tmp_path)
    m.scenario("оба", [{"рычаг": 30, "кисть": 0}, {"рычаг": 30, "кисть": 45}], hold_s=0.1)
    _, r = _run(m, "оба", tmp_path)
    assert float(np.abs(r.required).max()) < 5.0
