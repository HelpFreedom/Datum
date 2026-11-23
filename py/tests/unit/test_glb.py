"""GLB — то, что уезжает в окно.

Главное требование к этому формату не «открывается», а «совпадает»: в окне
человек МЕРЯЕТ, и показанная геометрия обязана быть той же, по которой считался
отпечаток и работали проверки. Поэтому почти каждый тест здесь сравнивает
обратное чтение с исходными массивами, а не проверяет, что файл не пустой.
"""

from __future__ import annotations

import io
import json
import struct

import numpy as np
import pytest
import trimesh

from datum.geom.convert import vertices_faces
from datum.geom.prims import box, cyl_z, sphere
from datum.io.glb import (DEFAULT_COLOR, GLB_WRITER_VERSION, mesh_to_glb,
                          solid_to_glb)

GLTF_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def unpack(blob: bytes) -> tuple[dict, bytes]:
    """Разобрать GLB руками: так проверяется сам контейнер, а не библиотека."""
    magic, version, total = struct.unpack("<III", blob[:12])
    assert magic == GLTF_MAGIC, "не GLB"
    assert version == 2
    assert total == len(blob), "длина в заголовке не совпадает с файлом"
    n, kind = struct.unpack("<II", blob[12:20])
    assert kind == JSON_CHUNK
    gltf = json.loads(blob[20:20 + n])
    rest = blob[20 + n:]
    bn, bkind = struct.unpack("<II", rest[:8])
    assert bkind == BIN_CHUNK
    return gltf, rest[8:8 + bn]


def reread(blob: bytes):
    """`process=False` обязателен: иначе trimesh сольёт вершины уже на чтении,
    и сравнение перестанет что-либо доказывать."""
    return trimesh.load(io.BytesIO(blob), file_type="glb", force="mesh",
                        process=False)


SHAPES = {
    "коробка": box(0, 10, 0, 20, 0, 30),
    "цилиндр": cyl_z(15, 0, 6, n=64),
    "шар": sphere(16, n=48),
    "разность": box(0, 20, 0, 20, 0, 20) - cyl_z(10, -1, 21, n=48),
}


# --------------------------------------------------------------------- главное
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_geometry_survives_the_round_trip_exactly(name):
    solid = SHAPES[name]
    verts, faces = vertices_faces(solid.manifold)
    back = reread(solid_to_glb(solid, name=name))

    assert np.array_equal(np.asarray(back.vertices, dtype=np.float32),
                          verts.astype(np.float32)), "вершины разошлись"
    assert np.array_equal(np.asarray(back.faces), faces), "треугольники разошлись"


def test_vertices_are_not_merged():
    """Именной замок на причину, по которой писатель не ходит через trimesh:
    `Trimesh(vertices, faces)` по умолчанию сливает совпадающие вершины, а в
    окне человек мерит, и показанное обязано совпадать с измеренным.

    Меш нарочно вырожденный: два треугольника с ОДИНАКОВЫМИ координатами, но
    разными номерами вершин. Обработка trimesh схлопнула бы их в три вершины.
    """
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0],
                      [0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)

    assert len(trimesh.Trimesh(vertices=verts, faces=faces,
                               process=True).vertices) == 3, "проба негодна"

    back = reread(mesh_to_glb(verts, faces))
    assert len(back.vertices) == 6, "писатель слил вершины"
    assert np.array_equal(np.asarray(back.faces), faces)


# ------------------------------------------------------------------ контейнер
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_buffer_views_are_aligned(name):
    """Смещения видов обязаны делиться на 4, иначе float32 лягут невыровненно
    и строгий загрузчик откажется читать файл."""
    gltf, binary = unpack(solid_to_glb(SHAPES[name]))
    for view in gltf["bufferViews"]:
        assert view["byteOffset"] % 4 == 0, view
        assert view["byteOffset"] + view["byteLength"] <= len(binary)
    assert gltf["buffers"][0]["byteLength"] == len(binary)


