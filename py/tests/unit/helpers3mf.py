"""Сборка файлов 3MF руками — для тестов чтения и импорта.

Файлы собираются из XML прямо здесь, чтобы было видно, что именно в них
лежит: устройство Bambu/Orca (компоненты со ссылками на части пакета), единицы,
объекты стола слайсера.
"""

from __future__ import annotations

import zipfile

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"


def cube_mesh(x0: float, size: float) -> str:
    """Куб 12 треугольников, наружу нормалями, с углом в (x0, 0, 0)."""
    s = size
    pts = [(x0 + a * s, b * s, c * s) for c in (0, 1) for b in (0, 1) for a in (0, 1)]
    tris = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
            (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in pts)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    return f"<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh>"


def package(path, root: str, parts: dict[str, str], extra: dict[str, str] | None = None) -> None:
    """Пакет 3MF: корневая модель, части-модели (со связями) и служебные файлы
    `extra` (без связей — как разметка слайсера в Metadata/)."""
    types = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             '</Types>')
    rels = (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{REL}">'
            f'<Relationship Target="/3D/3dmodel.model" Id="r0" Type="{MODEL_REL}"/>'
            f'</Relationships>')
    part_rels = (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{REL}">'
                 + "".join(f'<Relationship Target="/{name}" Id="p{i}" Type="{MODEL_REL}"/>'
                           for i, name in enumerate(parts))
                 + "</Relationships>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", root)
        if parts:
            z.writestr("3D/_rels/3dmodel.model.rels", part_rels)
        for name, body in parts.items():
            z.writestr(name, body)
        for name, body in (extra or {}).items():
            z.writestr(name, body)


def model(body: str, *, unit: str = "millimeter", build: str = "") -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<model unit="{unit}" xml:lang="en-US" xmlns="{CORE}" xmlns:p="{PROD}" '
            f'requiredextensions="p"><resources>{body}</resources>'
            f'<build>{build}</build></model>')


def plate_of_three(path) -> None:
    """Стол слайсера: «Кубик» 10 мм, «Пирамидка» — объект из ДВУХ частей
    (компоненты), и безымянный куб 4 мм; у каждого элемента сборки свой сдвиг."""
    root = model(
        f'<object id="1" type="model" name="Кубик">{cube_mesh(0, 10)}</object>'
        f'<object id="2" type="model">{cube_mesh(0, 6)}</object>'
        f'<object id="3" type="model">{cube_mesh(0, 3)}</object>'
        '<object id="5" type="model" name="Пирамидка"><components>'
        '<component objectid="2"/>'
        '<component objectid="3" transform="1 0 0 0 1 0 0 0 1 0 0 6"/>'
        '</components></object>'
        f'<object id="6" type="model">{cube_mesh(0, 4)}</object>',
        build='<item objectid="1"/>'
              '<item objectid="5" transform="1 0 0 0 1 0 0 0 1 30 0 0"/>'
              '<item objectid="6" transform="1 0 0 0 1 0 0 0 1 60 0 0"/>')
    package(path, root, {})


def box_mesh(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> str:
    """Коробка по границам — 12 треугольников, наружу нормалями."""
    pts = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    tris = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
            (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in pts)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    return f"<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh>"


def slicer_project(path, objects) -> None:
    """3MF, как его пишут Bambu/Orca/Elegoo: сетки частей — в части пакета
    `3D/Objects/object_<n>.model`, объект корня — из компонентов со ссылками,
    типы частей и имена — в `Metadata/model_settings.config`.

    objects — список словарей: {"name": str, "at": (x, y), "printable": bool,
    "parts": [(subtype, mesh_xml), …]}. Номера частей — уникальные по файлу,
    как пишет сам слайсер.
    """
    root_objects, items, config = [], [], []
    files: dict[str, str] = {}
    next_id = 1
    for n, obj in enumerate(objects, 1):
        part_file = f"3D/Objects/object_{n}.model"
        meshes, comps, parts = [], [], []
        for subtype, mesh in obj["parts"]:
            pid = next_id
            next_id += 1
            meshes.append(f'<object id="{pid}" type="model">{mesh}</object>')
            comps.append(f'<component p:path="/{part_file}" objectid="{pid}"/>')
            parts.append(f'<part id="{pid}" subtype="{subtype}">'
                         f'<metadata key="name" value="{subtype}-{pid}"/></part>')
        files[part_file] = model("".join(meshes))
        oid = next_id
        next_id += 1
        root_objects.append(f'<object id="{oid}" type="model"><components>'
                            + "".join(comps) + "</components></object>")
        x, y = obj.get("at", (0, 0))
        printable = "" if obj.get("printable", True) else ' printable="0"'
        items.append(f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 {x} {y} 0"{printable}/>')
        config.append(f'<object id="{oid}"><metadata key="name" value="{obj["name"]}"/>'
                      + "".join(parts) + "</object>")
    root = model("".join(root_objects), build="".join(items))
    settings = ('<?xml version="1.0" encoding="UTF-8"?><config>' + "".join(config) + "</config>")
    package(path, root, files, {"Metadata/model_settings.config": settings})
