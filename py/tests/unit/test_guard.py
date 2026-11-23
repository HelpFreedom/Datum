"""Страж CSG: контрольные точки, поиск виновной операции, сохранение ленивости."""

from __future__ import annotations

import time

import numpy as np
import pytest
from manifold3d import Manifold as M
from manifold3d import Mesh

from datum.geom.guard import Expect, GuardError, GuardMode, checkpoint, locate_failure
from datum.geom.solid import Solid


def S(man: M, name: str = "", op: str = "prim") -> Solid:
    return Solid.wrap(man, name=name, op=op)


def nan_solid() -> Solid:
    return S(M(Mesh(
        vert_properties=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, np.nan]], dtype=np.float32),
        tri_verts=np.array([[0, 1, 2], [0, 1, 3], [1, 2, 3], [0, 2, 3]], dtype=np.uint32),
    )), name="битое", op="from_mesh")


# --------------------------------------------------------------- норма
def test_clean_solid_passes():
    rep = checkpoint(S(M.cube((10, 10, 10)), "куб"))
    assert rep.bodies == 1 and rep.sealed_voids == 0


def test_through_hole_is_allowed_by_default():
    """Сквозное отверстие — норма, страж не должен на него ругаться."""
    body = S(M.cube((10, 10, 10)), "пластина") - M.cylinder(20, 2.0, circular_segments=48).translate((5, 5, -5))
    rep = checkpoint(body)
    assert rep.genus == 1 and rep.bodies == 1


# --------------------------------------------------------------- дефекты
def test_sealed_void_is_rejected():
    body = S(M.cube((10, 10, 10)), "корпус") - M.cube((4, 4, 4)).translate((3, 3, 3))
    with pytest.raises(GuardError) as e:
        checkpoint(body)
    assert e.value.reason == "sealed_void"
    assert "нечем заполнить" in e.value.message
    assert e.value.report.void_volumes[0] == pytest.approx(-64.0)


def test_sealed_void_can_be_declared_intentional():
    body = S(M.cube((10, 10, 10))) - M.cube((4, 4, 4)).translate((3, 3, 3))
    rep = checkpoint(body, Expect(allow_sealed_voids=True))
    assert rep.sealed_voids == 1


def test_part_falling_apart_is_rejected():
    body = S(M.cube((10, 10, 10)), "деталь") + M.cube((10, 10, 10)).translate((40, 0, 0))
    with pytest.raises(GuardError) as e:
        checkpoint(body)
    assert e.value.reason == "body_count"
    assert "распалась" in e.value.message
    assert e.value.value == 2 and e.value.threshold == 1


def test_sliver_is_rejected():
    body = S(M.cube((10, 10, 10)), "деталь") + M.cube((0.02, 0.02, 0.02)).translate((40, 0, 0))
    with pytest.raises(GuardError) as e:
        checkpoint(body)
    assert e.value.reason == "slivers"


def test_everything_cut_away_is_rejected():
    body = S(M.cube((2, 2, 2)), "мелочь") - M.cube((20, 20, 20)).translate((-9, -9, -9))
    with pytest.raises(GuardError) as e:
        checkpoint(body)
    assert e.value.reason == "empty"


def test_guard_off_never_raises():
    body = S(M.cube((10, 10, 10))) - M.cube((4, 4, 4)).translate((3, 3, 3))
    rep = checkpoint(body, mode=GuardMode.OFF)
    assert rep.sealed_voids == 1


# --------------------------------------------------------------- поиск виновного
@pytest.mark.parametrize("bad_at", [0, 1, 7, 19])
def test_locate_failure_names_the_exact_operation(bad_at):
    """Журнал должен указать на ТУ САМУЮ операцию, где всё сломалось."""
    s = S(M.cube((10, 10, 10)), "деталь", op="cube")
    for i in range(20):
        if i == bad_at:
            s = s + nan_solid()                       # порча ровно здесь
        else:
            s = s - M.cube((1, 1, 1)).translate((i * 0.4, 0, 9.5))

    with pytest.raises(GuardError) as e:
        checkpoint(s)
    assert e.value.reason == "manifold_error"
    assert e.value.culprit is not None, "страж обязан назвать виновную операцию"
    # +1: нулевая запись журнала — само создание куба. Виновник — битое тело
    # (его запись встала в журнал перед объединением), а не объединение с ним.
    assert e.value.culprit.index == bad_at + 1, (
        f"указано на операцию #{e.value.culprit.index}, а порча была на #{bad_at + 1}")
    assert e.value.culprit.op == "from_mesh"


