"""Запись детали в GLB — то, что уезжает в окно.

ПОЧЕМУ НЕ ЧЕРЕЗ trimesh. `trimesh.Trimesh(vertices, faces)` по умолчанию
сливает совпадающие вершины и выбрасывает вырожденные грани. Для просмотра это
безобидно, а здесь — нет: в окне человек МЕРЯЕТ, и показанное обязано совпадать
с тем, по чему считался отпечаток и работали проверки. Поэтому массивы берутся
из `vertices_faces()` — той же канонической дороги, что и у `Solid.digest()`, —
и кладутся в файл как есть.

НОРМАЛЕЙ НЕТ, и это не экономия на спичках. Спецификация glTF предписывает при
отсутствии `NORMAL` считать нормали плоскими, а загрузчик three.js на это прямо
ставит `flatShading = true` (проверено в исходнике GLTFLoader 0.186). Плоская
заливка — ровно то, как должна выглядеть деталь CAD: у коробки не должно быть
скруглённых рёбер. Заодно файл выходит втрое легче, потому что гладкие нормали
потребовали бы расщепить вершины по граням.

СИСТЕМА КООРДИНАТ — ДАТУМОВСКАЯ, А НЕ glTF. Ось Z вверх (как при печати),
единица — миллиметр. glTF по соглашению ждёт Y вверх и метры, но это наш
внутренний артефакт кеша, который читает наш же вьюпорт, и разворачивать
координаты значило бы мерить в окне в одной системе, а в проверках — в другой.
Наружу деталь уходит операцией `export` (STL/3MF), где Z вверх и миллиметры —
и есть то, чего ждёт слайсер. Если открыть такой GLB сторонним просмотрщиком,
деталь будет лежать на боку и казаться огромной: это не ошибка, а цена решения.

Замерено на самой тяжёлой сборке корпуса (`mech_arm.3mf`, 1.27 млн
треугольников, 12 тел): 33 мс на запись, 22.4 МБ — против 40 МБ и 0.2 с,
заложенных в план. Запасной сырой формат не понадобился.
"""

from __future__ import annotations

import json
import math
import struct

import numpy as np

__all__ = ["solid_to_glb", "mesh_to_glb", "links_to_glb", "GLB_WRITER_VERSION",
           "DEFAULT_COLOR"]

#: Версия писателя. Входит в ключ кеша артефактов: поправили формат — старые
#: файлы перестают подходить, и никто не увидит вчерашнюю геометрию.
GLB_WRITER_VERSION = 1

DEFAULT_COLOR = "#9aa0a6"

_MAGIC = 0x46546C67            # "glTF"
_JSON_CHUNK = 0x4E4F534A       # "JSON"
_BIN_CHUNK = 0x004E4942        # "BIN\0"

_FLOAT32 = 5126
_UINT16 = 5123
_UINT32 = 5125
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963


def _srgb_to_linear(channel: float) -> float:
    """CSS-цвет задан в sRGB, а `baseColorFactor` в glTF — линейный.

    Без пересчёта деталь в окне выходит заметно светлее и блёклее заявленного
    цвета, причём тем сильнее, чем цвет темнее.
    """
    return (channel / 12.92 if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4)


