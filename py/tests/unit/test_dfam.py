"""Печатность слоями: прилипание, поддержки, мосты.

Каждая проверка меряется на случае с ЗАРАНЕЕ ИЗВЕСТНЫМ ответом — иначе
«выглядит правдоподобно» так и осталось бы единственным доводом.
"""

from __future__ import annotations

import pytest

from datum.checks.dfam import (bed_contact, bridge_span, overhanging,
                               support_volume)
from datum.geom.booleans import diff, union
from datum.geom.prims import box
from datum.geom.voxel import voxelize


#: Плита 20×20×2 на колонне 4×4 высотой 10: под плитой висит (400 − 16) мм²
#: на высоте 10, то есть 3840 мм³ поддержек.
def _tee():
    return union([box(8, 12, 8, 12, 0, 10), box(0, 20, 0, 20, 10, 12)])


#: Две опоры по 4 мм и плита сверху: геометрический зазор 22 мм.
def _bridge():
    return union([box(0, 4, 0, 20, 0, 10), box(26, 30, 0, 20, 0, 10),
                  box(0, 30, 0, 20, 10, 12)])


# ------------------------------------------------------------------ прилипание
def test_the_bed_area_is_exact_for_a_box():
    result = bed_contact(box(0, 20, 0, 30, 0, 10), name="коробка")
    assert result.value == pytest.approx(600.0, rel=1e-6)
    assert result.status == "pass"


def test_a_part_standing_on_an_edge_is_refused():
    """Высокую деталь на пятачке оторвёт, и узнаётся это на третьем часу."""
    thin = box(0, 40, 0, 1, 0, 60)
    assert bed_contact(thin, min_area=100.0, name="стойка").status == "fail"


def test_turning_the_part_changes_the_bed_area():
    """Ради этого `up` и заведён: деталь строится в СБОРОЧНОМ положении, а
    печатается в другом. Замерено на дозаторе: у шибера 18 мм² против 484 у
    перевёрнутого."""
    slab = box(0, 40, 0, 30, 0, 2)
    flat = bed_contact(slab, up="+Z", name="плита").value
    edge = bed_contact(slab, up="+X", name="плита").value
    assert flat == pytest.approx(40 * 30, rel=1e-6)
    assert edge == pytest.approx(30 * 2, rel=1e-6)


@pytest.mark.parametrize("up", ["вверх", "+Q", "Z", ""])
def test_a_nonsensical_orientation_is_refused(up):
    with pytest.raises(ValueError, match="ось вверх"):
        bed_contact(box(0, 10, 0, 10, 0, 10), up=up)


# ------------------------------------------------------------------ поддержки
def test_the_support_volume_converges_to_the_known_answer():
    """Точный ответ 3840 мм³. Недобор — однослойный поясок правила 45° вокруг
    колонны, и он обязан убывать с шагом."""
    got = [support_volume(_tee(), step=s, name="тавр").value for s in (1.0, 0.5, 0.25)]
    assert got[0] < got[1] < got[2] < 3840
    assert abs(got[-1] - 3840) / 3840 < 0.03


def test_a_slope_of_45_degrees_needs_no_support():
    """Классическое правило, по которому живут все слайсеры: уклон, уходящий
    вбок не быстрее чем на слой за слой, держится сам."""
    steps = [box(0, 20 - i, 0, 20, i, i + 1) for i in range(10)]
    assert support_volume(union(steps), step=0.5, name="лесенка").value == 0.0


def test_a_flat_overhang_does_need_support():
    result = support_volume(_tee(), step=0.5, name="тавр")
    assert result.value > 3000
    assert "зачищать" in result.message


def test_turning_the_part_can_remove_the_supports_entirely():
    """Главная польза проверки — подсказать, как класть деталь."""
    tee = _tee()
    upright = support_volume(tee, up="+Z", step=0.5, name="тавр").value
    on_side = support_volume(tee, up="+X", step=0.5, name="тавр").value
    assert upright > 3000 and on_side < upright / 3


def test_the_hanging_mask_leaves_the_bottom_layer_alone():
    """Нижний слой лежит на столе и не повисает никогда."""
    mask = overhanging(voxelize(_tee(), 0.5))
    assert not mask[0].any()


@pytest.mark.parametrize("limit", [0.0, 90.0, -5.0, 120.0])
def test_a_nonsensical_overhang_limit_is_refused(limit):
    with pytest.raises(ValueError, match="предел нависания"):
        overhanging(voxelize(box(0, 10, 0, 10, 0, 10), 2.0), limit)


