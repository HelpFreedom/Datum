"""Тетраэдральная сетка произвольного тела — претензия №8, часть вторая.

До сих пор решатель брал только призматический брус. Здесь проверяется, что
сетке ПРОИЗВОЛЬНОГО тела можно доверять ровно так же — а доверять есть чему
только тогда, когда она проходит эталон с точным ответом.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from datum.fem import solve as S
from datum.fem.model import Bar, FemError, Material
from datum.fem.tetmesh import TetMesh, mesher_available, tet_mesh
from datum.fem.verify import TOL_PATCH, patch_test
from datum.geom.implicit import blend_union, cyl_z_sdf, mesh_sdf, sphere_sdf
from datum.geom.measure import vol
from datum.geom.prims import box

pytestmark = pytest.mark.skipif(not mesher_available(),
                                reason="мешер fTetWild не установлен")

L, B, T = 40.0, 10.0, 2.0
PLA = Material(2000.0, 0.35, "проба")


@pytest.fixture(scope="module")
def bar_mesh() -> TetMesh:
    return tet_mesh(box(0, L, -B / 2, B / 2, -T / 2, T / 2), edge_relative=0.04)


# ------------------------------------------------------------------ сетка
def test_the_mesh_keeps_the_volume(bar_mesh):
    assert bar_mesh.volume == pytest.approx(L * B * T, rel=1e-3)
    assert bar_mesh.nodes > 50 and bar_mesh.cells > 100


def test_the_mesh_reports_its_quality(bar_mesh):
    """Вырожденные тетраэдры портят не столько точность, сколько
    обусловленность: решение уезжает молча. Значит их надо видеть."""
    assert 0.0 < bar_mesh.worst_quality <= bar_mesh.median_quality <= 1.0
    assert "правильность худшего" in bar_mesh.line()


def test_a_finer_setting_gives_more_nodes():
    coarse = tet_mesh(box(0, L, -B / 2, B / 2, -T / 2, T / 2), edge_relative=0.08)
    fine = tet_mesh(box(0, L, -B / 2, B / 2, -T / 2, T / 2), edge_relative=0.03)
    assert fine.nodes > coarse.nodes


def test_an_organic_body_can_be_meshed():
    """Ради этого всё и затевалось: гладкое тело, которого брусом не описать."""
    body = mesh_sdf(blend_union(cyl_z_sdf(20.0, 0.0, 40.0), sphere_sdf(30.0, z=40.0), 6.0),
                    (-20, -20, -2, 20, 20, 58), 1.0, name="рукоять")
    mesh = tet_mesh(body, edge_relative=0.06)
    assert mesh.volume == pytest.approx(vol(body), rel=0.02)
    assert mesh.cells > 500


@pytest.mark.parametrize("bad", [dict(edge_relative=0.0), dict(epsilon=-1.0)])
def test_nonsensical_mesh_settings_are_refused(bad):
    with pytest.raises(FemError):
        tet_mesh(box(0, 10, 0, 10, 0, 10), **bad)


# ------------------------------------------------------------------ эталон
def test_the_patch_test_passes_on_an_unstructured_mesh(bar_mesh):
    """Эталоны из `verify()` держатся на плоскостях симметрии, а у сетки
    произвольного тела их нет: у бруска, разбитого fTetWild, на плоскость y=0
    попадает горстка узлов из двухсот. Тест заплаты этого не требует."""
    report = patch_test(bar_mesh.skfem(), PLA)
    assert report.ok, report.report()
    assert all(c.error < TOL_PATCH for c in report.cases)


def test_the_patch_test_catches_a_linear_element(bar_mesh):
    """И вот тут важно, что сетка НЕПРАВИЛЬНАЯ.

    На структурированной сетке квадратичное поле воспроизводит и линейный
    элемент — до 2e-15, хотя представить квадратику он не может: ему льстит
    симметрия. На сетке fTetWild тот же элемент даёт 1e-3 и проваливается, как
    и положено. Проверять элемент надо на неправильной сетке.
    """
    from skfem import ElementTetP1, ElementVector

    report = patch_test(bar_mesh.skfem(), PLA,
                        element=lambda: ElementVector(ElementTetP1()))
    by_name = {c.name: c for c in report.cases}
    assert by_name["заплата: линейное поле"].ok, "линейное поле обязан дать и P1"
    assert not by_name["заплата: квадратичное поле"].ok
    assert by_name["заплата: квадратичное поле"].error > 1e-5


def test_the_patch_test_refuses_a_mesh_without_an_interior():
    """Сетка, где все узлы на границе, тест заплаты не проверяет ничем —
    и молча выдавать на ней «ОК» было бы хуже отказа."""
    from skfem import MeshTet

    single = MeshTet(np.array([[0.0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]),
                     np.array([[0], [1], [2], [3]]))
    with pytest.raises(FemError, match="внутреннего узла"):
        patch_test(single, PLA)


# ------------------------------------------------------------------ решение
def test_the_unstructured_answer_matches_the_structured_one(bar_mesh):
    """Два независимых пути к одному числу.

    Структурированная сетка и сетка fTetWild строятся совершенно по-разному;
    сойтись они обязаны. Расхождение означало бы, что врёт один из них, и
    какой — по одному пути не узнать.
    """
    unstructured = S.solve_faces(bar_mesh, PLA, fixed="x_min", load="x_max",
                                 force=(0.0, 0.0, -1.0))
    structured = S.tip_load(Bar(L, B, T), PLA, force=1.0, divisions=(20, 4, 2))
    a = -float(unstructured.at_face(0, L, 2).mean())
    b = -float(structured.at_face(0, L, 2).mean())
    assert a == pytest.approx(b, rel=0.02), f"неструктурированная {a}, структурированная {b}"


def test_the_load_is_the_force_that_was_asked_for(bar_mesh):
    """Прогиб обязан быть ПРОПОРЦИОНАЛЕН силе: если площадь грани посчитана
    неверно, здесь это не видно, а вот в сверке с формулой — сразу. Первая
    версия собирала линейную форму «1.0», которая от пробной функции не
    зависит вовсе, и прогиб выходил ровно в десять раз меньше должного."""
    one = S.solve_faces(bar_mesh, PLA, fixed="x_min", load="x_max", force=(0, 0, -1.0))
    three = S.solve_faces(bar_mesh, PLA, fixed="x_min", load="x_max", force=(0, 0, -3.0))
    a = -float(one.at_face(0, L, 2).mean())
    b = -float(three.at_face(0, L, 2).mean())
    assert b == pytest.approx(3 * a, rel=1e-6)
    # И абсолютная величина согласна с балочной формулой в пределах пары процентов.
    assert a == pytest.approx(L ** 3 / (3 * PLA.modulus * (B * T ** 3 / 12)), rel=0.05)


@pytest.mark.parametrize("spec", ["q_min", "z_middle", "z", "zmin"])
def test_a_nonsensical_face_is_refused(bar_mesh, spec):
    with pytest.raises(FemError, match="ось_min"):
        S.solve_faces(bar_mesh, PLA, fixed=spec, load="x_max", force=(0, 0, -1.0))


def test_a_curved_extreme_is_refused_instead_of_pinching_three_nodes():
    """У ПЛОСКОГО торца грани лежат ровно на плоскости, и сколь угодно малый
    допуск их находит. Опасен другой случай: крайняя точка скруглённого тела,
    где в отбор попадает почти ничего. Молча защемить три узла вместо грани —
    верный способ получить красивую чушь, поэтому тут отказ с объяснением.
    """
    ball = tet_mesh(mesh_sdf(sphere_sdf(24.0), (-14, -14, -14, 14, 14, 14), 1.0),
                    edge_relative=0.08)
    with pytest.raises(FemError, match="не нашлось"):
        S.solve_faces(ball, PLA, fixed="z_min", load="z_max",
                      force=(0, 0, -1.0), tolerance=1e-12)


def test_a_curved_extreme_works_with_an_honest_tolerance():
    """А с внятным допуском шапочка отбирается, и счёт идёт."""
    ball = tet_mesh(mesh_sdf(sphere_sdf(24.0), (-14, -14, -14, 14, 14, 14), 1.0),
                    edge_relative=0.08)
    solution = S.solve_faces(ball, PLA, fixed="z_min", load="z_max",
                             force=(0, 0, -1.0), tolerance=2.0)
    assert solution.dofs > 100 and math.isfinite(solution.peak_strain)


def _trivial_job():
    return ({"status": "ok", "answer": 1}, b"")


def test_meshing_does_not_break_isolated_builds(bar_mesh):
    """Замок на настоящую регрессию.

    Одна построенная сетка раздувает виртуальное пространство процесса на
    0.9…1.4 ГБ — это резервы пулов потоков OpenMP и арен geogram, не занятая
    память. При абсолютном пределе в 4096 МБ после этого падала ЛЮБАЯ
    изолированная сборка: «предел ниже того, что уже занято». Здесь сетка
    строится заведомо раньше (фикстура), и изолированная работа обязана идти.
    """
    from datum.build.worker import WorkerLimits, run_in_worker

    assert bar_mesh.cells > 0
    outcome = run_in_worker(_trivial_job, limits=WorkerLimits(wall_seconds=30.0))
    assert outcome.ok, outcome.message


def test_the_mesher_does_not_litter_the_working_directory(tmp_path, monkeypatch):
    """fTetWild пишет отладочный `__tracked_surface.stl` в ТЕКУЩИЙ каталог при
    каждом построении, и параметрами это не выключается. Библиотека, гадящая в
    чужой рабочий каталог, — не то, что стоит оставлять: два таких файла уже
    успели попасть в коммит.
    """
    from datum.fem.tetmesh import LITTER

    monkeypatch.chdir(tmp_path)
    tet_mesh(box(0, 10, 0, 10, 0, 10), edge_relative=0.2)
    assert not (tmp_path / LITTER).exists()


def test_a_pre_existing_file_of_that_name_is_left_alone(tmp_path, monkeypatch):
    """Убирать надо ТОЛЬКО за собой. Файл с таким именем мог лежать тут и до
    нас, и стирать чужое молча нельзя."""
    from datum.fem.tetmesh import LITTER

    monkeypatch.chdir(tmp_path)
    mine = tmp_path / LITTER
    mine.write_text("не трогать", encoding="utf-8")
    tet_mesh(box(0, 10, 0, 10, 0, 10), edge_relative=0.2)
    assert mine.exists(), "чужой файл с тем же именем удалять нельзя"


# ------------------------------------------------------------------ операция
def test_the_operation_proves_itself_on_this_very_mesh(tmp_path):
    """Порядок в операции не случаен: сетка → эталон НА НЕЙ ЖЕ → ответ.

    Эталон на другой сетке ничего не доказал бы про эту: у неструктурированной
    сетки качество тетраэдров разное, и портит его именно геометрия детали.
    """
    import asyncio

    from datum.doc.project import Project
    from datum.tools.ops import fem_solve

    root = tmp_path / "балка"
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "ДЛИНА: {value: 40, unit: mm, provenance: chosen, reason: '—'}\n",
        encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        "@part('брус')\ndef build():\n"
        "    return box(0, P.ДЛИНА, -5, 5, -1, 1)\n",
        encoding="utf-8")
    Project.open(root)

    out = asyncio.run(fem_solve(project=str(root), part="брус", modulus=2000.0,
                                force=1.0, fixed="x_min", load="x_max",
                                along="-Z", edge=0.05))
    assert "error" not in out, out.get("report")
    assert out["self_check"], "ответ обязан приходить вместе с эталонами"
    assert all(v < 1e-9 for v in out["self_check"].values()), out["self_check"]
    # Прогиб консоли под 1 Н: формула даёт 1.6 мм, трёхмерный ответ на пару
    # процентов ниже (см. `fem/solve.py`). Границы с запасом на разброс от
    # недетерминированности мешера — замерено 0.25 % на восьми прогонах.
    assert 1.45 < out["max_displacement_mm"] < 1.65, out["report"]
    # И главное: защемлена ВСЯ грань. Первый допуск отбора был привязан к
    # габариту и терял до трети заделки — прогиб тогда выскакивал на 10 %.
    assert out["faces"]["fixed_area_mm2"] == pytest.approx(10.0 * 2.0, rel=0.01), \
        out["report"]
    assert out["mesh"]["nodes"] > 50
    assert "эталоны" in out["report"] and "сетка" in out["report"]


def test_the_operation_refuses_a_nonsensical_face(tmp_path):
    import asyncio

    from datum.doc.project import Project
    from datum.tools.ops import fem_solve

    root = tmp_path / "балка2"
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "ДЛИНА: {value: 20, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        "@part('брус')\ndef build():\n    return box(0, P.ДЛИНА, -5, 5, -2, 2)\n",
        encoding="utf-8")
    Project.open(root)

    out = asyncio.run(fem_solve(project=str(root), part="брус", modulus=2000.0,
                                force=1.0, fixed="куда_то", load="x_max", edge=0.1))
    assert "error" in out and "ось_min" in out["error"]


# ------------------------------------------------- отбор граней и разброс
def test_the_clamped_face_is_selected_whole(bar_mesh):
    """Допуск отбора грани привязан к КОНВЕРТУ мешера, а не к габариту.

    Мешер вправе двигать поверхность внутри конверта, и узлы плоской грани
    ложатся не ровно на плоскость: замерено смещение до 5.5·10⁻³ мм на бруске
    40×10×2. Прежний допуск в 10⁻⁶ габарита (1.8·10⁻⁴ мм) отбирал тогда
    13.5…16.4 мм² вместо 20 — до трети заделки оставалось свободной, и прогиб
    выскакивал на десяток процентов. Поймал это мигающий тест.

    Число граней о полноте отбора НЕ говорит: триангуляция разная, и восемь
    крупных треугольников покрывают ту же грань, что десять мелких. Говорит
    площадь.
    """
    solution = S.solve_faces(bar_mesh, PLA, fixed="x_min", load="x_max",
                             force=(0.0, 0.0, -1.0))
    assert solution.fixed_area == pytest.approx(B * T, rel=0.01)
    assert solution.load_area == pytest.approx(B * T, rel=0.01)
    assert solution.face_tolerance > bar_mesh.envelope_mm


def test_the_answer_survives_the_mesher_being_random():
    """Мешер НЕ ДЕТЕРМИНИРОВАН: на одном теле даёт 199…215 узлов от запуска к
    запуску, и зерна наружу не отдаёт. Ответ от этого гуляет, но гораздо
    меньше — замерено 0.25 % на восьми прогонах при грубой сетке и 0.15 % при
    вдвое мельче, то есть разброс убывает вместе с ошибкой дискретизации и
    лежит внутри вилки, которую проверки и так сообщают.

    Тест сторожит именно это: пусть сетка случайна, ответ обязан оставаться
    тем же в пределах процента.
    """
    from datum.geom.prims import box

    bar = box(0, L, -B / 2, B / 2, -T / 2, T / 2)
    answers = []
    for _ in range(3):
        mesh = tet_mesh(bar, edge_relative=0.05)
        solution = S.solve_faces(mesh, PLA, fixed="x_min", load="x_max",
                                 force=(0.0, 0.0, -1.0))
        answers.append(-float(solution.at_face(0, L, 2).mean()))
    spread = (max(answers) - min(answers)) / (sum(answers) / len(answers))
    assert spread < 0.02, f"разброс {spread:.3%} слишком велик: {answers}"


def test_an_old_litter_file_does_not_get_overwritten(tmp_path, monkeypatch):
    """Раз попав в каталог, след раньше оставался навсегда и перезаписывался
    каждым построением: правило «убирать только своё» считало его чужим."""
    from datum.fem.tetmesh import LITTER

    monkeypatch.chdir(tmp_path)
    old = tmp_path / LITTER
    old.write_text("прежний след", encoding="utf-8")
    tet_mesh(box(0, 10, 0, 10, 0, 10), edge_relative=0.2)
    assert old.read_text(encoding="utf-8") == "прежний след"


# ------------------------------------------------- честность ответа операции
def _beam_project(tmp_path, name="балка3"):
    from datum.doc.project import Project

    root = tmp_path / name
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "ДЛИНА: {value: 40, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box, sphere\n\n"
        "@part('брус')\ndef build():\n    return box(0, P.ДЛИНА, -5, 5, -1, 1)\n\n"
        "@part('шар')\ndef ball():\n    return sphere(5.0)\n", encoding="utf-8")
    Project.open(root)
    return root


def test_the_operation_reports_how_far_the_mesh_is_from_converged(tmp_path):
    """Тест заплаты доказывает элемент, а не сетку. Поэтому вторая сетка и
    вилка — иначе одно число с пятью знаками рядом с «эталоны 1e-15»."""
    import asyncio

    from datum.tools.ops import fem_solve

    out = asyncio.run(fem_solve(project=str(_beam_project(tmp_path)), part="брус",
                                modulus=2000.0, force=1.0, fixed="x_min",
                                load="x_max", along="-Z", edge=0.05))
    assert "error" not in out, out.get("report")
    lo, hi = out["max_displacement_band_mm"]
    assert lo == pytest.approx(out["max_displacement_mm"])
    assert hi >= lo
    assert "coarse_max_displacement_mm" in out
    assert "НЕ сходимость" in out["report"]


def test_clamping_and_loading_the_same_face_is_refused(tmp_path):
    import asyncio

    from datum.tools.ops import fem_solve

    out = asyncio.run(fem_solve(project=str(_beam_project(tmp_path, "балка4")),
                                part="брус", modulus=2000.0, force=1.0,
                                fixed="x_min", load="x_min", edge=0.1))
    assert "error" in out and "одна и та же грань" in out["error"]


def test_a_point_like_clamp_is_warned_about(tmp_path):
    """Шар с заделкой по z_min: под допуск попадает пятачок меньше
    квадратного миллиметра — решение возвращалось без единого слова."""
    import asyncio

    from datum.tools.ops import fem_solve

    out = asyncio.run(fem_solve(project=str(_beam_project(tmp_path, "шарик")),
                                part="шар", modulus=2000.0, force=1.0,
                                fixed="z_min", load="z_max", edge=0.1))
    assert "error" not in out, out.get("report")
    assert out["faces"]["fixed_share"] < 0.10
    assert any("проекции" in w for w in out["warnings"]), out["report"]


# ------------------------------------------------ участок грани и сингулярность
def _bracket_project(tmp_path, name: str, fillet: bool):
    """Уголок 40×10×4 + стойка 4×10×30: острый внутренний угол или скругление R3."""
    from datum.doc.project import Project

    root = tmp_path / name
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "R: {value: 3, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    body = ("    parts = [box(0, 40, 0, 10, 0, 4), box(0, 4, 0, 10, 0, 30)]\n"
            + ("    parts.append(diff(box(4, 7, 0, 10, 4, 7), [cyl_y(6, -1, 11, x=7, z=7, n=96)]))\n"
               if fillet else "")
            + "    return union(parts)\n")
    (root / "parts" / "p.py").write_text(
        "from datum.build import part\nfrom datum.geom import box, cyl_y, diff, union\n\n"
        "@part('уголок')\ndef build():\n" + body, encoding="utf-8")
    Project.open(root)
    return root


def _bracket(tmp_path, name, fillet, **kw):
    import asyncio

    from datum.tools.ops import fem_solve

    return asyncio.run(fem_solve(project=str(_bracket_project(tmp_path, name, fillet)),
                                 part="уголок", modulus=2500.0, force=5.0, fixed="x_max",
                                 load="z_max", along="+X", edge=0.03, **kw))


def test_a_sharp_inner_corner_is_called_a_singularity(tmp_path):
    """Пик в остром внутреннем углу растёт с измельчением без предела, а смещение
    сходится. Это свойство модели — и сравнивать такой пик с допускаемым нельзя."""
    out = _bracket(tmp_path, "острый", fillet=False)
    assert "error" not in out, out.get("error")
    assert out["peak_strain_singular"] is True
    x, _, z = out["peak_strain_at"]
    assert abs(x - 4) < 1.5 and abs(z - 4) < 1.5                 # у внутреннего угла
    assert "острого внутреннего угла" in out["report"]


def test_a_fillet_takes_the_singularity_away(tmp_path):
    """Со скруглением R3 пик остаётся конечным. Сходится ли он на ГРУБОЙ сетке —
    вопрос разрешения скругления (замерено: рост 2…9 % от запуска к запуску,
    мешер недетерминирован), но особенностью это не объявляется."""
    out = _bracket(tmp_path, "скругление", fillet=True)
    assert "error" not in out, out.get("error")
    assert out["peak_strain_singular"] is False
    sharp = _bracket(tmp_path, "без скругления", fillet=False)
    assert out["peak_strain"] < sharp["peak_strain"]


def test_a_load_on_a_patch_of_the_face(tmp_path):
    """Сила на пятно, а не на всю грань: отобрана половина торца стойки.

    Треугольник сетки идёт в отбор по СЕРЕДИНЕ, поэтому пересечённые краем
    пятна входят или не входят целиком: площадь честно гуляет на полосу
    шириной в ребро сетки вдоль края (4 мм). Допуск в 5 % был уже этой полосы
    и падал от запуска к запуску — мешер недетерминирован (замерено 21.12 при
    20). Граница выводится из шага сетки, а не подбирается."""
    out = _bracket(tmp_path, "пятно", fillet=True, load_region=[0, 0, 29, 4, 5, 31])
    assert "error" not in out, out.get("error")
    edge = 0.03 * math.dist((0, 0, 0), (40, 10, 30))          # ребро сетки, мм
    assert abs(out["faces"]["load_area_mm2"] - 20.0) <= edge * 4.0
    whole = _bracket(tmp_path, "грань", fillet=True)
    assert whole["faces"]["load_area_mm2"] == pytest.approx(40.0, rel=0.05)


def test_a_region_off_the_face_is_refused(tmp_path):
    out = _bracket(tmp_path, "мимо", fillet=True, load_region=[20, 0, 29, 30, 10, 31])
    assert "участок мимо грани" in out.get("error", "")
