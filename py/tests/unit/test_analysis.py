"""Аналитические проверки.

Эталон здесь не выдуманный: это числа из `calc.py` проекта toothpaste-dispenser
рабочего процесса CAD. Если формула переврана в коэффициенте, сверка с ними
это покажет — а по результату такую ошибку не заметишь, число просто выйдет
другим и всё равно правдоподобным.
"""

from __future__ import annotations

import math

import pytest

from datum.checks.analysis import (beam_strain, cam_force, com_support_margin,
                                   euler_buckling, flexure_force, mass_estimate,
                                   tipping_margin)
from datum.geom.prims import box, cyl_z
from datum.geom.xform import move

# Параметры дозатора: work/params.py и calc.py исходного проекта.
LEAF_L, LEAF_T, LEAF_B = 70.0, 1.0, 18.0
STROKE_MAX, PRELOAD = 17.0, 6.0
E_PLA, E_PETG = 3000.0, 2000.0
EPS_PLA = 0.015


# ------------------------------------------------------------------- изгиб
class TestBeamStrain:
    def test_matches_the_original_project(self):
        """calc.py: 1.04 % без преднатяга, 1.41 % с ним."""
        bare = beam_strain(length=LEAF_L, thickness=LEAF_T, deflection=STROKE_MAX,
                           limit=EPS_PLA)
        loaded = beam_strain(length=LEAF_L, thickness=LEAF_T,
                             deflection=STROKE_MAX + PRELOAD, limit=EPS_PLA)
        assert bare.value * 100 == pytest.approx(1.0408, abs=0.0005)
        assert loaded.value * 100 == pytest.approx(1.4082, abs=0.0005)
        assert bare.status == "pass" and loaded.status == "pass"

    def test_guided_is_twice_the_cantilever(self):
        """Перепутать закрепление — значит ошибиться РОВНО ВДВОЕ и не заметить:
        число останется правдоподобным. Поэтому соотношение закреплено."""
        common = dict(length=LEAF_L, thickness=LEAF_T, deflection=10.0, limit=1.0)
        guided = beam_strain(mode="guided", **common).value
        cantilever = beam_strain(mode="cantilever", **common).value
        assert guided == pytest.approx(2 * cantilever, rel=1e-12)

    def test_the_margin_shows_how_close_to_the_edge(self):
        """Ради этого всё и затевалось: «прошло» с запасом 6 % и «прошло» с
        запасом 30 % — разные вещи, а старый скрипт печатал одно и то же OK."""
        loaded = beam_strain(length=LEAF_L, thickness=LEAF_T,
                             deflection=STROKE_MAX + PRELOAD, limit=EPS_PLA)
        bare = beam_strain(length=LEAF_L, thickness=LEAF_T, deflection=STROKE_MAX,
                           limit=EPS_PLA)
        assert 0 < loaded.margin < bare.margin
        assert loaded.margin == pytest.approx(0.061, abs=0.002)
        assert "запас меньше 15 %" not in bare.message

    def test_exceeding_the_limit_says_by_how_much(self):
        result = beam_strain(length=20.0, thickness=2.0, deflection=5.0, limit=0.015)
        assert result.status == "fail"
        assert "ПРЕВЫШЕН" in result.message and "раза" in result.message

    def test_a_bad_mode_is_refused_loudly(self):
        with pytest.raises(ValueError, match="закрепление"):
            beam_strain(length=10, thickness=1, deflection=1, limit=0.01, mode="как-то")


