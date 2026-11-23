"""Чтение 3MF: то, что стоит в сборке, в мировых координатах, в миллиметрах.

Главный случай — устройство файлов Bambu/Orca: корневая модель состоит из
компонентов со ссылками `p:path` на отдельную часть пакета, где лежат сами
сетки. trimesh на каждый такой компонент приписывал ВСЕ сетки той части — и
импорт выходил с копиями деталей не на своих местах. Файлы здесь собираются
руками, чтобы было видно, что именно в них лежит.
"""

from __future__ import annotations

import numpy as np
import pytest

from datum.geom.repair import load_mesh
from datum.io.read3mf import Read3mfError, read_3mf, read_3mf_objects
from helpers3mf import cube_mesh as _cube
from helpers3mf import model as _model
from helpers3mf import package as _package
from helpers3mf import plate_of_three as _plate_of_three

def _bambu_like(path) -> None:
    """Корень: объект 4 из двух компонентов → части пакета с объектами 1 и 2.

    Сетки: куб 10 мм у x=0 и куб 5 мм у x=20. Компонент 1 сдвинут на +100 по Y,
    элемент сборки поднимает всё на +7 по Z.
    """
    part = _model(f'<object id="1" type="model">{_cube(0, 10)}</object>'
                  f'<object id="2" type="model">{_cube(20, 5)}</object>')
    root = _model(
        '<object id="4" type="model"><components>'
        '<component p:path="/3D/Objects/object_1.model" objectid="1" '
        'transform="1 0 0 0 1 0 0 0 1 0 100 0"/>'
        '<component p:path="/3D/Objects/object_1.model" objectid="2"/>'
        '</components></object>',
        build='<item objectid="4" transform="1 0 0 0 1 0 0 0 1 0 0 7"/>')
    _package(path, root, {"3D/Objects/object_1.model": part})


def test_components_from_another_part_are_taken_once_each(tmp_path):
    path = tmp_path / "bambu.3mf"
    _bambu_like(path)
    v, f = read_3mf(path)
    assert len(f) == 24                           # два куба, а не по два на компонент
    mesh = load_mesh(path)
    assert mesh.volume == pytest.approx(10**3 + 5**3, rel=1e-6)
    lo, hi = mesh.bounds
    # Сдвиги компонента и элемента сборки применены оба.
    assert lo == pytest.approx([0, 0, 7])
    assert hi == pytest.approx([25, 110, 17])


def test_objects_are_read_one_per_build_item(tmp_path):
    """Деталь = объект файла (решение владельца): части одного объекта вместе,
    каждый объект — отдельно, на своём месте и под своим именем."""
    path = tmp_path / "стол.3mf"
    _plate_of_three(path)
    objects, notes = read_3mf_objects(path)
    assert notes == []
    assert [o.name for o in objects] == ["Кубик", "Пирамидка", ""]
    assert [len(o.parts) for o in objects] == [1, 2, 1]
    assert [len(o.faces) for o in objects] == [12, 24, 12]
    pyramid = objects[1]
    assert pyramid.vertices.min(axis=0) == pytest.approx([30, 0, 0])
    assert pyramid.vertices.max(axis=0) == pytest.approx([36, 6, 9])
    # Слитное чтение — то же самое одной сеткой.
    v, f = read_3mf(path)
    assert len(f) == 48 and len(v) == sum(len(o.vertices) for o in objects)
    assert f.max() == len(v) - 1


def test_units_become_millimetres(tmp_path):
    path = tmp_path / "inch.3mf"
    root = _model(f'<object id="1" type="model">{_cube(0, 1)}</object>',
                  unit="inch", build='<item objectid="1"/>')
    _package(path, root, {})
    v, _ = read_3mf(path)
    assert np.ptp(v, axis=0) == pytest.approx([25.4, 25.4, 25.4], rel=1e-6)


def test_only_what_is_built_is_read(tmp_path):
    """Ресурс вне сборки — заготовка, не деталь: слайсер его не печатает."""
    path = tmp_path / "spare.3mf"
    root = _model(f'<object id="1" type="model">{_cube(0, 10)}</object>'
                  f'<object id="2" type="model">{_cube(50, 10)}</object>',
                  build='<item objectid="1"/>')
    _package(path, root, {})
    _, f = read_3mf(path)
    assert len(f) == 12


def test_an_empty_build_is_refused(tmp_path):
    path = tmp_path / "empty.3mf"
    _package(path, _model(f'<object id="1" type="model">{_cube(0, 10)}</object>'), {})
    with pytest.raises(Read3mfError, match="нет ни одной сетки"):
        read_3mf(path)


def test_a_broken_package_is_refused_by_name(tmp_path):
    path = tmp_path / "битый.3mf"
    path.write_bytes(b"not a zip")
    with pytest.raises(Read3mfError, match="битый.3mf"):
        read_3mf(path)


def test_reading_keeps_the_process_locale(tmp_path):
    """lib3mf переводит процесс в локаль «C» — и запись кириллицы без явной
    кодировки после этого ломалась по всему процессу (см. io.export)."""
    import locale

    path = tmp_path / "bambu.3mf"
    _bambu_like(path)
    before = locale.setlocale(locale.LC_ALL)
    read_3mf(path)
    assert locale.setlocale(locale.LC_ALL) == before