# ------------------------------------------------------------------ мосты
def test_the_bridge_span_converges_to_the_gap():
    """Зазор 22 мм. Измеренный пролёт короче на два шага растра — правило 45°
    разрешает первому ряду за опорой держаться, — и разница уходит с шагом."""
    got = [bridge_span(_bridge(), step=s, name="мост").value for s in (1.0, 0.5, 0.25)]
    assert got[0] < got[1] < got[2] < 22.0
    assert abs(got[-1] - 22.0) / 22.0 < 0.05


def test_a_cantilever_is_not_a_bridge():
    """Полоса без опоры хотя бы с одного конца — консоль, и разбираться с ней
    должны поддержки. Первая версия считала её мостом и врала на 20 мм."""
    cantilever = union([box(0, 4, 0, 20, 0, 10), box(0, 30, 0, 20, 10, 12)])
    result = bridge_span(cantilever, step=0.5, name="консоль")
    assert result.value == 0.0
    assert support_volume(cantilever, step=0.5, name="консоль").value > 0


def test_a_well_bridges_in_both_directions():
    """У колодца выгодны оба направления, и пролёт обязан быть одинаков."""
    well = union([diff(box(0, 40, 0, 40, 0, 10), [box(8, 32, 8, 32, -1, 11)]),
                  box(0, 40, 0, 40, 10, 12)])
    result = bridge_span(well, step=0.5, name="колодец")
    assert result.value == pytest.approx(24.0, abs=1.0)



@pytest.mark.parametrize("step", [0.5, 1.0])
def test_a_ledge_along_a_wall_is_not_a_bridge(step):
    """Губка корпуса latch: кольцо шириной 0.8 мм, нависающее внутрь проёма.

    Вдоль стороны ряд её ячеек «опёрт» с обоих концов на углы, и первая версия
    называла это мостом в 35.5 мм. Но поперёк это консоль в 0.8 мм, прилипшая
    к стене по всей длине, — её ведёт нависающий периметр, и речь о ней у
    поддержек."""
    box_with_lip = union([
        diff(box(0, 60, 0, 40, 0, 20), [box(2, 58, 2, 38, 2, 21)]),
        diff(box(2, 58, 2, 38, 17, 20), [box(2.8, 57.2, 2.8, 37.2, 16, 21)]),
    ])
    assert bridge_span(box_with_lip, step=step, name="губка").value == 0.0
    if step == 0.5:
        # При шаге 1 мм кромка 0.8 мм укладывается в одну ячейку досягаемости
        # правила 45°, и растр честно говорит «держится».
        assert support_volume(box_with_lip, step=step, name="губка").value > 0

def test_a_long_bridge_fails_and_says_why():
    result = bridge_span(_bridge(), max_span=10.0, step=0.5, name="мост")
    assert result.status == "fail" and "провиснет" in result.message


# ------------------------------------------------------------- на живом проекте
def test_the_ported_spool_needs_no_supports():
    """Независимое подтверждение записанного в корпусе: «Уклон и люльки без
    поддержек». Оригинал утверждал это рассуждением, здесь это число."""
    import pathlib

    from datum.doc.project import Project

    root = pathlib.Path(__file__).resolve().parents[3] / "examples" / "spool"
    solid = Project.open(root).build()["подставка"].solid
    assert support_volume(solid, step=1.0, name="подставка").value == 0.0
    assert bridge_span(solid, step=1.0, name="подставка").value == 0.0


# ------------------------------------------------------------------- капля
def _plate_with_hole(profile):
    """Пластина 20×10×20 (X×Y×Z) с горизонтальным отверстием Ø6 вдоль Y."""
    from datum.geom.prims import prism_xz

    pts = list(profile.exterior.coords)[:-1]
    return diff(box(0, 20, 0, 10, 0, 20), [prism_xz(pts, -1, 11)])


def test_a_teardrop_hole_needs_no_support_where_a_round_one_does():
    """Ради этого капля и существует: горизонтальное отверстие без опор.

    Капля в профиле XZ носиком в +Z — носик смотрит вверх при печати как
    построено. Круглое отверстие того же диаметра требует опор под свод.
    """
    from shapely.geometry import Point

    from datum.geom.profile2d import teardrop_poly

    round_ = _plate_with_hole(Point(10, 10).buffer(3, quad_segs=24))
    drop = _plate_with_hole(teardrop_poly(6, cx=10, cy=10, nose="+y"))
    assert support_volume(round_, step=0.25).value > 1.0
    assert support_volume(drop, step=0.25).value == 0.0
    # Носик вниз — капля бесполезна: свод снова висит. Направление носика и
    # есть смысл капли, и перепутать его легко.
    down = _plate_with_hole(teardrop_poly(6, cx=10, cy=10, nose="-y"))
    assert support_volume(down, step=0.25).value > 1.0


