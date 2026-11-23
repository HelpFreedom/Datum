"""МКЭ под произвольной нагрузкой и напряжения звена в худший момент.

Решатель — по формулам консоли (сила, чистый момент через пятно, собственный
вес) и по равновесию: реакция заделки обязана уравновесить приложенное. Звено
механизма — по сверке с MuJoCo: всё, что приложено к телу, равно и противоположно
силе и моменту родителя на звено. Пороги с запасом: мешер fTetWild не
детерминирован (CLAUDE.md), прогиб от запуска к запуску гуляет на доли процента.
"""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest

pytest.importorskip("skfem")

from datum.fem.tetmesh import mesher_available  # noqa: E402

pytestmark = pytest.mark.skipif(not mesher_available(), reason="нет мешера fTetWild")

L, B, H, E = 100.0, 10.0, 10.0, 2000.0
I_Y = B * H ** 3 / 12


@pytest.fixture(scope="module")
def bar():
    from datum.fem.loads import facet_centroids
    from datum.fem.tetmesh import tet_mesh
    from datum.geom import box

    tm = tet_mesh(box(0, L, -B / 2, B / 2, -H / 2, H / 2), edge_relative=0.04)
    m = tm.skfem()
    bf = m.boundary_facets()
    c = facet_centroids(m, bf)
    tol = 2 * tm.envelope_mm + 1e-3
    return tm, bf[c[:, 0] < tol], bf[c[:, 0] > L - tol]


def _solve(bar, **kw):
    from datum.fem.loads import solve_loads
    from datum.fem.model import Material

    tm, root, _ = bar
    return solve_loads(tm, Material(E, 0.3), fixed=root, **kw)


def test_a_tip_force_bends_the_cantilever_by_the_textbook(bar):
    from datum.fem.loads import Patch

    _, _, tip = bar
    r = _solve(bar, patches=[Patch("торец", tip, np.array([0, 0, -10.0]), about=np.array([L, 0, 0]))])
    assert r.max_displacement == pytest.approx(10 * L ** 3 / (3 * E * I_Y), rel=0.03)
    f, m = r.imbalance
    assert f < 1e-8 and m < 1e-8                      # реакция уравновешивает силу


def test_a_pure_moment_patch_transmits_the_moment_and_no_force(bar):
    """Пятно раздаёт момент, как жёсткий фланец: ∫t = 0, ∫r×t = M. Прогиб — M·L²/(2EI);
    невязка силы у чистого момента меряется не долей нуля (прежде выходила «257»)."""
    from datum.fem.loads import Patch

    _, _, tip = bar
    r = _solve(bar, patches=[Patch("момент", tip, np.zeros(3), moment=np.array([0, 1000.0, 0]),
                                   about=np.array([L, 0, 0]))])
    assert r.max_displacement == pytest.approx(1000 * L ** 2 / (2 * E * I_Y), rel=0.03)
    assert np.linalg.norm(r.applied[0]) < 1e-9
    assert r.applied[1][1] == pytest.approx(1000.0, rel=1e-9)
    assert max(r.imbalance) < 1e-8
    # середина пролёта — чистый изгиб: σ = 6M/(b·h²) = 6 МПа; 99-й процентиль у него
    assert r.vm_p99 == pytest.approx(6.0, rel=0.12)


def test_own_weight_is_a_body_force_not_a_point(bar):
    rho_g = 1.25e-3 * 9.81                            # Н/мм³ (в тысячу раз тяжелее PETG — для сигнала)
    r = _solve(bar, patches=[], body=(np.array([0, 0, -rho_g]), np.zeros((3, 3))))
    w = rho_g * B * H
    assert r.applied[0][2] == pytest.approx(-w * L, rel=0.01)
    assert r.max_displacement == pytest.approx(w * L ** 4 / (8 * E * I_Y), rel=0.03)


def test_a_mesh_piece_without_support_is_refused(bar):
    from datum.fem.loads import solve_loads
    from datum.fem.model import FemError, Material
    from datum.fem.tetmesh import tet_mesh
    from datum.geom import box
    from datum.geom.booleans import compose

    two = compose([box(0, 20, 0, 5, 0, 5), box(40, 60, 0, 5, 0, 5)])
    tm = tet_mesh(two, edge_relative=0.08)
    m = tm.skfem()
    from datum.fem.loads import facet_centroids

    bf = m.boundary_facets()
    root = bf[facet_centroids(m, bf)[:, 0] < 1e-2]
    with pytest.raises(FemError, match="без опоры"):
        solve_loads(tm, Material(E, 0.3), fixed=root, patches=[])