# ------------------------------------------------ как слайсер: роли частей
def _slicer_file(tmp_path, *objects):
    from helpers3mf import slicer_project

    path = tmp_path / "проект.3mf"
    slicer_project(path, list(objects))
    return path


def test_a_negative_part_is_cut_out_like_the_slicer_does(tmp_path):
    """Сверено с ElegooSlicer: вычитаемая часть вырезается из модели при нарезке
    (пластика меньше ровно на вырезанное). Куб 20 мм без столба 5×5 в углу."""
    from datum.geom.repair import load_objects

    from helpers3mf import box_mesh

    path = _slicer_file(tmp_path, {"name": "Коробка", "parts": [
        ("normal_part", box_mesh(0, 20, 0, 20, 0, 20)),
        ("negative_part", box_mesh(15, 25, 15, 25, -1, 21))]})
    (obj,), notes = load_objects(path)
    assert obj.name == "Коробка" and notes == []
    assert obj.mesh.volume == pytest.approx(8000 - 500)
    assert any("вычтены" in n for n in obj.notes)


@pytest.mark.parametrize("subtype", ["modifier_part", "support_blocker", "support_enforcer"])
def test_settings_volumes_are_not_geometry(tmp_path, subtype):
    """Модификатор и блокировщики — только настройки: слайсер их не печатает."""
    from datum.geom.repair import load_objects

    from helpers3mf import box_mesh

    path = _slicer_file(tmp_path, {"name": "Коробка", "parts": [
        ("normal_part", box_mesh(0, 20, 0, 20, 0, 20)),
        (subtype, box_mesh(30, 40, 0, 10, 0, 10))]})
    (obj,), _ = load_objects(path)
    assert obj.mesh.volume == pytest.approx(8000)
    assert obj.mesh.bounds[1][0] == pytest.approx(20), "модификатор не расширил деталь"
    assert any("не геометрия" in n for n in obj.notes)


def test_overlapping_parts_merge_into_one_body(tmp_path):
    """Части модели слиты, как при нарезке: перекрытие не считается дважды."""
    from datum.geom.repair import load_objects

    from helpers3mf import box_mesh

    path = _slicer_file(tmp_path, {"name": "Двойной", "parts": [
        ("normal_part", box_mesh(0, 10, 0, 10, 0, 10)),
        ("normal_part", box_mesh(5, 15, 0, 10, 0, 10))]})
    (obj,), _ = load_objects(path)
    assert obj.mesh.volume == pytest.approx(1500)
    assert obj.mesh.body_count == 1


def test_a_single_part_object_is_taken_triangle_for_triangle(tmp_path):
    """Без вычитаемых и с одной частью сетка не проходит через булеву вовсе."""
    from datum.geom.repair import load_objects

    from helpers3mf import box_mesh

    path = _slicer_file(tmp_path, {"name": "Куб", "parts": [
        ("normal_part", box_mesh(0, 10, 0, 10, 0, 10))]})
    (obj,), _ = load_objects(path)
    assert len(obj.mesh.faces) == 12 and obj.notes == ()


def test_unprintable_objects_are_skipped_and_said_so(tmp_path):
    from datum.geom.repair import load_objects

    from helpers3mf import box_mesh

    path = _slicer_file(
        tmp_path,
        {"name": "Печатается", "parts": [("normal_part", box_mesh(0, 10, 0, 10, 0, 10))]},
        {"name": "Нет", "printable": False, "at": (50, 0),
         "parts": [("normal_part", box_mesh(0, 10, 0, 10, 0, 10))]})
    objects, notes = load_objects(path)
    assert [o.name for o in objects] == ["Печатается"]
    assert any("непечатаемым" in n for n in notes)


def test_an_open_negative_part_is_not_silently_ignored(tmp_path):
    """Незамкнутую вычитаемую часть булевой не вычесть — и это сказано, а не скрыто."""
    from datum.geom.repair import load_objects

    from helpers3mf import box_mesh

    open_box = box_mesh(15, 25, 15, 25, -1, 21).replace('<triangle v1="4" v2="5" v3="6"/>', "")
    path = _slicer_file(tmp_path, {"name": "Коробка", "parts": [
        ("normal_part", box_mesh(0, 20, 0, 20, 0, 20)),
        ("negative_part", open_box)]})
    (obj,), _ = load_objects(path)
    assert obj.mesh.volume == pytest.approx(8000)
    assert any("НЕ ВЫЧТЕНЫ" in n for n in obj.notes)


def test_roles_are_keyed_by_object_and_part_not_by_part_alone(tmp_path):
    """Номер части уникален лишь в пределах объекта: у Bambu копии одной сетки
    делят номер (замерено на настоящем файле). Роль ищется по паре."""
    from datum.io.read3mf import _layout

    from helpers3mf import box_mesh

    path = _slicer_file(
        tmp_path,
        {"name": "А", "parts": [("normal_part", box_mesh(0, 10, 0, 10, 0, 10)),
                                ("negative_part", box_mesh(2, 4, 2, 4, -1, 11))]},
        {"name": "Б", "parts": [("normal_part", box_mesh(0, 10, 0, 10, 0, 10))]})
    layout = _layout(path)
    assert sorted(layout.roles.values()) == ["negative_part", "normal_part", "normal_part"]
    assert set(layout.names.values()) == {"А", "Б"}
    assert layout.printable == [True, True]


