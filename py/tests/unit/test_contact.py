"""Квазистатический контакт — претензия №3.

Три звена: насколько отжать (мерится по геометрии), какая сила на это нужна
(МКЭ, вилкой), во что она превращается на скосе (клин с трением). Здесь
проверяется первое звено — на геометрии с ЗАРАНЕЕ ИЗВЕСТНЫМ наклоном, — и
поведение проверки целиком.
"""

from __future__ import annotations

import pytest
from manifold3d import CrossSection, Manifold

from datum.checks.contact import (insertion_force,
                                  interference_profile)
from datum.fem import solve as S
from datum.geom.booleans import diff
from datum.geom.prims import box
from datum.geom.solid import Solid

W, H, PLATE_Z, PLATE_T, HOLE = 5.0, 20.0, 10.0, 1.0, 5.5


@pytest.fixture(scope="module")
def plate():
    """Плита с квадратным отверстием — в неё опускают конус."""
    return diff(box(-30, 30, -30, 30, PLATE_Z, PLATE_Z + PLATE_T),
                [box(-HOLE, HOLE, -HOLE, HOLE, PLATE_Z - 1, PLATE_Z + PLATE_T + 1)]
                ).renamed("плита")


def cone(scale_top: float) -> Solid:
    """Конус с ТОЧНО ЗАДАННЫМ наклоном: tan(α) = (W·scale − W)/H."""
    poly = [(-W, -W), (W, -W), (W, W), (-W, W)]
    return Solid.wrap(
        Manifold.extrude(CrossSection([poly]), H, 0, 0.0, (scale_top, scale_top)),
        name="конус", op="extrude")


def taper(scale_top: float) -> float:
    return (W * scale_top - W) / H


# ------------------------------------------------------------------ измерение
@pytest.mark.parametrize("scale_top", [1.4, 2.0, 3.0])
def test_the_ramp_slope_is_recovered_exactly(plate, scale_top):
    """Наклон скоса задан геометрией конуса — значит ответ известен заранее.

    Так и проверялась вся затея: пока наклон считался по КОНЦАМ участка
    нарастания, он выходил заниженным (обе крайние точки лежат ЗА границами
    нарастания, каждая примерно на полшага). По внутренним точкам методом
    наименьших квадратов смещения нет.
    """
    profile = interference_profile(cone(scale_top), plate,
                                   direction="-Z", distance=14.0, samples=57)
    assert profile.ramp() == pytest.approx(taper(scale_top), rel=1e-3)


def test_the_peak_matches_the_geometry(plate):
    """Отжатие на посадке = полуширина конуса внизу минус полуширина отверстия."""
    profile = interference_profile(cone(2.0), plate, direction="-Z",
                                   distance=14.0, samples=57)
    # Худшее сечение — у нижней грани плиты, где конус шире всего внутри неё.
    want = W + taper(2.0) * (PLATE_Z + PLATE_T) - HOLE
    assert profile.peak == pytest.approx(want, rel=2e-3)


def test_a_square_step_gives_no_ramp_at_any_resolution():
    """Прямая ступенька — это ОТСУТСТВИЕ скоса, а не очень крутой скос.

    Поделить пик на один шаг развёртки и назвать это наклоном — соблазн, в
    который я попал: на прямой ступеньке так выходило tan(α) = 2.0, число про
    шаг сетки, а не про деталь. Вывод обязан не зависеть от шага.
    """
    peg = box(-5.5, 5.5, -5.5, 5.5, 0, 10).renamed("палец")
    hole = diff(box(-30, 30, -30, 30, 10, 11),
                [box(-5, 5, -5, 5, 9, 12)]).renamed("плита")
    for samples in (21, 81, 321):
        profile = interference_profile(peg, hole, direction="-Z",
                                       distance=12.0, samples=samples)
        assert profile.peak == pytest.approx(0.5, abs=0.01)
        assert profile.ramp() is None, f"при {samples} точках наклон выдумался"
        assert profile.rise_samples == 0


def test_a_free_path_has_no_interference(plate):
    thin = box(-4.0, 4.0, -4.0, 4.0, 0, 10).renamed("тонкий")
    profile = interference_profile(thin, plate, direction="-Z", distance=14.0, samples=21)
    assert profile.peak == 0.0
    assert profile.ramp() is None and profile.engaged_from is None


def test_engagement_is_measured_along_the_INSERTION(plate):
    """Путь хранится от посадки наружу, а вставляют навстречу. Первая версия
    сканировала путь в порядке хранения и отвечала про ВЫХОД детали."""
    profile = interference_profile(cone(2.0), plate, direction="-Z",
                                   distance=14.0, samples=57)
    # Конус встречает плиту далеко от посадки, а не на первом же шаге.
    assert profile.engaged_from > 5.0


def test_an_interference_beyond_the_search_limit_is_reported(plate):
    """Упор в границу поиска — не измерение. Выдать саму границу за ответ
    значило бы соврать числом, которое выглядит измеренным."""
    fat = box(-20, 20, -20, 20, 0, 10).renamed("толстяк")
    profile = interference_profile(fat, plate, direction="-Z", distance=12.0,
                                   samples=9, limit=0.5)
    assert profile.saturated