def test_vertex_colours_go_into_the_glb_as_linear_color_0():
    from datum.io.glb import mesh_to_glb

    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    f = np.array([[0, 1, 2]])
    plain = mesh_to_glb(v, f, color="#ff0000")
    painted = mesh_to_glb(v, f, vertex_colors=[[255, 0, 0], [0, 255, 0], [128, 128, 128]])
    doc = json.loads(painted[20:20 + struct.unpack("<I", painted[12:16])[0]])
    prim = doc["meshes"][0]["primitives"][0]
    assert prim["attributes"]["COLOR_0"] == 2
    assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0]
    assert b"COLOR_0" not in plain                    # без карты — файл прежний
    with pytest.raises(ValueError):
        mesh_to_glb(v, f, vertex_colors=[[0, 0, 0]])


# ------------------------------------------------------------ звено механизма
def _lever(tmp_path, scenario):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_sim import _solid

    from datum.geom import box, cyl_y
    from datum.geom.booleans import diff
    from datum.sim import Mechanism, drive, revolute
    from datum.sim.compile import compile_mechanism
    from datum.sim.run import run_scenario

    m = Mechanism(base="clamped")
    m.link("стойка").add(box(-5, 5, -5, 5, -30, -20), mass_g=50)
    arm = m.link("рычаг", parent="стойка",
                 joint=revolute("+Y", [0, 0, 0], [-180, 180], drive=drive(50, 120)))
    arm.add(diff(box(-8, L, -B / 2, B / 2, -H / 2, H / 2), [cyl_y(6.0, -10, 10)]), part="рычаг")
    arm.add(box(40, 50, -5, 5, 5, 15), mass_g=200, label="мотор")      # навесное тело сверху
    m.payload("груз", link="рычаг", mass_g=1000, at=[L - 2, 0, 0])
    m.scenario("держать", [{"рычаг": 0}], hold_s=0.3, payloads=["груз"])
    m.scenario("махнуть", [{"рычаг": 0}, {"рычаг": -90}, {"рычаг": 0}], hold_s=0.05,
               payloads=["груз"])
    comp = compile_mechanism(m, part_settings=_solid, cache_dir=tmp_path)
    return comp, run_scenario(comp, scenario, geometry=False)


@pytest.mark.parametrize("scenario", ["держать", "махнуть"])
def test_the_link_loads_balance_what_mujoco_says_the_joint_carries(tmp_path, scenario):
    """Груз, навесной мотор и своё тело (вес и инерция) против реакции сустава из
    MuJoCo. Тело сплошное (заполнение 1), поэтому центр масс печати = центр объёма,
    и расхождение — только инерция вращения навесного тела, которой нет."""
    from datum.sim.stress import link_stress

    comp, r = _lever(tmp_path, scenario)
    rep = link_stress(comp, r, "рычаг", modulus=E, strength_z=30.0, strength_xy=47.0, edge=0.04)
    assert rep.pieces == ["рычаг"] and rep.loose == ["мотор"]
    assert max(rep.imbalance) < 1e-8
    assert rep.mujoco_mismatch < 0.01
    names = [ld["name"] for ld in rep.loads]
    assert names == ["груз груз", "мотор"]
    if scenario == "держать":
        # в покое: груз — m·g вниз, мотор — тоже
        assert rep.loads[0]["force_n"] == pytest.approx([0, 0, -9.81], abs=1e-6)
        assert rep.loads[1]["force_n"] == pytest.approx([0, 0, -0.2 * 9.81], abs=1e-6)
        # прогиб конца: консоль от кромки отверстия, P = груз + мотор на своих плечах
        assert 0.5 < rep.max_displacement_mm < 3.0
    else:
        # худший момент — не покой: силы инерции входят в нагрузку
        assert np.linalg.norm(rep.loads[0]["force_n"]) > 9.81 * 1.01
    assert rep.utilization == pytest.approx(rep.value_mpa / 30.0)