def test_load_mesh_reads_a_slicer_project_as_printed(tmp_path):
    """Одним мешем (диагноз, импорт образца) — тоже как при нарезке."""
    from datum.geom.repair import load_mesh

    from helpers3mf import box_mesh

    path = _slicer_file(tmp_path, {"name": "Коробка", "parts": [
        ("normal_part", box_mesh(0, 20, 0, 20, 0, 20)),
        ("modifier_part", box_mesh(30, 40, 0, 10, 0, 10)),
        ("negative_part", box_mesh(15, 25, 15, 25, -1, 21))]})
    assert load_mesh(path).volume == pytest.approx(7500)


# ---------------------------------------------------------------- зеркало
def _signed_volume(vertices, faces) -> float:
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)


def test_a_mirrored_part_is_read_right_side_out(tmp_path):
    """Найдено обзором: слайсер пишет отражённую копию (левая и правая пара)
    преобразованием с определителем < 0, а обход треугольников при этом не
    меняет — копия читалась вывернутой, пара сливалась в объём 0. Двойное
    зеркало (отражённая часть в отражённом объекте) — снова прямое тело."""
    mirror = "-1 0 0 0 1 0 0 0 1"
    body = (f'<object id="1" type="model">{_cube(0, 10)}</object>'
            f'<object id="2" type="model"><components>'
            f'<component objectid="1"/>'
            f'<component objectid="1" transform="{mirror} -5 0 0"/>'
            f'</components></object>'
            f'<object id="3" type="model"><components>'
            f'<component objectid="1" transform="{mirror} 0 0 0"/>'
            f'</components></object>')
    build = (f'<item objectid="2"/>'
             f'<item objectid="1" transform="{mirror} 60 0 0"/>'
             f'<item objectid="3" transform="{mirror} 90 0 0"/>')
    path = tmp_path / "зеркало.3mf"
    _package(path, _model(body, build=build), {})
    objects, _ = read_3mf_objects(path)
    volumes = [[_signed_volume(p.vertices, p.faces) for p in o.parts] for o in objects]
    assert volumes == [[pytest.approx(1000), pytest.approx(1000)],
                       [pytest.approx(1000)], [pytest.approx(1000)]]


def test_a_package_with_links_to_missing_thumbnails_is_read(tmp_path):
    """Консольный ElegooSlicer пишет в `_rels/.rels` связи на превью, которых
    в пакете нет, — и lib3mf отказывал ВЕСЬ файл («Could not create OPC
    Part»): так не читался ни один файл нарезки владельца (Gear Cube,
    `печать/`, 24 из 24). Связи на пустоту пропускаются, геометрия та же, а
    сам файл человека не трогается."""
    import zipfile

    from helpers3mf import MODEL_REL, REL

    part = _model(f'<object id="1" type="model">{_cube(0, 10)}</object>')
    root = _model('<object id="2" type="model"><components>'
                  '<component p:path="/3D/Objects/прокладка_1.model" objectid="1"/>'
                  '</components></object>', build='<item objectid="2"/>')
    good, bad = tmp_path / "good.3mf", tmp_path / "нарезка.3mf"
    _package(good, root, {"3D/Objects/прокладка_1.model": part})
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w") as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "_rels/.rels":
                data = (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{REL}">'
                        f'<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="{MODEL_REL}"/>'
                        '<Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://'
                        'schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>'
                        "<Relationship Target='/Metadata/plate_1_small.png' Id='rel-5' Type='x'>"
                        "</Relationship>"                   # и парой тегов — законно
                        '</Relationships>').encode()
            out.writestr(info, data)
    before = bad.read_bytes()

    objects, notes = read_3mf_objects(bad)
    (obj,) = objects
    assert len(obj.faces) == 12
    assert np.allclose(obj.vertices.min(axis=0), 0) and np.allclose(obj.vertices.max(axis=0), 10)
    assert any("2 связ. на отсутствующие части" in n for n in notes), notes
    assert bad.read_bytes() == before
    # Целый пакет — без замечания.
    assert not [n for n in read_3mf_objects(good)[1] if "отсутствующие" in n]


def test_relationship_targets_resolve_like_opc():
    from datum.io.read3mf import _rels_target

    assert _rels_target("_rels/.rels", "/Metadata/plate_1.png") == "Metadata/plate_1.png"
    assert _rels_target("3D/_rels/3dmodel.model.rels", "Objects/a.model") == "3D/Objects/a.model"
    assert _rels_target("3D/_rels/3dmodel.model.rels", "../Metadata/x.png") == "Metadata/x.png"
    assert _rels_target("_rels/.rels", "/3D/%D0%BF.model") == "3D/п.model"