# --------------------------------------------------------------- жёсткость
class TestFlexureForce:
    def test_matches_the_original_project(self):
        """calc.py: PLA 1.89 Н в покое и 7.24 Н на полном ходе.

        Эталон записан с полной точностью, а не переписан с округлённого
        вывода: округление в тесте скрывает ошибку в четвёртом знаке, а она и
        означала бы неверный коэффициент.
        """
        rest = flexure_force(leaves=2, length=LEAF_L, width=LEAF_B, thickness=LEAF_T,
                             modulus=E_PLA, deflection=PRELOAD)
        full = flexure_force(leaves=2, length=LEAF_L, width=LEAF_B, thickness=LEAF_T,
                             modulus=E_PLA, deflection=STROKE_MAX + PRELOAD)
        assert rest.value == pytest.approx(1.889213, abs=1e-5)
        assert full.value == pytest.approx(7.241982, abs=1e-5)

    def test_force_grows_with_the_cube_of_thickness(self):
        """k ∝ t³. Лист толще на треть — вдвое жёстче, и это та ошибка, из-за
        которой флексуры ломаются: 1.0 вместо 1.2 мм выглядит мелочью."""
        thin = flexure_force(leaves=1, length=50, width=10, thickness=1.0,
                             modulus=1000, deflection=1).value
        thick = flexure_force(leaves=1, length=50, width=10, thickness=1.2,
                              modulus=1000, deflection=1).value
        assert thick / thin == pytest.approx(1.2 ** 3, rel=1e-12)

    def test_too_weak_is_a_failure_too(self):
        """Пружина, которая не возвращает, бесполезна ровно так же, как та,
        которую не продавить."""
        result = flexure_force(leaves=1, length=100, width=5, thickness=0.5,
                               modulus=2000, deflection=1.0, min_force=5.0)
        assert result.status == "fail" and "МАЛО" in result.message

    def test_the_margin_is_to_the_nearer_edge(self):
        """У вилки запас считается до БЛИЖНЕЙ границы: иначе проверка у самого
        верхнего края притворялась бы благополучной за счёт далёкого нижнего."""
        result = flexure_force(leaves=2, length=LEAF_L, width=LEAF_B,
                               thickness=LEAF_T, modulus=E_PLA, deflection=PRELOAD,
                               min_force=1.5, max_force=2.0)
        # 1.89 Н: до верха 0.11, до низа 0.39 — в долях 0.055 и 0.26.
        assert result.margin == pytest.approx(0.055394, abs=1e-5)


# ------------------------------------------------------------ устойчивость
class TestEulerBuckling:
    def test_matches_the_original_project(self):
        """calc.py: тяга 4×8×240 из PLA — критическая сила 21.9 Н."""
        result = euler_buckling(length=240.0, width=8.0, thickness=4.0,
                                modulus=E_PLA, load=1.0)
        assert result.value == pytest.approx(21.93, abs=0.01)
        assert result.status == "pass"

    def test_the_weak_axis_decides(self):
        """Считать по сильной оси — обычный способ получить бодрое число вместо
        честного: стержень потеряет устойчивость там, где он тоньше."""
        flat = euler_buckling(length=100, width=10, thickness=2, modulus=2000, load=1)
        turned = euler_buckling(length=100, width=2, thickness=10, modulus=2000, load=1)
        assert flat.value == pytest.approx(turned.value, rel=1e-12)

    def test_end_conditions_change_the_answer_fourfold(self):
        common = dict(length=100, width=5, thickness=5, modulus=2000, load=1)
        pinned = euler_buckling(ends="pinned", **common).value
        fixed = euler_buckling(ends="fixed", **common).value
        assert fixed == pytest.approx(4 * pinned, rel=1e-12)


# ----------------------------------------------------------------- кулачок
class TestCamForce:
    def test_matches_the_original_project(self):
        """calc.py: шибер дозатора — 0.606 Н на выходе при нужных 0.6."""
        stiffness = 2 * 12 * E_PETG * (LEAF_B * LEAF_T ** 3 / 12) / LEAF_L ** 3
        result = cam_force(force=stiffness * PRELOAD, rise=6.5, run=6.0,
                           friction_deg=17.0, min_out=0.6, name="шибер")
        assert result.value == pytest.approx(0.6064, abs=0.001)
        assert result.status == "pass"
        assert "запас меньше 15 %" in result.message, (
            "проверка обязана сказать, что запас ничтожный: 0.606 против 0.6")

    def test_friction_adds_to_the_angle_and_not_subtracts(self):
        """Трение мешает, а не помогает. Перепутать знак — значит получить
        механизм, который в расчёте работает, а в руках встаёт."""
        smooth = cam_force(force=10, rise=1, run=1, friction_deg=0.0).value
        rough = cam_force(force=10, rise=1, run=1, friction_deg=17.0).value
        assert rough < smooth

    def test_a_steep_cam_eats_the_force(self):
        gentle = cam_force(force=10, rise=1, run=5, friction_deg=10).value
        steep = cam_force(force=10, rise=5, run=1, friction_deg=10).value
        assert steep < gentle / 3


# -------------------------------------------------------------------- масса
class TestMass:
    def test_volume_times_density(self):
        part = box(0, 100, 0, 100, 0, 10)       # 100 см³
        result = mass_estimate(part, density=1.24, name="плита")
        assert result.value == pytest.approx(124.0, rel=1e-9)

    def test_fill_is_not_the_slicer_setting(self):
        """У детали с 20 % заполнения и четырьмя периметрами настоящая доля
        материала заметно выше 0.2 — это измеряемая величина, а не настройка."""
        part = box(0, 100, 0, 100, 0, 10)
        assert mass_estimate(part, density=1.24, fill=0.45).value == pytest.approx(55.8)

    def test_outside_the_window_is_a_failure(self):
        part = box(0, 100, 0, 100, 0, 10)
        result = mass_estimate(part, density=1.24, max_g=50.0)
        assert result.status == "fail" and "вне вилки" in result.message