def test_locate_failure_finds_the_fault_in_the_shorter_branch():
    """Журнал булевой — из ОБЕИХ ветвей. Прежде брался только длинный, и
    схлопывание в короткой ветви бисекция сваливала на последнюю операцию."""
    long = S(M.cube((10, 10, 10)), "корпус", op="cube")
    for i in range(20):
        long = long - M.cube((1, 1, 1)).translate((i * 0.4, 0, 9.5))
    short = S(M.cube((10, 10, 10)), "вставка", op="cube")
    short = short - M.cube((30, 30, 30)).translate((-10, -10, -10))   # срезано всё
    short = short - M.cube((1, 1, 1))
    body = long ^ short

    with pytest.raises(GuardError) as e:
        checkpoint(body)
    assert e.value.reason == "empty"
    culprit = e.value.culprit
    assert culprit is not None and culprit.op == "difference"
    assert body.journal[culprit.index].result is short.journal[1].result


def test_the_journal_still_bounds_memory():
    """Родители — слабые ссылки: запись, выпавшая из обрезанного журнала,
    освобождается, как и до связей между записями."""
    import gc
    import weakref

    from datum.geom.solid import MAX_JOURNAL

    s = S(M.cube((1, 1, 1)), "тело", op="cube")
    first = weakref.ref(s.journal[0])
    for _ in range(MAX_JOURNAL + 10):
        s = s.derive(s.manifold, "noop")
    gc.collect()
    assert len(s.journal) == MAX_JOURNAL and first() is None


def test_the_journal_keeps_both_branches_without_repeating_ancestors():
    a = S(M.cube((10, 10, 10)), "a", op="cube")
    b = a.derive(a.manifold.translate((5, 0, 0)), "move")
    c = a - b
    assert [r.op for r in c.journal] == ["cube", "move", "difference"]
    assert c.journal[-1].parents == (a.journal[-1], b.journal[-1])


def test_locate_failure_returns_none_when_no_hard_error():
    """Мягкий отказ (полость) не должен выдумывать виновную операцию."""
    s = S(M.cube((10, 10, 10))) - M.cube((4, 4, 4)).translate((3, 3, 3))
    assert locate_failure(s, Expect()) is None


# --------------------------------------------------------------- цена
def test_building_a_chain_stays_lazy():
    """Основание всей конструкции: сборка цепочки не должна ничего форсировать.

    Если кто-то добавит запрос свойства в Solid или в оператор, этот тест
    упадёт — и правильно сделает, потому что цена вырастет в разы.
    """
    N = 168
    t0 = time.perf_counter()
    s = S(M.cylinder(15.0, 20.0, circular_segments=128), "тело", op="cylinder")
    cut = M.cube((1.2, 3.0, 17.0)).translate((-0.6, 18.5, -1.0))
    for i in range(N):
        s = s - cut.rotate((0, 0, i * 360.0 / N))
    build_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    checkpoint(s, Expect(bodies=None))
    force_ms = (time.perf_counter() - t1) * 1000

    assert len(s.journal) == N + 1
    assert build_ms < force_ms, (
        f"сборка {build_ms:.1f} мс не должна быть дороже расчёта {force_ms:.1f} мс — "
        f"похоже, ленивость потеряна")
    assert build_ms < 50, f"сборка цепочки из {N} операций заняла {build_ms:.1f} мс — слишком долго"


# ------------------------------------------------------------ где полость
def _two_voids() -> Solid:
    from datum.geom import box, diff

    return diff(box(0, 20, 0, 20, 0, 10), [box(3, 7, 3, 7, 3, 4), box(12, 15, 12, 16, 2, 8)])


def test_the_refusal_says_where_each_void_is():
    """Прежде отказ называл только объёмы, и каждую из пяти полостей
    подставки агент искал отдельной серией сечений."""
    with pytest.raises(GuardError) as caught:
        checkpoint(_two_voids())
    text = str(caught.value)
    # Крупнейшая первой — с центром и габаритом.
    assert "72 мм³ у (13.50, 14.00, 5.00), габарит 3.00×4.00×6.00 мм" in text
    assert "16 мм³ у (5.00, 5.00, 3.50), габарит 4.00×4.00×1.00 мм" in text
    # Срез поперёк самого тонкого размера крупнейшей: X шириной 3 мм.
    assert "section(plane=yz, at=13.50)" in text


def test_the_place_does_not_enter_the_stored_metrics():
    """Кеш хранит деталь без положения: место из него было бы не там."""
    from datum.geom.topology import analyze

    rep = analyze(_two_voids().manifold)
    assert len(rep.void_places) == 2
    assert "void_places" not in rep.as_dict(round_to=None)


def test_many_voids_are_listed_by_count():
    from datum.geom import box, diff

    holes = [box(2 + 3 * i, 3 + 3 * i, 2, 3, 2, 3) for i in range(5)]
    with pytest.raises(GuardError, match="и ещё 2"):
        checkpoint(diff(box(0, 20, 0, 6, 0, 6), holes))