def test_stress_is_refused_for_a_base_and_for_a_link_without_printed_parts(tmp_path):
    from datum.sim.mechanism import MechanismError
    from datum.sim.stress import link_stress, stress_candidates

    comp, r = _lever(tmp_path, "держать")
    assert stress_candidates(comp) == ["рычаг"]
    with pytest.raises(MechanismError, match="на ШАРНИРЕ"):
        link_stress(comp, r, "стойка", modulus=E)


def test_the_stress_check_passes_and_fails_by_the_layer_strength(tmp_path):
    from datum.checks.sim import sim_stress

    comp, r = _lever(tmp_path, "держать")
    strong = sim_stress((comp, r), modulus=E, strength_z=30.0, strength_xy=47.0, edge=0.04)
    assert [c.status for c in strong] == ["pass"]
    assert "между слоями 30" in strong[0].message
    weak = sim_stress((comp, r), modulus=E, strength_z=0.5, edge=0.04)
    assert weak[0].status == "fail" and "НЕ ВЫДЕРЖИТ" in weak[0].message
    none = sim_stress((comp, r), edge=0.04)
    assert none[0].status == "skip"


# ------------------------------------------------------------ скорость без потери точности
def test_batched_stiffness_equals_skfem_asm_on_an_irregular_mesh():
    """Пакетная сборка (BLAS, кусками) — те же интегралы и нумерация, что `asm`:
    прежде 73 с на одном ядре у плеча руки, теперь 5."""
    from skfem import Basis, MeshTet, asm
    from skfem.models.elasticity import linear_elasticity

    from datum.fem import solve as S
    from datum.fem.model import Material

    m = MeshTet().refined(2)
    m = MeshTet(m.p + 0.05 * np.sin(7 * m.p), m.t)          # неправильная сетка
    b = Basis(m, S._element())
    mat = Material(2111, 0.35)
    ref = asm(linear_elasticity(*mat.lame), b)
    old = S.STIFFNESS_CHUNK
    try:
        S.STIFFNESS_CHUNK = 37                               # кусков много, и края кусков проверены
        K = S._stiffness(b, mat)
    finally:
        S.STIFFNESS_CHUNK = old
    assert abs(K - ref).max() <= 1e-12 * abs(ref).max()


def test_surface_distance_matches_trimesh_near_the_surface():
    import trimesh

    from datum.geom import box, cyl_z
    from datum.geom.booleans import diff
    from datum.geom.convert import to_trimesh
    from datum.geom.measure import man
    from datum.geom.proximity import surface_distance

    s = diff(box(-30, 30, -20, 20, 0, 10), [cyl_z(16, -1, 11)])   # щепки по образующей отверстия
    p = np.random.default_rng(1).uniform([-35, -25, -5], [35, 25, 15], (5000, 3))
    d, inside = surface_distance(s, p, spacing=1.0)
    tm = to_trimesh(man(s))
    _, exact, _ = trimesh.proximity.closest_point(tm, p)
    near = exact < 3
    assert np.abs(d - exact)[near].max() < 0.1
    assert (inside == tm.contains(p))[near].mean() > 0.999


def test_a_mesh_ladder_is_built_in_separate_processes():
    from datum.fem.tetmesh import tet_meshes
    from datum.geom import box

    one = tet_meshes(box(0, 40, 0, 10, 0, 4), [0.05, 0.1])
    assert one[0].nodes > one[1].nodes > 0
    many = tet_meshes(None, [], batch=[(box(0, 40, 0, 10, 0, 4), [0.05, 0.1]),
                                       (box(0, 20, 0, 20, 0, 5), [0.08])])
    assert [len(g) for g in many] == [2, 1]