def test_a_diagonal_insertion_is_refused(plate):
    with pytest.raises(ValueError, match="вдоль осей"):
        interference_profile(cone(2.0), plate, direction=(1.0, 1.0, 0.0),
                             distance=5.0, samples=5)


def test_an_empty_obstacle_list_is_refused(plate):
    with pytest.raises(ValueError, match="пуст"):
        interference_profile(cone(2.0), [], direction="-Z")


# ------------------------------------------------------------------ проверка
SPRING = dict(spring_length=18.0, spring_width=60.0, spring_thickness=2.0,
              modulus=2000.0, poisson=0.35, divisions=(8, 4, 2))


def test_the_chain_reports_all_three_links(plate):
    result = insertion_force(cone(1.4), plate, direction="-Z", distance=14.0,
                             samples=57, max_force=1e6, friction=0.3, part_name="конус", **SPRING)
    assert result.status == "pass"
    for piece in ("отжать", "сила пружины", "скос", "усилие вставки", "Эталоны решателя"):
        assert piece in result.message, piece
    data = result.evidence[0].data
    assert data["ramp_tan"] == pytest.approx(taper(1.4), rel=1e-3)
    assert len(data["path"]) == 57


def test_without_a_ramp_the_force_is_not_invented():
    peg = box(-5.5, 5.5, -5.5, 5.5, 0, 10).renamed("палец")
    hole = diff(box(-30, 30, -30, 30, 10, 11),
                [box(-5, 5, -5, 5, 9, 12)]).renamed("плита")
    result = insertion_force(peg, hole, direction="-Z", distance=12.0, samples=41,
                             friction=0.3, part_name="палец", **SPRING)
    assert result.status == "warn"
    assert "СКОСА НЕТ" in result.message and "фаска" in result.message


def test_self_locking_is_named(plate):
    """μ·tan(α) ≥ 1 — сколько ни дави, деталь не пойдёт. Формула в этом месте
    меняет знак, и выдать отрицательную силу было бы хуже всего."""
    # tan(α) = 0.25 у этого конуса, значит самоторможение наступает при μ ≥ 4.
    result = insertion_force(cone(2.0), plate, direction="-Z", distance=14.0,
                             samples=57, friction=4.5, part_name="конус", **SPRING)
    assert result.status == "fail"
    assert "САМОТОРМОЖЕНИЕ" in result.message
    assert result.value == float("inf"), "силы тут нет вовсе, а не «очень большая»"


def test_a_free_part_needs_no_force(plate):
    thin = box(-4.0, 4.0, -4.0, 4.0, 0, 10).renamed("тонкий")
    result = insertion_force(thin, plate, direction="-Z", distance=14.0,
                             samples=21, friction=0.3, part_name="тонкий", **SPRING)
    assert result.status == "pass" and result.value == 0.0
    assert "свободно" in result.message


def test_a_threshold_inside_the_band_is_not_decided(plate):
    rough = insertion_force(cone(1.4), plate, direction="-Z", distance=14.0,
                            samples=57, max_force=1e6, friction=0.3, part_name="конус", **SPRING)
    lo, hi = rough.evidence[0].data["spring_force_band"]
    factor = rough.value / hi
    middle = (lo + hi) / 2 * factor
    result = insertion_force(cone(1.4), plate, direction="-Z", distance=14.0,
                             samples=57, max_force=middle, friction=0.3, part_name="конус", **SPRING)
    assert result.status == "warn" and "не разделяется" in result.message


def test_a_broken_solver_refuses_to_speak(plate):
    import skfem

    original = S._element
    S._element = lambda: skfem.ElementVector(skfem.ElementTetP1())
    try:
        result = insertion_force(cone(1.4), plate, direction="-Z", distance=14.0,
                                 samples=21, friction=0.3, part_name="конус", **SPRING)
    finally:
        S._element = original
    assert result.status == "error" and "не воспроизвёл эталоны" in result.message


@pytest.mark.parametrize("friction", [-0.1, 5.0])
def test_a_nonsensical_friction_is_refused(plate, friction):
    with pytest.raises(ValueError, match="трения"):
        insertion_force(cone(1.4), plate, direction="-Z", friction=friction, **SPRING)


def test_material_properties_have_no_defaults():
    """Трение и Пуассон — свойства материала и пары, а не константы.

    Умолчание 0.3 прятало трение от развёртки по незамеренному: `latch` его не
    передавал, и «трение μ=0.3» в отчёте было числом из кода. Теперь, как и
    модуль упругости, они обязательны.
    """
    import inspect

    from datum.checks.analysis import cam_force
    from datum.checks.fem import flexure_force_fem

    for fn, names in ((insertion_force, ("friction", "poisson", "modulus")),
                      (flexure_force_fem, ("poisson", "modulus")),
                      (cam_force, ("friction_deg",))):
        params = inspect.signature(fn).parameters
        for name in names:
            assert params[name].default is inspect.Parameter.empty, (fn.__name__, name)
