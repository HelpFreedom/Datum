"""Диагностика и ремонт чужих мешей."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from datum.geom.repair import diagnose, repair, voxel_remesh


@pytest.fixture(scope="module")
def sphere():
    return trimesh.creation.icosphere(subdivisions=4, radius=10.0)


def holed(mesh: trimesh.Trimesh, frac: float = 0.10, seed: int = 0) -> trimesh.Trimesh:
    """Выкусить долю граней — получится дырявый меш."""
    m = mesh.copy()
    rng = np.random.default_rng(seed)
    keep = np.ones(len(m.faces), bool)
    keep[rng.choice(len(m.faces), int(len(m.faces) * frac), replace=False)] = False
    m.update_faces(keep)
    return m


# ------------------------------------------------------------------ диагноз
def test_healthy_mesh_is_reported_healthy(sphere):
    d = diagnose(sphere)
    assert d.healthy and d.watertight and d.components == 1
    assert d.problems() == []
    assert d.volume_mm3 == pytest.approx(4 / 3 * np.pi * 1000, rel=0.02)


def test_holed_mesh_problems_are_named(sphere):
    d = diagnose(holed(sphere))
    assert not d.healthy and not d.watertight
    assert d.boundary_edges > 0
    assert any("не замкнут" in p for p in d.problems())


def test_nonmanifold_edges_are_distinguished_from_holes():
    """Незамкнутость бывает двух разных видов, и путать их нельзя.

    На реальном mech_arm.3mf было 0 открытых рёбер и 92 760 неманифольдных;
    первая версия диагностики выдавала самопротиворечие «не замкнут: открытых
    рёбер 0».
    """
    # Дыра: у грани нет пары → открытые рёбра.
    holed_box = trimesh.creation.box((10, 10, 10))
    keep = np.ones(len(holed_box.faces), bool); keep[0] = False
    holed_box.update_faces(keep)
    d = diagnose(holed_box)
    assert d.boundary_edges > 0 and d.nonmanifold_edges == 0
    assert any("дыры" in p for p in d.problems())

    # Неманифольдность: третья грань на существующем ребре.
    nm = trimesh.creation.box((10, 10, 10))
    extra = np.array([[nm.faces[0][0], nm.faces[0][1], len(nm.vertices)]])
    nm.vertices = np.vstack([nm.vertices, [[20.0, 20.0, 20.0]]])
    nm.faces = np.vstack([nm.faces, extra])
    d2 = diagnose(nm)
    assert d2.nonmanifold_edges > 0, "третья грань на ребре должна быть замечена"
    assert any("неманифольдных" in p for p in d2.problems())


def test_components_use_vertex_connectivity():
    """Счёт кусков — по вершинам, а не по смежности граней.

    На mech_arm.3mf счёт по граням давал 123 692 «куска» (и 13.6 с), потому что
    смежность рассыпается на неманифольдных рёбрах; по вершинам — 15 тел за 0.07 с.
    """
    a = trimesh.creation.box((10, 10, 10))
    b = trimesh.creation.box((10, 10, 10)); b.apply_translation((30, 0, 0))
    assert diagnose(trimesh.util.concatenate([a, b])).components == 2


def test_units_suspect_is_flagged():
    tiny = trimesh.creation.box((0.01, 0.01, 0.01))     # похоже на метры
    assert diagnose(tiny).units_suspect
    assert not diagnose(trimesh.creation.box((10, 10, 10))).units_suspect


def test_duplicate_and_degenerate_faces_are_counted(sphere):
    m = sphere.copy()
    m.faces = np.vstack([m.faces, m.faces[:5]])          # дубли
    assert diagnose(m).duplicate_faces >= 5


# ------------------------------------------------------------------ лёгкий ремонт
def test_repair_of_a_healthy_mesh_changes_nothing(sphere):
    r = repair(sphere)
    assert r.ok and r.after.watertight
    assert r.after.volume_mm3 == pytest.approx(r.before.volume_mm3, rel=1e-9)
    assert not any(s.op == "перестройка" for s in r.steps)


def test_small_holes_are_filled_without_remesh(sphere):
    r = repair(holed(sphere, frac=0.005))
    assert r.ok, r.report()
    assert any(s.op == "дыры" for s in r.steps)
    assert not any(s.op == "перестройка" for s in r.steps), \
        "мелкие дыры должны закрываться без перестройки"
    assert r.after.volume_mm3 == pytest.approx(r.before.volume_mm3 or 4188, rel=0.05)


def test_inverted_mesh_is_turned_right_side_out(sphere):
    m = sphere.copy()
    m.invert()
    r = repair(m)
    assert r.ok
    assert r.after.volume_mm3 > 0


def test_loose_debris_is_dropped(sphere):
    debris = trimesh.creation.box((0.5, 0.5, 0.5))
    debris.apply_translation((40, 0, 0))
    m = trimesh.util.concatenate([sphere.copy(), debris])
    assert diagnose(m).components == 2
    r = repair(m, keep_largest=True)
    assert r.after.components == 1
    assert any(s.op == "тела" for s in r.steps)


# ------------------------------------------------------------------ перестройка
def test_voxel_remesh_closes_a_badly_holed_mesh(sphere):
    """10 % выкушенных граней — дыры слишком велики для заделки."""
    bad = holed(sphere, frac=0.10)
    out = voxel_remesh(bad, pitch=0.5, close_k=3)
    assert out.is_watertight, "перестройка обязана дать замкнутый меш"
    assert len(out.split(only_watertight=False)) == 1, "и одно тело"
    # Перестройка систематически РАЗДУВАЕТ тело примерно на полвокселя наружу:
    # изоповерхность идёт по границе размеченных вокселей. Замерено на шаре
    # r=10: +20 % при шаге 1.0 мм, +10 % при 0.5, +5 % при 0.25 — ровно вдвое с
    # шагом. Поэтому проверяется НАПРАВЛЕНИЕ и ПОРЯДОК смещения, а не близость
    # к эталону: допуск, выставленный вплотную к измеренному, ломается от
    # любого изменения и ничего не объясняет.
    ratio = out.volume / sphere.volume
    assert 1.0 < ratio < 1.15, (
        f"объём {out.volume:.0f} против эталона {sphere.volume:.0f} "
        f"(отношение {ratio:.3f}); ожидалось раздутие на полвокселя")


def test_repair_auto_escalates_to_remesh(sphere):
    r = repair(holed(sphere, frac=0.10), level="auto", pitch=0.5, close_k=3)
    assert r.ok, r.report()
    assert any(s.op == "перестройка" for s in r.steps), \
        "при больших дырах лестница обязана дойти до перестройки"
    assert r.after.volume_mm3 == pytest.approx(sphere.volume, rel=0.12)


def test_remesh_without_closing_is_worse(sphere):
    """Обоснование close_k: без замыкания заливка утекает через дыры.

    Замер показал 19 % объёма при шаге 0.5 без замыкания против ~100 % с ним.
    """
    bad = holed(sphere, frac=0.10)
    leaky = voxel_remesh(bad, pitch=0.5, close_k=0)
    solid = voxel_remesh(bad, pitch=0.5, close_k=3)
    assert solid.volume > leaky.volume * 2, \
        f"с замыканием {solid.volume:.0f}, без него {leaky.volume:.0f} — разницы нет"


# ------------------------------------------------------------------ журнал
def test_journal_records_what_was_done(sphere):
    r = repair(holed(sphere, frac=0.10), pitch=0.5, close_k=3)
    assert r.steps, "журнал не должен быть пустым"
    assert all(s.ms >= 0 for s in r.steps)
    d = r.as_dict()
    assert d["ok"] and d["steps"] and "before" in d and "after" in d
    txt = r.report()
    assert "Было:" in txt and "Сделано:" in txt and "Стало:" in txt


def test_repair_returns_a_usable_solid(sphere):
    from datum.geom.guard import Expect, checkpoint

    r = repair(holed(sphere, frac=0.10), pitch=0.5, close_k=3)
    rep = checkpoint(r.solid, Expect(bodies=1))
    assert rep.bodies == 1 and rep.sealed_voids == 0


# ------------------------------------------------------- своя растеризация
class TestRasterize:
    """Растеризация поверхности в воксели.

    Почему своя, а не `mesh.voxelized()`: trimesh по умолчанию рекурсивно
    четвертит ВСЕ грани, пока самая крупная не станет мельче вокселя. Цену
    задаёт одна крупная грань, а платят её все. На настоящей модели с
    Thingiverse (17 тысяч граней, одна грань 4020 мм²) это семь четвертований,
    276 миллионов граней и 3.5 ГБ — и ремонт не проходил в отдельном процессе,
    упираясь в предел памяти. Здесь каждая грань дробится по своему размеру.
    """

    def test_a_box_is_rasterized_into_its_own_bounds(self):
        from datum.geom.repair import rasterize_surface

        mesh = trimesh.creation.box(extents=(20, 10, 6))
        occ, origin = rasterize_surface(mesh, 0.5, pad=2)

        cells = np.argwhere(occ)
        low = origin + cells.min(axis=0) * 0.5
        high = origin + cells.max(axis=0) * 0.5
        assert np.allclose(low, mesh.bounds[0], atol=0.75)
        assert np.allclose(high, mesh.bounds[1], atol=0.75)

    def test_the_shell_is_closed(self):
        """Дырявая оболочка — главная беда растеризации: заливка через дыру
        вытечет наружу и съест деталь целиком. Проверяем, что залитое тело
        осталось внутри габарита, а не расползлось на всю решётку."""
        from scipy import ndimage

        from datum.geom.repair import rasterize_surface

        mesh = trimesh.creation.box(extents=(20, 10, 6))
        occ, _ = rasterize_surface(mesh, 0.4, pad=3)
        filled = ndimage.binary_fill_holes(occ)

        # Внутренность должна залиться, а внешние слои — остаться пустыми.
        assert filled.sum() > occ.sum() * 1.5, "заливка не сработала: оболочка дырявая"
        assert not filled[0].any() and not filled[-1].any(), "заливка вытекла наружу"

    def test_one_huge_face_does_not_cost_the_whole_mesh(self):
        """Именной замок на ту самую беду. Меш из множества мелких граней и
        одной большой: цена обязана определяться суммарной площадью, а не
        самой крупной гранью."""
        from datum.geom.repair import rasterize_surface

        small = trimesh.creation.box(extents=(4, 4, 4))
        big = trimesh.Trimesh(
            vertices=np.array([[0, 0, 20.0], [300, 0, 20.0], [0, 300, 20.0]]),
            faces=np.array([[0, 1, 2]]), process=False)
        mesh = trimesh.util.concatenate([small, big])

        occ, _ = rasterize_surface(mesh, 0.5, pad=2)
        assert occ.any()
        # Решётка определяется габаритом, и ничего более крупного не заводится.
        assert occ.size < 60_000_000, f"решётка разрослась: {occ.shape}"

    def test_a_degenerate_face_is_not_a_crash(self):
        """У вырожденной грани все рёбра нулевые: деление на число шагов
        обязано не превратиться в деление на ноль."""
        from datum.geom.repair import rasterize_surface

        mesh = trimesh.Trimesh(
            vertices=np.array([[0.0, 0, 0], [0, 0, 0], [0, 0, 0], [1, 0, 0]]),
            faces=np.array([[0, 1, 2]]), process=False)
        occ, _ = rasterize_surface(mesh, 0.5, pad=1)
        assert occ.sum() >= 1

    def test_sampling_is_fine_enough_for_the_grid(self):
        """Шаг выборки — половина вокселя. На наклонной грани это значит, что
        ни один воксель по её пути не пропущен: иначе оболочка дырявая, и
        заливка вытекает."""
        from datum.geom.repair import rasterize_surface

        # Наклонный треугольник: худший случай для растеризации по решётке.
        mesh = trimesh.Trimesh(
            vertices=np.array([[0.0, 0, 0], [30, 7, 0], [15, 3.5, 20]]),
            faces=np.array([[0, 1, 2]]), process=False)
        pitch = 0.5
        occ, origin = rasterize_surface(mesh, pitch, pad=1)

        # Идём по грани независимой мелкой выборкой и проверяем, что каждая
        # точка попала в размеченный воксель.
        v = np.asarray(mesh.vertices)
        rng = np.random.default_rng(1)
        u = rng.random(4000)
        w = rng.random(4000)
        outside = u + w > 1
        u[outside], w[outside] = 1 - u[outside], 1 - w[outside]
        points = v[0] + u[:, None] * (v[1] - v[0]) + w[:, None] * (v[2] - v[0])
        cells = np.floor((points - origin + pitch * 0.5) / pitch).astype(int)
        hit = occ[cells[:, 0], cells[:, 1], cells[:, 2]]
        assert hit.all(), f"пропущено вокселей: {(~hit).sum()} из {len(hit)}"


    def test_the_margin_is_what_it_claims(self):
        """Отступ обязан быть НЕ ТОНЬШЕ заявленного с каждой стороны.

        Мина, на которой это и поймали: при ровном `pad * pitch` индекс крайней
        вершины считается как floor(3.0), а на числах с плавающей точкой это
        иногда 2.9999… → 2. Отступ выходил на воксель тоньше, и расширение
        перед заливкой упиралось в край решётки — заливке негде отличить
        наружное от внутреннего.
        """
        from datum.geom.repair import rasterize_surface

        for extent in ((20, 10, 6), (7.3, 11.9, 3.1), (100, 2, 2)):
            mesh = trimesh.creation.box(extents=extent)
            for pad in (1, 3, 5):
                occ, _ = rasterize_surface(mesh, 0.5, pad=pad)
                cells = np.argwhere(occ)
                below = cells.min(axis=0)
                above = np.array(occ.shape) - 1 - cells.max(axis=0)
                assert (below >= pad).all(), f"{extent}, pad={pad}: снизу {below}"
                assert (above >= pad).all(), f"{extent}, pad={pad}: сверху {above}"

    def test_the_fill_never_reaches_the_grid_edge(self):
        """Итоговое тело обязано остаться внутри решётки. Дошло до края —
        значит заливка вытекла наружу и съела деталь целиком."""
        from scipy import ndimage

        from datum.geom.repair import rasterize_surface

        mesh = trimesh.creation.icosphere(subdivisions=3, radius=8)
        occ, _ = rasterize_surface(mesh, 0.5, pad=3)
        st = ndimage.generate_binary_structure(3, 1)
        solid = ndimage.binary_erosion(
            ndimage.binary_fill_holes(ndimage.binary_dilation(occ, st, iterations=2)),
            st, iterations=2)
        touching = sum(int(face.sum()) for face in
                       (solid[0], solid[-1], solid[:, 0], solid[:, -1],
                        solid[:, :, 0], solid[:, :, -1]))
        assert touching == 0
        # И само тело осталось телом, а не исчезло.
        assert solid.sum() > occ.sum()