def test_pieces_touching_across_a_gap_are_tied_and_carry_load():
    """Два бруска встык с зазором 0.4 мм (больше конверта мешера — иначе он сольёт их
    сам): защемлён только первый, сила — на торце
    второго. Без склейки второй висел бы в пустоте (отказ), со склейкой — консоль
    почти как цельная (стык жёсткий, но не сплошной)."""
    from datum.fem.loads import Patch, facet_centroids, solve_loads, tie_pairs
    from datum.fem.model import FemError, Material
    from datum.fem.tetmesh import tet_mesh
    from datum.geom import box
    from datum.geom.booleans import compose

    two = compose([box(0, 50, -5, 5, -5, 5), box(50.4, 100, -5, 5, -5, 5)])
    tm = tet_mesh(two, edge_relative=0.04)
    m = tm.skfem()
    bf = m.boundary_facets()
    c = facet_centroids(m, bf)
    tol = 2 * tm.envelope_mm + 1e-3
    root, tip = bf[c[:, 0] < tol], bf[c[:, 0] > 100 - tol]
    load = [Patch("торец", tip, np.array([0, 0, -10.0]), about=np.array([100, 0, 0]))]
    with pytest.raises(FemError, match="без опоры"):
        solve_loads(tm, Material(E, 0.3), fixed=root, patches=load)
    ties = tie_pairs(m, 0.5)
    assert len(ties) > 0
    r = solve_loads(tm, Material(E, 0.3), fixed=root, patches=load, ties=ties)
    f_err, m_err = r.imbalance
    assert f_err < 1e-8
    # пружина склейки соединяет узлы на расстоянии зазора: встречные силы на этом плече
    # — малая пара, отсюда невязка момента порядка зазор/длина (0.2 % здесь)
    assert m_err < 0.01
    whole = 10 * L ** 3 / (3 * E * I_Y)
    assert whole * 0.97 < r.max_displacement < whole * 1.3


def test_pardiso_and_the_superlu_fallback_agree(monkeypatch):
    """PARDISO есть только под x86-64 Linux/Windows; на остальных — SuperLU. Оба пути
    обязаны давать одно решение (на плече руки расхождение было 9e-12)."""
    import scipy.sparse as sp

    from datum.fem import solve as S

    n = 400
    rng = np.random.default_rng(3)
    M = sp.random(n, n, density=0.02, random_state=rng)
    A = (M @ M.T + sp.eye(n) * n).tocsr()                   # симметричная положительно определённая
    b = rng.normal(size=n)
    with_default = S.spd_factor(A)(b)
    monkeypatch.setattr(S, "_pardiso", lambda: None)
    fallback = S.spd_factor(A)(b)
    assert np.abs(A @ fallback - b).max() < 1e-9 * np.abs(b).max()
    assert np.abs(with_default - fallback).max() < 1e-10 * np.abs(fallback).max()


def test_a_mass_only_mechanism_says_how_to_make_its_links_printed(tmp_path):
    """Звенья, чьи тела объявлены только массой (как прежде у Gear Cube — массы из
    G-code), для МКЭ покупные. Ответ называет звенья и как поправить, а не «нет
    печатных звеньев» без объяснения."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_sim import _pendulum, _solid

    from datum.checks.sim import sim_stress
    from datum.sim.compile import compile_mechanism
    from datum.sim.run import run_scenario

    m = _pendulum()
    m.scenario("держать", [{"рычаг": 0}], hold_s=0.2)
    comp = compile_mechanism(m, part_settings=_solid, cache_dir=tmp_path)
    r = run_scenario(comp, "держать", geometry=False)
    [c] = sim_stress((comp, r), modulus=E, strength_z=30.0)
    assert c.status == "skip"
    assert "рычаг" in c.message and "part=" in c.message and "mass_g" in c.message


def test_a_bad_mesh_is_rebuilt_and_said_so(tmp_path, monkeypatch):
    """Мешер случаен: на сетке, где тест заплаты не прошёл, числа не называются —
    она строится заново, и ответ это говорит. Не вышло за все попытки — отказ."""
    import datum.fem.verify as V
    from datum.sim import stress as ST
    from datum.sim.mechanism import MechanismError

    comp, r = _lever(tmp_path, "держать")
    real = V.patch_test
    calls = []

    def flaky(mesh, material, **kw):
        res = real(mesh, material, **kw)
        calls.append(1)
        if len(calls) == 1:                        # первая сетка «плохая»
            from dataclasses import replace

            bad = tuple(replace(c, error=1e-3) for c in res.cases)
            return replace(res, cases=bad)
        return res

    monkeypatch.setattr(V, "patch_test", flaky)
    rep = ST.link_stress(comp, r, "рычаг", modulus=E, strength_z=30.0, edge=0.04)
    assert len(calls) == 2
    assert any("построена заново" in n for n in rep.notes)

    def never(mesh, material, **kw):
        from dataclasses import replace

        res = real(mesh, material, **kw)
        return replace(res, cases=tuple(replace(c, error=1e-3) for c in res.cases))

    monkeypatch.setattr(V, "patch_test", never)
    with pytest.raises(MechanismError, match="сетках подряд"):
        ST.link_stress(comp, r, "рычаг", modulus=E, strength_z=30.0, edge=0.04)