def _color_factor(color: str | None) -> list[float]:
    value = (color or DEFAULT_COLOR).strip().lstrip("#")
    if len(value) == 3:                       # краткая запись #abc
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        value = DEFAULT_COLOR.lstrip("#")
    try:
        rgb = [int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        rgb = [int(DEFAULT_COLOR.lstrip("#")[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return [round(_srgb_to_linear(c), 6) for c in rgb] + [1.0]


def _pad(length: int, size: int = 4) -> int:
    return (-length) % size


def mesh_to_glb(verts, faces, *, color: str | None = None, name: str = "",
                vertex_colors=None) -> bytes:
    """GLB из вершин (N×3) и треугольников (M×3).

    `vertex_colors` — цвет каждой вершины (N×3, sRGB 0…255): карта поля (напряжения
    звена) на самой детали. Пишется атрибутом COLOR_0 в ЛИНЕЙНОМ пространстве, как
    требует glTF, а основной цвет материала тогда белый — иначе он умножался бы на карту.

    Пустая геометрия — не ошибка: деталь могла целиком уйти в вычитание, и в
    окне это честнее показать пустотой, чем отказом.
    """
    positions = np.ascontiguousarray(np.asarray(verts, dtype=np.float32)[:, :3])
    triangles = np.asarray(faces, dtype=np.int64)
    if positions.size == 0 or triangles.size == 0:
        positions = np.zeros((0, 3), dtype=np.float32)
        triangles = np.zeros((0, 3), dtype=np.int64)

    # Индексы поуже, когда вершин мало: они занимают вдвое больше места, чем
    # координаты (три индекса на треугольник против трёх чисел на вершину),
    # и на обычной детали это половина файла.
    #
    # Порог — 0xFFFF вершин, а не 0x10000, хотя влезло бы и столько. При
    # 0xFFFF вершинах наибольший индекс равен 0xFFFE, то есть значение 0xFFFF
    # в буфере не появляется никогда: в некоторых конвейерах оно означает
    # разрыв полосы примитивов. Одна потерянная вершина запаса дешевле разбора
    # такой поломки.
    if len(positions) <= 0xFFFF:
        index_type, indices = _UINT16, triangles.astype(np.uint16).reshape(-1)
    else:
        index_type, indices = _UINT32, triangles.astype(np.uint32).reshape(-1)

    chunks: list[bytes] = []
    views: list[dict] = []
    offset = 0

    def add_view(data: bytes, target: int) -> int:
        nonlocal offset
        views.append({"buffer": 0, "byteOffset": offset,
                      "byteLength": len(data), "target": target})
        chunks.append(data)
        offset += len(data)
        # Смещение следующего вида обязано делиться на 4: иначе float32 лягут
        # невыровненно и строгие загрузчики откажутся читать.
        gap = _pad(offset)
        if gap:
            chunks.append(b"\0" * gap)
            offset += gap
        return len(views) - 1

    index_view = add_view(indices.tobytes(), _ELEMENT_ARRAY_BUFFER)
    position_view = add_view(positions.tobytes(), _ARRAY_BUFFER)
    colour_view = None
    if vertex_colors is not None and len(positions):
        vc = np.asarray(vertex_colors, float)
        if vc.shape != (len(positions), 3):
            raise ValueError(f"цветов вершин {vc.shape}, а вершин {len(positions)}")
        srgb = np.clip(vc / 255.0, 0.0, 1.0)
        linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
        colour_view = add_view(np.ascontiguousarray(linear, dtype=np.float32).tobytes(),
                               _ARRAY_BUFFER)

    lo = positions.min(axis=0).tolist() if len(positions) else [0.0, 0.0, 0.0]
    hi = positions.max(axis=0).tolist() if len(positions) else [0.0, 0.0, 0.0]
    accessors = [
        {"bufferView": index_view, "componentType": index_type,
         "count": int(indices.size), "type": "SCALAR"},
        # min/max для POSITION спецификация требует: по ним загрузчик строит
        # габарит, не читая сами координаты.
        {"bufferView": position_view, "componentType": _FLOAT32,
         "count": int(len(positions)), "type": "VEC3", "min": lo, "max": hi},
    ]
    attributes = {"POSITION": 1}
    if colour_view is not None:
        accessors.append({"bufferView": colour_view, "componentType": _FLOAT32,
                          "count": int(len(positions)), "type": "VEC3"})
        attributes["COLOR_0"] = 2

    label = name or "деталь"
    gltf = {
        "asset": {"version": "2.0", "generator": f"Datum GLB {GLB_WRITER_VERSION}"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": label}],
        "meshes": [{"name": label, "primitives": [
            {"attributes": attributes, "indices": 0, "material": 0}]}],
        "materials": [{
            "name": label,
            "pbrMetallicRoughness": {
                "baseColorFactor": ([1.0, 1.0, 1.0, 1.0] if colour_view is not None
                                    else _color_factor(color)),
                "metallicFactor": 0.0,
                "roughnessFactor": 0.75,
            },
            # Односторонний намеренно: тела замкнуты и намотаны правильно, а
            # отсечение задних граней даёт заглянуть внутрь при сечении.
            "doubleSided": False,
        }],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": offset}],
    }

    payload = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload += b" " * _pad(len(payload))      # JSON добивается ПРОБЕЛАМИ
    binary = b"".join(chunks)                 # BIN — нулями, это уже сделано выше

    total = 12 + 8 + len(payload) + 8 + len(binary)
    return b"".join([
        struct.pack("<III", _MAGIC, 2, total),
        struct.pack("<II", len(payload), _JSON_CHUNK), payload,
        struct.pack("<II", len(binary), _BIN_CHUNK), binary,
    ])


def solid_to_glb(solid, *, color: str | None = None, name: str = "") -> bytes:
    """GLB из `Solid`. Массивы берутся канонической дорогой — той же, по
    которой считается отпечаток."""
    from datum.geom.convert import vertices_faces

    verts, faces = vertices_faces(solid.manifold)
    return mesh_to_glb(verts, faces, color=color, name=name or solid.name)


# ====================================================== механизм с движением
def links_to_glb(links, *, times=None, poses=None, name: str = "",
                 animation: str = "") -> bytes:
    """GLB механизма ДЛЯ ЧУЖИХ программ (Kadr, Blender, three.js): звенья узлами
    и, если даны кадры, анимация их положений.

    В отличие от GLB окна (`mesh_to_glb`: Z вверх, миллиметры) этот — по
    стандарту glTF: **Y вверх, метры**. Перевод делает корневой узел (поворот
    −90° вокруг X и масштаб 0.001), а сетки и кадры под ним остаются в
    координатах Datum — числа в файле те же, что в проекте.

    `links` — [(имя, вершины мм, треугольники, цвет)], сетка звена — в сцене при
    нуле. `times` — секунды кадров; `poses[кадр][звено]` — [x, y, z, w, qx, qy, qz]
    (мм и кватернион, как в записи проигрывателя). Нормалей нет, как и в GLB
    окна: по спецификации glTF это указание считать заливку плоской.
    """
    chunks: list[bytes] = []
    views: list[dict] = []
    accessors: list[dict] = []
    offset = 0

    def add(data: np.ndarray, kind: str, component: int, *, target: int | None = None,
            bounds: bool = False) -> int:
        nonlocal offset
        raw = np.ascontiguousarray(data).tobytes()
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target is not None:
            view["target"] = target
        views.append(view)
        chunks.append(raw)
        offset += len(raw)
        gap = _pad(offset)
        if gap:
            chunks.append(b"\0" * gap)
            offset += gap
        accessor = {"bufferView": len(views) - 1, "componentType": component,
                    "count": int(len(data)),
                    "type": kind}
        if bounds and len(data):
            accessor["min"] = np.atleast_1d(data.min(axis=0)).astype(float).tolist()
            accessor["max"] = np.atleast_1d(data.max(axis=0)).astype(float).tolist()
        accessors.append(accessor)
        return len(accessors) - 1

    nodes: list[dict] = [{"name": name or "механизм",
                          # Z вверх → Y вверх: (x, y, z) → (x, z, −y)
                          "rotation": [-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)],
                          "scale": [0.001, 0.001, 0.001], "children": []}]
    meshes, materials = [], []
    for label, verts, faces, color in links:
        positions = np.asarray(verts, dtype=np.float32).reshape(-1, 3)
        triangles = np.asarray(faces, dtype=np.int64).reshape(-1)
        if len(positions) <= 0xFFFF:                    # см. `mesh_to_glb`
            index = add(triangles.astype(np.uint16), "SCALAR", _UINT16,
                        target=_ELEMENT_ARRAY_BUFFER)
        else:
            index = add(triangles.astype(np.uint32), "SCALAR", _UINT32,
                        target=_ELEMENT_ARRAY_BUFFER)
        position = add(positions, "VEC3", _FLOAT32, target=_ARRAY_BUFFER, bounds=True)
        materials.append({"name": label, "doubleSided": False,
                          "pbrMetallicRoughness": {"baseColorFactor": _color_factor(color),
                                                   "metallicFactor": 0.0,
                                                   "roughnessFactor": 0.75}})
        meshes.append({"name": label, "primitives": [
            {"attributes": {"POSITION": position}, "indices": index,
             "material": len(materials) - 1}]})
        nodes[0]["children"].append(len(nodes))
        nodes.append({"name": label, "mesh": len(meshes) - 1})

    gltf: dict = {
        "asset": {"version": "2.0", "generator": f"Datum GLB {GLB_WRITER_VERSION}"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes, "meshes": meshes,
        "materials": materials,
    }
    if times is not None and poses is not None and len(times) > 1:
        t = np.asarray(times, dtype=np.float32)
        track = np.asarray(poses, dtype=float)                 # кадры × звенья × 7
        if track.shape != (len(t), len(links), 7):
            raise ValueError(f"кадры формы {track.shape}, а ждали "
                             f"({len(t)}, {len(links)}, 7)")
        if not (np.diff(t) > 0).all():
            raise ValueError("время кадров обязано строго расти")
        clock = add(t, "SCALAR", _FLOAT32, bounds=True)
        samplers, channels = [], []
        for k in range(len(links)):
            move = track[:, k, 0:3]
            quat = track[:, k, [4, 5, 6, 3]].copy()            # w x y z → x y z w
            quat /= np.linalg.norm(quat, axis=1, keepdims=True)
            # q и −q — один поворот, но интерполяция между ними идёт дальней
            # дорогой: знак выравнивается по предыдущему кадру.
            for i in range(1, len(quat)):
                if float(quat[i] @ quat[i - 1]) < 0:
                    quat[i] = -quat[i]
            node = k + 1
            nodes[node]["translation"] = move[0].tolist()
            nodes[node]["rotation"] = quat[0].tolist()
            if np.allclose(move, move[0], atol=1e-6) and np.allclose(quat, quat[0], atol=1e-9):
                continue                                        # звено стоит — дорожки не нужно
            for path, data, kind in (("translation", move, "VEC3"), ("rotation", quat, "VEC4")):
                samplers.append({"input": clock, "interpolation": "LINEAR",
                                 "output": add(data.astype(np.float32), kind, _FLOAT32)})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": node, "path": path}})
        if channels:
            gltf["animations"] = [{"name": animation or "движение", "samplers": samplers,
                                   "channels": channels}]
    gltf["accessors"] = accessors
    gltf["bufferViews"] = views
    gltf["buffers"] = [{"byteLength": offset}]

    payload = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload += b" " * _pad(len(payload))
    binary = b"".join(chunks)
    total = 12 + 8 + len(payload) + 8 + len(binary)
    return b"".join([
        struct.pack("<III", _MAGIC, 2, total),
        struct.pack("<II", len(payload), _JSON_CHUNK), payload,
        struct.pack("<II", len(binary), _BIN_CHUNK), binary,
    ])