def test_position_accessor_declares_bounds():
    """Спецификация требует min/max у POSITION: по ним загрузчик строит габарит,
    не читая координаты."""
    gltf, _ = unpack(solid_to_glb(box(0, 10, 0, 20, 0, 30)))
    position = gltf["accessors"][gltf["meshes"][0]["primitives"][0]
                                 ["attributes"]["POSITION"]]
    assert position["min"] == [0.0, 0.0, 0.0]
    assert position["max"] == [10.0, 20.0, 30.0]


def test_no_normals_are_written():
    """Не экономия, а решение: спецификация предписывает при отсутствии NORMAL
    считать нормали плоскими, а GLTFLoader three.js прямо ставит
    flatShading = true. Плоская заливка — ровно то, как выглядит деталь CAD."""
    gltf, _ = unpack(solid_to_glb(SHAPES["шар"]))
    assert "NORMAL" not in gltf["meshes"][0]["primitives"][0]["attributes"]


@pytest.mark.parametrize("count, component, label", [
    (0xFFFF, 5123, "uint16"),
    (0x10000, 5125, "uint32"),
])
def test_index_width_switches_at_the_boundary(count, component, label):
    """Порог — ровно 0xFFFF вершин. На нём наибольший индекс равен 0xFFFE, и
    значение 0xFFFF в буфере не появляется: в некоторых конвейерах оно означает
    разрыв полосы примитивов."""
    verts = np.zeros((count, 3), dtype=np.float32)
    verts[:, 0] = np.arange(count)
    gltf, binary = unpack(mesh_to_glb(verts, np.array([[0, 1, 2]], dtype=np.int64)))
    assert gltf["accessors"][0]["componentType"] == component, label

    indices = np.frombuffer(binary[:6 if component == 5123 else 12],
                            dtype=np.uint16 if component == 5123 else np.uint32)
    assert indices.tolist() == [0, 1, 2]


@pytest.mark.parametrize("count, width", [(1000, 2), (0x10000, 4)])
def test_indices_occupy_the_declared_width(count, width):
    """Прямое измерение: на одно и то же число треугольников узкий тип обязан
    занять ровно вдвое меньше байт. Сравнивать файлы целиком бессмысленно —
    у них разное число вершин."""
    verts = np.zeros((count, 3), dtype=np.float32)
    verts[:, 0] = np.arange(count)
    faces = np.arange(999, dtype=np.int64).reshape(333, 3)

    gltf, _ = unpack(mesh_to_glb(verts, faces))
    index_view = gltf["bufferViews"][gltf["accessors"][0]["bufferView"]]
    assert index_view["byteLength"] == faces.size * width


def test_axes_stay_datum_own():
    """Z вверх и миллиметры — датумовские, а не glTF-евские. Разворот в Y-вверх
    означал бы, что в окне мерят в одной системе, а в проверках в другой."""
    back = reread(solid_to_glb(box(0, 10, 0, 20, 0, 30)))
    assert [round(float(v), 3) for v in back.bounds.ravel()] == [
        0.0, 0.0, 0.0, 10.0, 20.0, 30.0]


# ---------------------------------------------------------------------- цвет
def test_colour_is_converted_to_linear():
    """CSS задаёт цвет в sRGB, а baseColorFactor в glTF линейный. Без пересчёта
    деталь выходит заметно светлее заявленного, тем сильнее, чем цвет темнее."""
    gltf, _ = unpack(solid_to_glb(box(0, 1, 0, 1, 0, 1), color="#c9723b"))
    factor = gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    srgb = [0xC9 / 255, 0x72 / 255, 0x3B / 255]
    assert factor[3] == 1.0
    assert all(a < b for a, b in zip(factor[:3], srgb)), "цвет не переведён"
    assert factor[0] == pytest.approx(0.584, abs=0.005)