# -------------------------------------------------------------- устойчивость
class TestStability:
    def test_com_over_a_wide_base_is_comfortable(self):
        part = box(-40, 40, -40, 40, 0, 5) + box(-5, 5, -5, 5, 0, 60)
        result = com_support_margin(part, base_z=0.0, min_margin=2.0, name="башня")
        assert result.status == "pass"
        assert result.value > 30, "центр масс должен быть далеко от края"

    def test_a_narrow_foot_is_caught(self):
        """Габарит здесь обманул бы: у детали на тонкой ножке он вдвое больше
        настоящей опоры."""
        part = box(-3, 3, -3, 3, 0, 60) + box(20, 40, -10, 10, 55, 60)
        result = com_support_margin(part, base_z=0.0, min_margin=2.0, name="грибок")
        assert result.status == "fail"
        assert result.value < 2.0

    def test_tipping_matches_hand_arithmetic(self):
        """m·g·d против F·доля·h — оба множителя видно в сообщении."""
        part = box(-50, 50, -50, 50, 0, 4)
        result = tipping_margin(part, mass_g=650.0, push_n=20.0, push_z=49.5,
                                lateral_frac=0.3, min_ratio=1.5, name="дозатор")
        restoring = 0.650 * 9.81 * 50.0
        tipping = 20.0 * 0.3 * 49.5
        assert result.value == pytest.approx(restoring / tipping, rel=1e-9)

    def test_a_tall_push_tips_what_a_low_one_does_not(self):
        part = box(-30, 30, -30, 30, 0, 4)
        low = tipping_margin(part, mass_g=200.0, push_n=20.0, push_z=20.0)
        high = tipping_margin(part, mass_g=200.0, push_n=20.0, push_z=200.0)
        assert low.value > high.value
        assert high.status == "fail"

    def test_a_part_off_the_floor_says_so(self):
        part = move(box(0, 10, 0, 10, 0, 10), dz=5)
        result = com_support_margin(part, base_z=0.0)
        assert result.status == "error" and "не касается" in result.message


def test_no_material_constants_are_hidden_inside():
    """Главное правило модуля: свойства материала приходят аргументами.

    Спрятав модуль упругости в таблицу, мы получили бы число, выглядящее
    точным, и уверенность на пустом месте: у печатного PLA он лежит между 2000
    и 3600 МПа в зависимости от ориентации слоёв. Значит в проекте он обязан
    быть параметром с происхождением, и развёртка по незамеренному должна
    показывать, решает ли дело именно эта догадка.
    """
    import inspect

    from datum.checks import analysis, contact, fem

    # Проверки контакта и МКЭ — тоже: умолчание трения 0.3 и Пуассона 0.35 жили
    # там, пока этот тест читал только analysis.
    for module in (analysis, contact, fem):
        source = inspect.getsource(module)
        for suspicious in ("PLA\":", "PETG\":", "= 3000", "= 2000.0", "MATERIALS",
                           "DEFAULT_FRICTION", "friction: float =", "poisson: float =",
                           "friction_deg: float ="):
            assert suspicious not in source, (
                f"в {module.__name__} завелась константа материала: {suspicious!r}")


@pytest.mark.parametrize("rise", [20.0, 60.0])
def test_a_self_locking_cam_is_a_failure_not_infinite_force(rise):
    """β + φ ≥ 90°: клин заперт. Прежняя версия давала «∞ Н на выходе» и pass."""
    r = cam_force(force=10, rise=rise, run=6, friction_deg=17, min_out=0.6)
    assert r.status == "fail"
    assert r.value == 0.0
    assert "САМОТОРМОЖЕНИЕ" in r.message


def test_a_steep_cam_just_below_locking_still_passes_force_through():
    r = cam_force(force=10, rise=6.5, run=6, friction_deg=17, min_out=0.6)
    assert r.status == "pass" and 0 < r.value < 10


@pytest.mark.parametrize("kw", [{"force": -1}, {"friction_deg": 95}, {"friction_deg": -1}])
def test_a_nonsensical_cam_input_is_refused(kw):
    args = dict(force=10, rise=6.5, run=6, friction_deg=17) | kw
    with pytest.raises(ValueError):
        cam_force(**args)