def test_a_teardrop_is_a_circle_plus_a_square_corner():
    """¾ круга и квадрат r×r со стороны носика; носик на r·√2 от центра."""
    import math

    from datum.geom.profile2d import teardrop_poly

    r = 3.0
    for nose, (ux, uy) in {"+y": (0, 1), "-y": (0, -1), "+x": (1, 0), "-x": (-1, 0)}.items():
        p = teardrop_poly(2 * r, cx=1.0, cy=-2.0, nose=nose, n=720)
        assert p.area == pytest.approx(0.75 * math.pi * r * r + r * r, rel=1e-4)
        far = max(p.exterior.coords, key=lambda c: (c[0] - 1.0) * ux + (c[1] + 2.0) * uy)
        assert far[0] == pytest.approx(1.0 + ux * r * math.sqrt(2))
        assert far[1] == pytest.approx(-2.0 + uy * r * math.sqrt(2))
    with pytest.raises(ValueError, match="nose"):
        teardrop_poly(6, nose="вверх")


def test_under_a_gentle_vault_only_the_per_layer_strip_hangs():
    """Горизонтальное отверстие Ø6: правило 45° послойно даёт в пределе
    ∫(1 − s)·2√(r² − x²) по зоне |x| < r/√2, т. е. 141.4 мм³ на длину 10, — а
    не 231.4 под всей зоной. Растр сходится к первому, снизу, с ошибкой первого
    порядка: на этом замере и держится описание в docstring."""
    from shapely.geometry import Point

    solid = _plate_with_hole(Point(10, 10).buffer(3, quad_segs=64))
    got = [support_volume(solid, step=s).value for s in (0.25, 0.125)]
    assert got[0] < got[1] < 141.4
    err = [141.4 - g for g in got]
    assert 1.6 < err[0] / err[1] < 2.4                     # ошибка ∝ шагу


# ------------------------------------------------ ось вверх — одна для всех
def test_every_printing_check_that_takes_up_understands_axis_names():
    """Проект подставляет `print_up` детали в КАЖДУЮ проверку с аргументом `up`.

    Однажды это сломалось: у `overhang` `up` был вектором, и строка «+Z»
    уронила проверку подставки. Поэтому набор таких проверок перечислен здесь
    явно, а новая проверка с `up` заставит этот тест вспомнить о себе.
    """
    import inspect

    from datum.checks.library import LIBRARY

    takes_up = {n for n, e in LIBRARY.items()
                if "up" in inspect.signature(e.fn).parameters}
    assert takes_up == {"bed_contact", "support_volume", "bridge_span",
                        "overhang", "bbox_fits", "min_wall", "layer_strength"}
    brick = box(0, 10, 0, 20, 0, 5)
    # У `layer_strength` есть обязательные аргументы — сила и предел.
    extra = {"layer_strength": {"force": 10.0, "strength_z": 30.0}}
    for name in sorted(takes_up):
        fn = LIBRARY[name].fn
        results = {up: fn(brick, up=up, **extra.get(name, {})) for up in ("+Z", "-X")}
        assert all("вверх" in r.message for r in results.values()), name


def test_bbox_fits_measures_the_printing_pose():
    """Брусок 10×20×240: стоймя в стол 256 входит, а на столе высотой 100 —
    только лёжа."""
    from datum.checks.printing import bbox_fits, overhang

    bar = box(0, 10, 0, 20, 0, 240)
    assert bbox_fits(bar, bed=(256, 256, 100)).status == "fail"
    lying = bbox_fits(bar, bed=(256, 256, 100), up="+X")
    assert lying.status == "pass" and "240.0×20.0×10.0" in lying.message
    # Строка и вектор — одна и та же ось.
    assert overhang(bar, up="-Y").value == overhang(bar, up=(0, -1, 0)).value



def test_an_overhang_limit_stricter_than_the_grid_can_tell_is_refused():
    """30° раньше молча считались как 45° — под подписью «правило 30°»."""
    with pytest.raises(ValueError, match="строже 45"):
        overhanging(voxelize(box(0, 10, 0, 10, 0, 10), 2.0), 30.0)
