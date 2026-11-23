"""Проверки удержания. Главный тест этапа 1.

Геометрия: карман в корпусе, сверху губка, которая нависает над деталью на L мм
с каждой стороны. Деталь построена с печатным зазором g со всех сторон.

Тогда «зацеп» — это (L − g): насколько губка ещё перекрывает деталь ПОСЛЕ того,
как деталь выбрала свой зазор. Ровно эта величина в NOTES.md записана для v3 как
«0.8 − 0.57 = 0.23», и ровно её не видела проверка check3.
"""

from __future__ import annotations

import pytest

from datum.checks.motion import direction, escape, naive_touch, stop_contact, travel_sweep
from datum.checks.result import CheckRun
from datum.geom import box, diff, move, union


def holder_with_lip(L: float):
    """Корпус: карман 5..35 × 5..15 × 2..10, сверху губка глубиной L."""
    block = box(0, 40, 0, 20, 0, 14)
    cavity = box(5, 35, 5, 15, 2, 10)            # сам карман
    opening = box(5 + L, 35 - L, 5, 15, 10, 14)  # проём сверху уже кармана на L
    return diff(block, [cavity, opening]).renamed("корпус")


def part_with_gap(g: float):
    """Деталь в кармане с печатным зазором g со всех сторон."""
    return box(5 + g, 35 - g, 5 + g, 15 - g, 2 + g, 10 - g).renamed("шторка")


# ------------------------------------------------------------------ направления
def test_direction_parsing():
    assert tuple(direction("+z")) == (0, 0, 1)
    assert tuple(direction("-X")) == (-1, 0, 0)
    assert tuple(direction((0, 0, 2))) == (0, 0, 1), "вектор должен нормироваться"
    with pytest.raises(ValueError):
        direction("вверх")
    with pytest.raises(ValueError):
        direction((0, 0, 0))


# ------------------------------------------------------------------ escape
def test_escape_passes_with_real_engagement():
    """Губка 1.5 мм при зазоре 0.3 → зацеп 1.2 мм, деталь держится."""
    res = escape(part_with_gap(0.3), holder_with_lip(1.5), ["+Z"], gap=0.3)
    assert len(res) == 1
    r = res[0]
    assert r.status == "pass", r.message
    assert r.value > 10.0, f"зацеп должен быть заметным, получено {r.value}"


def test_escape_catches_engagement_smaller_than_the_gap():
    """Губка 0.32 мм при зазоре 0.3 → зацеп 0.02 мм. Деталь выпадет.

    Это и есть класс отказа v3: зацеп меньше того, что съедает печатный зазор.
    """
    r = escape(part_with_gap(0.3), holder_with_lip(0.32), ["+Z"], gap=0.3)[0]
    assert r.status == "fail"
    assert "НЕ ДЕРЖИТ" in r.message
    assert r.value < 1.0
    assert r.margin < 0


def test_escape_catches_a_jammed_part():
    """Деталь построена без зазора (номинально) — она не сможет его выбрать."""
    nominal = box(5, 35, 5, 15, 2, 10).renamed("шторка")
    r = escape(nominal, holder_with_lip(1.5), ["+Z"], gap=0.4)[0]
    assert r.status == "fail"
    assert "КЛИНИТ" in r.message


def test_escape_reports_every_direction_separately():
    res = escape(part_with_gap(0.3), holder_with_lip(1.5), ["+Z", "-Z", "+X"], gap=0.3)
    assert [r.id.rsplit(".", 1)[1] for r in res] == ["+Z", "-Z", "+X"]
    assert all(r.unit == "мм³" and r.op == ">=" for r in res)


def test_escape_evidence_carries_both_measurements():
    r = escape(part_with_gap(0.3), holder_with_lip(1.5), ["+Z"], gap=0.3)[0]
    data = r.evidence[0].data
    assert data["free_shift_mm"] == pytest.approx(0.25)
    assert data["hold_shift_mm"] == pytest.approx(1.3)
    assert data["free_vol_mm3"] < 0.2


# ------------------------------------------------------------------ устаревшая проверка
def test_naive_touch_is_fooled_by_nominal_fit():
    """Деталь, построенная без зазора, «держится» во все стороны — это ложь.

    Именно так check3 и пропустил v3: номинальная посадка даёт перекрытие при
    любом сдвиге, и проверка по касанию считает это удержанием.
    """
    nominal = box(5, 35, 5, 15, 2, 10).renamed("шторка")
    holder = holder_with_lip(0.32)      # зацепа по сути нет
    naive = CheckRun(tuple(naive_touch(nominal, holder, ["+X", "-X", "+Y", "-Y"])))
    assert naive.ok, "проверка по касанию обязана обмануться — в этом её порок"

    strict = CheckRun(tuple(escape(part_with_gap(0.3), holder, ["+Z"], gap=0.3)))
    assert not strict.ok, "а зацеп после зазора обязан поймать отказ"