@pytest.mark.parametrize("value", ["#abc", "abc", "#C9723B", "мусор", "", None])
def test_broken_colour_does_not_break_the_file(value):
    """Цвет приходит из скрипта детали, то есть от человека или агента. Ошибка
    в нём не повод не показать деталь."""
    gltf, _ = unpack(solid_to_glb(box(0, 1, 0, 1, 0, 1), color=value))
    factor = gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert len(factor) == 4 and all(0.0 <= c <= 1.0 for c in factor)


def test_colour_changes_the_address():
    """Отпечаток берётся с самого GLB, поэтому смена цвета даёт другой адрес и
    браузер перечитает файл, а не покажет вчерашний из кеша."""
    a = solid_to_glb(box(0, 1, 0, 1, 0, 1), color="#111111")
    b = solid_to_glb(box(0, 1, 0, 1, 0, 1), color="#eeeeee")
    assert a != b


# --------------------------------------------------------------------- прочее
def test_writing_is_deterministic():
    """Иначе один и тот же результат каждый раз ложился бы в кеш новым файлом."""
    solid = SHAPES["цилиндр"]
    assert solid_to_glb(solid, name="х") == solid_to_glb(solid, name="х")


def test_empty_part_is_not_an_error():
    """Деталь могла целиком уйти в вычитание. В окне это честнее показать
    пустотой, чем отказом."""
    empty = box(0, 1, 0, 1, 0, 1) - box(-1, 2, -1, 2, -1, 2)
    gltf, _ = unpack(solid_to_glb(empty, name="пусто"))
    assert gltf["accessors"][1]["count"] == 0


def test_generator_names_the_writer_version():
    """Версия писателя входит в файл: поправили формат — видно по артефакту."""
    gltf, _ = unpack(solid_to_glb(box(0, 1, 0, 1, 0, 1)))
    assert str(GLB_WRITER_VERSION) in gltf["asset"]["generator"]


def test_part_name_reaches_the_file():
    gltf, _ = unpack(solid_to_glb(box(0, 1, 0, 1, 0, 1), name="крышка"))
    assert gltf["nodes"][0]["name"] == "крышка"
    assert gltf["meshes"][0]["name"] == "крышка"


def test_default_colour_is_used_when_none_given():
    gltf, _ = unpack(solid_to_glb(box(0, 1, 0, 1, 0, 1)))
    same, _ = unpack(solid_to_glb(box(0, 1, 0, 1, 0, 1), color=DEFAULT_COLOR))
    assert (gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
            == same["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"])


# ----------------------------------------------------- публикация в кеш
def test_publishing_is_idempotent(tmp_path):
    """Отпечаток берётся с содержимого, поэтому повторная публикация той же
    детали обязана дать тот же адрес и не заводить второй файл: иначе каждая
    пересборка неизменной детали пухла бы в кеше."""
    from datum.build.cache import BuildCache
    from datum.io.artifacts import publish_glb

    with BuildCache(tmp_path / "кеш") as cache:
        solid = SHAPES["цилиндр"]
        first = publish_glb(cache, solid, name="цилиндр", color="#c9723b")
        second = publish_glb(cache, solid, name="цилиндр", color="#c9723b")
        assert first == second

        path = cache.blob_path(first["digest"], "glb")
        assert path is not None and path.stat().st_size == first["bytes"]
        assert len(list(path.parent.glob("*.glb"))) == 1

        other = publish_glb(cache, solid, name="цилиндр", color="#111111")
        assert other["digest"] != first["digest"], "цвет обязан менять адрес"


def test_published_view_describes_the_part(tmp_path):
    from datum.build.cache import BuildCache
    from datum.io.artifacts import publish_glb

    with BuildCache(tmp_path / "кеш") as cache:
        view = publish_glb(cache, box(0, 10, 0, 20, 0, 30), name="плита")

    assert view["url"] == f"/obj/{view['digest']}.glb"
    assert view["tris"] == 12 and view["verts"] == 8
    assert view["bbox"] == [0.0, 0.0, 0.0, 10.0, 20.0, 30.0]
