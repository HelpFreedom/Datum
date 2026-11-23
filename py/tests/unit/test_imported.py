"""Чужие модели как детали проекта.

Требование из задания: «импортировать и обрабатывать чужие модели». Половина
его — диагностика и ремонт — работала и раньше; здесь закрывается вторая:
починенная модель становится деталью проекта и живёт наравне со своими.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import trimesh

from datum.build.imported import (ImportedError, imported, imported_names,
                                  imported_stamp)
from datum.doc.project import Project


def run(op, **kwargs):
    return asyncio.run(op(**kwargs))


@pytest.fixture
def foreign(tmp_path):
    """Чужая модель: нарочно с дырой, чтобы ремонт был не формальностью."""
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=9)
    faces = mesh.faces.copy()
    keep = list(range(len(faces)))
    del keep[10:40]                       # выкусываем кусок оболочки
    broken = trimesh.Trimesh(vertices=mesh.vertices, faces=faces[keep], process=False)
    assert not broken.is_watertight, "проба негодна: меш должен быть дырявым"
    path = tmp_path / "чужая.stl"
    broken.export(path)
    return path


# ------------------------------------------------------------------ импорт
def test_import_keeps_the_source_untouched(latch, foreign):
    """Ремонт — решение, а не факт. Через месяц надо уметь сравнить с
    оригиналом и ответить, что именно с ним сделали."""
    from datum.tools.ops import import_model

    before = foreign.read_bytes()
    out = run(import_model, project=str(latch), path=str(foreign), name="чужая")
    assert not out.get("error"), out.get("error")

    folder = latch / "imported" / "чужая"
    assert (folder / "source.stl").read_bytes() == before
    assert (folder / "result.stl").is_file()

    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "чужая.stl"
    assert len(meta["digest"]) == 64
    assert [s["op"] for s in meta["steps"]], "журнал ремонта пуст"


def test_the_repaired_model_is_usable(latch, foreign):
    """Главное: после импорта модель — обычная деталь. Её строят, мерят,
    режут и вычитают наравне со своими."""
    from datum.tools.ops import import_model

    run(import_model, project=str(latch), path=str(foreign), name="чужая")
    (latch / "parts" / "foreign.py").write_text(
        'from datum.build import imported, part\n\n\n'
        '@part("чужая")\n'
        'def build():\n'
        '    return imported("чужая")\n',
        encoding="utf-8")

    built = Project.open(latch).build()
    assert "чужая" in built
    result = built["чужая"]
    assert result.ok, result.line()
    assert result.topology.volume > 0
    assert result.topology.bodies == 1


def test_a_second_import_replaces_the_first(latch, foreign, tmp_path):
    """Повторный импорт под тем же именем не должен оставлять два исходника
    разных форматов: непонятно, какой из них настоящий."""
    from datum.tools.ops import import_model

    run(import_model, project=str(latch), path=str(foreign), name="чужая")
    other = tmp_path / "другая.3mf"
    trimesh.creation.box(extents=(5, 5, 5)).export(other)
    run(import_model, project=str(latch), path=str(other), name="чужая")

    folder = latch / "imported" / "чужая"
    assert sorted(p.name for p in folder.glob("source.*")) == ["source.3mf"]


def test_a_missing_file_is_a_plain_refusal(latch):
    from datum.tools.ops import import_model

    out = run(import_model, project=str(latch), path=str(latch / "нет-такого.stl"))
    assert "нет такого файла" in out["error"]


# -------------------------------------------------------------- ключ кеша
def test_replacing_the_model_invalidates_the_cache(latch, foreign, tmp_path):
    """Ключ кеша сборок состоит из отпечатка кода и значений параметров. Чужой
    файл в него не входит сам: подменили модель — кеш показал бы вчерашнюю
    геометрию. Это ровно тот род молчаливой ошибки, ради которого Datum и
    затевался, поэтому отпечаток импорта входит в ключ."""
    from datum.tools.ops import import_model

    run(import_model, project=str(latch), path=str(foreign), name="чужая")
    (latch / "parts" / "foreign.py").write_text(
        'from datum.build import imported, part\n\n\n'
        '@part("чужая")\n'
        'def build():\n'
        '    return imported("чужая")\n',
        encoding="utf-8")

    first = Project.open(latch).build(["чужая"])["чужая"].topology.volume
    assert first > 0

    small = tmp_path / "мелкая.stl"
    trimesh.creation.box(extents=(4, 4, 4)).export(small)
    run(import_model, project=str(latch), path=str(small), name="чужая")

    second = Project.open(latch).build(["чужая"])["чужая"].topology.volume
    assert second == pytest.approx(64, rel=0.3), (
        f"кеш отдал старую геометрию: {second:.0f} вместо ~64 мм³")
    assert abs(second - first) > 1.0


def test_the_stamp_changes_with_the_content(latch, foreign, tmp_path):
    from datum.tools.ops import import_model

    assert imported_stamp(latch) == ""
    run(import_model, project=str(latch), path=str(foreign), name="чужая")
    one = imported_stamp(latch)
    assert one

    other = tmp_path / "другая.stl"
    trimesh.creation.box(extents=(6, 6, 6)).export(other)
    run(import_model, project=str(latch), path=str(other), name="чужая")
    assert imported_stamp(latch) != one


# ------------------------------------------------------------------ охрана
@pytest.mark.parametrize("name", ["../тайна", "a/b", ".скрытая", "", "x" * 65])
def test_a_bad_name_never_reaches_the_disk(latch, name):
    """Имя становится именем каталога. Ни косых черт, ни точек в начале."""
    with pytest.raises(ImportedError):
        imported(name, root=latch)


def test_an_unknown_model_says_what_there_is(latch, foreign):
    from datum.tools.ops import import_model

    run(import_model, project=str(latch), path=str(foreign), name="чужая")
    with pytest.raises(ImportedError) as info:
        imported("нетакой", root=latch)
    assert "чужая" in str(info.value), "отказ обязан назвать, что есть"


def test_imported_outside_a_build_explains_itself(latch):
    """Скрипт запущен напрямую — корень проекта взять неоткуда. Сообщение
    должно говорить, что делать, а не падать по AttributeError."""
    from datum.build.context import NoParamsError

    with pytest.raises(NoParamsError) as info:
        imported("что-нибудь")
    assert "use_project" in str(info.value)


def test_listing_is_empty_without_imports(latch):
    assert imported_names(latch) == []


# ------------------------------------------------ повторный импорт — транзакция
def test_a_bad_reimport_leaves_the_previous_model_intact(tmp_path):
    """Прежние source.* и result.* удалялись ДО загрузки нового файла: битый
    повторный импорт оставлял опись «цела» при отсутствующем результате."""
    import asyncio

    import trimesh

    from datum.build.imported import imported_names, result_path
    from datum.tools.ops import import_model, imported_list

    root = tmp_path / "проект"
    (root / "parts").mkdir(parents=True)
    good = tmp_path / "cube.stl"
    trimesh.creation.box((10, 10, 10)).export(good)
    first = asyncio.run(import_model(project=str(root), path=str(good), name="куб"))
    assert "error" not in first, first

    broken = tmp_path / "broken.stl"
    broken.write_bytes(b"solid x\nnot an STL at all\n")
    second = asyncio.run(import_model(project=str(root), path=str(broken), name="куб"))
    assert "error" in second
    assert imported_names(root) == ["куб"]
    assert result_path(root, "куб") is not None, "прежний результат обязан остаться"
    listing = asyncio.run(imported_list(project=str(root)))
    assert "error" not in listing and "куб" in listing["report"]


def test_an_unknown_repair_level_is_refused(tmp_path):
    import asyncio

    import trimesh

    from datum.tools.ops import import_model

    root = tmp_path / "проект2"
    (root / "parts").mkdir(parents=True)
    good = tmp_path / "cube.stl"
    trimesh.creation.box((10, 10, 10)).export(good)
    out = asyncio.run(import_model(project=str(root), path=str(good), repair_level="bogus"))
    assert "error" in out and "repair_level" in out["error"]
    assert not (root / "imported").exists() or not any((root / "imported").iterdir())


# ------------------------------------------------------- образец и деталь
def test_an_import_is_a_sample_until_a_part_takes_it(latch, foreign):
    """Импорт виден на столе сразу — образцом. «В деталь» заводит скрипт,
    который берёт модель и ставит её в центр стола; с этого момента модель —
    деталь, и образец ей не нужен (used_by)."""
    from datum.geom.measure import bbox
    from datum.tools.ops import clear_project_cache, import_model, import_to_part, imported_views

    run(import_model, project=str(latch), path=str(foreign), name="чужая")
    views = run(imported_views, project=str(latch))
    assert not views.get("error"), views.get("error")
    (view,) = views["models"]
    assert view["name"] == "чужая" and view["used_by"] == []
    assert view["url"] == f"/obj/{view['digest']}.glb" and view["tris"] > 0

    out = run(import_to_part, project=str(latch), name="чужая")
    assert not out.get("error"), out.get("error")
    assert out["file"] == "parts/chuzhaya.py" and out["part"] == "чужая"

    clear_project_cache()
    views = run(imported_views, project=str(latch))
    assert views["models"][0]["used_by"] == ["parts/chuzhaya.py"]

    project = Project.open(latch)
    result = project.build(["чужая"])["чужая"]
    assert result.ok, result.line()
    lo, hi = bbox(result.solid)
    bx, by, _ = project.bed
    assert (lo[0] + hi[0]) / 2 == pytest.approx(bx / 2, abs=1e-3)
    assert (lo[1] + hi[1]) / 2 == pytest.approx(by / 2, abs=1e-3)
    assert lo[2] == pytest.approx(0.0, abs=1e-3)              # стоит на столе


def test_making_a_part_never_overwrites(latch, foreign):
    from datum.tools.ops import clear_project_cache, import_model, import_to_part

    run(import_model, project=str(latch), path=str(foreign), name="чужая")
    mine = latch / "parts" / "chuzhaya.py"
    mine.write_text("# моё\n", encoding="utf-8")
    out = run(import_to_part, project=str(latch), name="чужая")
    assert "не перезаписывается" in out["error"]
    assert mine.read_text(encoding="utf-8") == "# моё\n"

    clear_project_cache()
    taken = run(import_to_part, project=str(latch), name="чужая", part="корпус")
    assert "уже есть в проекте" in taken["error"]           # имя детали занято
    missing = run(import_to_part, project=str(latch), name="нет-такой")
    assert "не импортирована" in missing["error"]


def test_a_sample_is_published_once_per_import(latch, foreign, tmp_path, monkeypatch):
    """Модель в полтора миллиона треугольников не перегоняется в GLB на каждое
    обновление окна — только когда её переимпортировали."""
    import datum.io.glb as glb
    from datum.tools.ops import import_model, imported_views

    calls = []
    real = glb.mesh_to_glb
    monkeypatch.setattr(glb, "mesh_to_glb", lambda *a, **k: calls.append(1) or real(*a, **k))

    run(import_model, project=str(latch), path=str(foreign), name="чужая")
    first = run(imported_views, project=str(latch))["models"][0]
    again = run(imported_views, project=str(latch))["models"][0]
    assert len(calls) == 1 and again["digest"] == first["digest"]

    other = tmp_path / "куб.stl"
    trimesh.creation.box(extents=(5, 5, 5)).export(other)
    run(import_model, project=str(latch), path=str(other), name="чужая")
    changed = run(imported_views, project=str(latch))["models"][0]
    assert len(calls) == 2 and changed["digest"] != first["digest"]


# ------------------------------------------------- на стол деталями, одним шагом
def _empty_project(tmp_path):
    from datum.doc.scaffold import init_project

    root = tmp_path / "проект"
    init_project(root)
    return root


def _footprints(project: Project):
    from datum.geom.measure import bbox

    out = {}
    for name, res in project.build().items():
        assert res.ok, (name, res.error)
        (x0, y0, z0), (x1, y1, _) = bbox(res.solid)
        out[name] = (x0, y0, x1, y1, z0)
    return out


def _apart(a, b, gap=0.0):
    return a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1]


def test_a_plate_of_three_objects_becomes_three_parts_on_the_bed(tmp_path):
    """3MF из трёх объектов: три детали, а не одна. Части одного объекта
    («Пирамидка» из двух кубов) — одна деталь; расстановка автора сохранена и
    стоит в центре стола; детали собираются, как есть."""
    from datum.tools.ops import clear_project_cache, import_to_bed
    from helpers3mf import plate_of_three

    root = _empty_project(tmp_path)
    plate = tmp_path / "стол.3mf"
    plate_of_three(plate)
    out = run(import_to_bed, project=str(root), path=str(plate))
    assert not out.get("error"), out.get("report")
    assert [p["part"] for p in out["parts"]] == ["Кубик", "Пирамидка", "стол-3"]
    assert out["kept_file_layout"] and out["fits"]
    assert sorted(imported_names(root)) == ["Кубик", "Пирамидка", "стол-3"]

    clear_project_cache()
    feet = _footprints(Project.open(root))
    assert set(feet) == {"Кубик", "Пирамидка", "стол-3"}
    assert all(f[4] == pytest.approx(0) for f in feet.values()), "все стоят на столе"
    # Как в файле: Пирамидка правее Кубика на 30, третий — на 60.
    assert feet["Пирамидка"][0] - feet["Кубик"][0] == pytest.approx(30)
    assert feet["стол-3"][0] - feet["Кубик"][0] == pytest.approx(60)
    lo = min(f[0] for f in feet.values())
    hi = max(f[2] for f in feet.values())
    assert (lo + hi) / 2 == pytest.approx(128)
    # Пирамидка — две части одного объекта: слиты в одно тело, как при нарезке.
    assert out["parts"][1]["bodies"] == 1
    assert any("слиты" in n for n in out["parts"][1]["notes"])


def test_objects_piled_together_are_laid_out_apart(tmp_path):
    """Экспорт сборки: все объекты в начале координат — их раскладывают рядами."""
    from datum.doc.bedlayout import GAP
    from datum.tools.ops import clear_project_cache, import_to_bed
    from helpers3mf import cube_mesh, model, package

    root = _empty_project(tmp_path)
    path = tmp_path / "куча.3mf"
    package(path, model(
        "".join(f'<object id="{i}" type="model" name="деталь{i}">{cube_mesh(0, 20)}</object>'
                for i in range(1, 5)),
        build="".join(f'<item objectid="{i}"/>' for i in range(1, 5))), {})
    out = run(import_to_bed, project=str(root), path=str(path))
    assert not out.get("error"), out.get("report")
    assert not out["kept_file_layout"]
    clear_project_cache()
    feet = list(_footprints(Project.open(root)).values())
    assert len(feet) == 4
    for i in range(4):
        for j in range(i + 1, 4):
            assert _apart(feet[i], feet[j], GAP - 1e-6)


def test_the_bed_is_shared_with_parts_already_there(cube, tmp_path):
    """Кубик в центре стола: новая деталь встаёт рядом, а не в него."""
    from datum.tools.ops import clear_project_cache, import_to_bed

    path = tmp_path / "брусок.stl"
    trimesh.creation.box(extents=(30, 30, 10)).export(path)
    out = run(import_to_bed, project=str(cube), path=str(path))
    assert not out.get("error"), out.get("report")
    clear_project_cache()
    feet = _footprints(Project.open(cube))
    assert set(feet) == {"кубик", "брусок"}
    assert _apart(feet["кубик"], feet["брусок"])
    assert "занят" in out["note"]


def test_a_second_import_of_the_same_file_gets_new_names(tmp_path):
    from datum.tools.ops import clear_project_cache, import_to_bed

    root = _empty_project(tmp_path)
    path = tmp_path / "брусок.stl"
    trimesh.creation.box(extents=(20, 20, 10)).export(path)
    first = run(import_to_bed, project=str(root), path=str(path))
    second = run(import_to_bed, project=str(root), path=str(path))
    assert [p["part"] for p in first["parts"]] == ["брусок"]
    assert [p["part"] for p in second["parts"]] == ["брусок-2"]
    clear_project_cache()
    feet = _footprints(Project.open(root))
    assert _apart(feet["брусок"], feet["брусок-2"])


def test_a_failure_halfway_leaves_nothing_behind(tmp_path, monkeypatch):
    """Всё или ничего: отказ на второй детали убирает и первую, и модели."""
    import sys

    from datum.tools.ops import import_to_bed
    from helpers3mf import plate_of_three

    root = _empty_project(tmp_path)
    plate = tmp_path / "стол.3mf"
    plate_of_three(plate)
    imp = sys.modules["datum.build.imported"]      # имя `imported` в пакете — функция
    real = imp.part_source
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("диск переполнен")
        return real(*args, **kwargs)

    monkeypatch.setattr(imp, "part_source", flaky)
    out = run(import_to_bed, project=str(root), path=str(plate))
    assert "диск переполнен" in out["error"]
    assert imported_names(root) == []
    assert not any((root / "parts").glob("*.py"))