def test_naive_touch_is_marked_as_informational():
    r = naive_touch(box(0, 1, 0, 1, 0, 1), box(0, 1, 0, 1, 0, 1), ["+Z"])[0]
    assert r.severity == "info"
    assert "устарела" in r.message


# ------------------------------------------------------------------ развёртка хода
def test_travel_sweep_finds_the_worst_position():
    """Препятствие стоит посередине хода — развёртка обязана его найти."""
    obstacle = box(9.5, 10.5, 0, 10, 0, 10)

    def build(u: float):
        return move(box(0, 2, 2, 8, 2, 8), 20.0 * u, 0, 0)

    r = travel_sweep(build, [obstacle], samples=9, max_inter_vol=0.05)
    assert r.status == "fail"
    assert 0.4 < r.evidence[0].data["worst_u"] < 0.6, "худшая точка должна быть в середине"
    assert r.value > 1.0


def test_travel_sweep_passes_when_the_path_is_clear():
    obstacle = box(9.5, 10.5, 50, 60, 0, 10)      # в стороне

    def build(u: float):
        return move(box(0, 2, 2, 8, 2, 8), 20.0 * u, 0, 0)

    assert travel_sweep(build, [obstacle], samples=9).status == "pass"


def test_stop_contact():
    stop = box(20, 24, 0, 10, 0, 10)

    def build(u: float):
        return move(box(0, 2, 2, 8, 2, 8), 20.0 * u, 0, 0)

    assert stop_contact(build, stop, u_over=1.05).status == "pass"
    assert stop_contact(build, stop, u_over=0.5).status == "fail"


# ---------------------------------------------- узкое задевание между узлами
def test_travel_sweep_catches_a_rib_between_grid_nodes():
    """Ребро 0.5 мм поперёк 40-миллиметрового хода, между узлами грубой сетки.

    Прежняя развёртка брала 9 или 13 узлов, на всех видела ноль и уточняла
    вокруг u=0 — то есть говорила «ход свободен». Теперь шаг берётся из
    сдвига детали, и ребро не проскакивает при любом начальном числе узлов.
    """
    from datum.geom import box, move

    cube = box(0, 1, 0, 1, 0, 1).renamed("кубик")
    rib = box(22.3, 22.8, -5, 5, 0, 1)
    for n in (2, 9, 13):
        r = travel_sweep(lambda u: move(cube, dx=u), [rib], u_range=(0, 40),
                         samples=n, max_inter_vol=0.05)
        assert r.status == "fail", n
        assert r.value == pytest.approx(0.5, abs=1e-6)
        assert "кубик" in r.message


def test_travel_sweep_measures_rotation_by_the_fastest_point():
    """Ход в градусах: шаг меряется по хорде у дальней точки, а не по углу."""
    from datum.geom import box
    from datum.geom.xform import rotate

    arm = box(0, 20, -0.5, 0.5, 0, 1).renamed("рычаг")
    post = box(9.5, 10.5, 9.5, 10.5, 0, 1)            # на полпути поворота
    r = travel_sweep(lambda a: rotate(arm, rz=a), [post], u_range=(0, 90), samples=3)
    assert r.status == "fail" and r.value > 0.5


def test_travel_sweep_says_so_when_it_cannot_measure_the_motion():
    """Деталь меняет топологию от хода — сдвиг вершин не сопоставить, и
    развёртка честно переходит на равномерную сетку."""
    from datum.geom import box, cyl_z, diff

    def part(u):
        b = box(0, 1 + u, 0, 1, 0, 1)
        if u > 10:
            b = diff(b, [cyl_z(0.4, -1, 2, 0.5 + u / 2, 0.5)])
        return b

    r = travel_sweep(part, [box(30, 31, 0, 1, 0, 1)], u_range=(0, 40), samples=5)
    assert r.status == "fail"
    assert "измерить нельзя" in r.message and r.evidence[0].data["blind"]


def test_travel_sweep_refuses_an_empty_range():
    from datum.geom import box

    with pytest.raises(ValueError, match="u_range"):
        travel_sweep(lambda u: box(0, 1, 0, 1, 0, 1), [], u_range=(1, 1))
