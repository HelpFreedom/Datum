"""Чтение 3MF через lib3mf — прямо в массивы numpy.

Почему не `trimesh.load`, как у остальных форматов. Замерено на модели в
1.48 млн треугольников (20 МБ 3MF):

    trimesh:  5.0 с, +2.4 ГБ адресного пространства — строит дерево lxml из
              всех <vertex> и <triangle> объекта, прежде чем его прочесть;
    lib3mf:   1.0 с, +0.18 ГБ.

Изолированному воркеру память дана с запасом над родителем, и 2.4 ГБ на одно
только чтение его исчерпывали: процесс падал SIGSEGV на первом же `malloc`,
которому отказали.

И вторая причина, важнее первой: **trimesh читает файлы Bambu/Orca неверно**.
Там корневая модель — компоненты со ссылками `p:path` на отдельные части
пакета (`3D/Objects/object_12.model`), и trimesh на КАЖДЫЙ такой компонент
приписывает ВСЕ сетки из той части. Три компонента — три копии всего, не на
своих местах: на `LiquidDeathNoAMS.3mf` вышло 78 072 треугольника вместо
26 024, на `ModularRotatingDeskOrganizer` — 390 822 вместо 199 038 (оба числа
lib3mf сверены подсчётом `<triangle>` по компонентам). На файлах без таких
ссылок оба читателя совпадают до 1e-5 мм.

Координаты в 3MF — float32 по устройству lib3mf; для диагноза и ремонта этого
достаточно: сами файлы пишутся слайсерами из float32.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from datum.io.export import _keep_locale

__all__ = ["read_3mf", "read_3mf_objects", "Object3mf", "Mesh3mf", "Read3mfError",
           "UNIT_MM", "PART_ROLES"]

#: Единица модели 3MF → миллиметры (порядок — `lib3mf.ModelUnit`).
UNIT_MM = {0: 1e-3, 1: 1.0, 2: 10.0, 3: 25.4, 4: 304.8, 5: 1000.0}

#: Глубина вложенности компонентов. В корректном файле циклов нет, но файл —
#: чужой, и рекурсия без предела на нём — ещё один способ уронить процесс.
MAX_DEPTH = 32


class Read3mfError(ValueError):
    """3MF не прочитался: битый пакет, нет сеток, неверные ссылки."""


def _matrix(transform) -> np.ndarray:
    """Преобразование 3MF (4×3, строка-вектор: p' = p·M + t) как 4×4."""
    fields = np.array([[transform.Fields[i][j] for j in range(3)] for i in range(4)],
                      dtype=np.float64)
    out = np.eye(4)
    out[:3, :3] = fields[:3]
    out[3, :3] = fields[3]
    return out


def _arrays(lib3mf, mesh) -> tuple[np.ndarray, np.ndarray]:
    """Вершины и треугольники сетки одним куском, без поштучных объектов.

    Публичные `GetVertices`/`GetTriangleIndices` создают объект ctypes на
    каждую вершину — на миллионе это секунды и сотни мегабайт. Структуры же
    упакованы плотно (`float32×3`, `uint32×3`) — ровно раскладка numpy, и
    C-функции заполняют переданный буфер. Зеркало `export._set_geometry`.
    """
    nv, nt = mesh.GetVertexCount(), mesh.GetTriangleCount()
    v = np.empty((nv, 3), dtype=np.float32)
    f = np.empty((nt, 3), dtype=np.uint32)
    if nv == 0 or nt == 0:
        return v, f
    lib = getattr(getattr(mesh, "_wrapper", None), "lib", None)
    get_v = getattr(lib, "lib3mf_meshobject_getvertices", None)
    get_t = getattr(lib, "lib3mf_meshobject_gettriangleindices", None)
    if get_v is None or get_t is None or ctypes.sizeof(lib3mf.Position) != 12 \
            or ctypes.sizeof(lib3mf.Triangle) != 12:            # pragma: no cover
        v[:] = [tuple(p.Coordinates) for p in mesh.GetVertices()]
        f[:] = [tuple(t.Indices) for t in mesh.GetTriangleIndices()]
        return v, f
    needed = ctypes.c_uint64()
    check = mesh._wrapper.checkError
    check(mesh, get_v(mesh._handle, ctypes.c_uint64(nv), needed,
                      (lib3mf.Position * nv).from_buffer(v)))
    check(mesh, get_t(mesh._handle, ctypes.c_uint64(nt), needed,
                      (lib3mf.Triangle * nt).from_buffer(f)))
    return v, f


#: Тип части в разметке слайсеров Bambu/Orca/Elegoo (`Metadata/model_settings.config`,
#: атрибут `subtype` у `<part>`) → роль в геометрии. Сверено с ElegooSlicer
#: 1.5 консольным режимом: обычная часть — модель; вычитаемая — вырезается из
#: модели при нарезке (замерено по пластику со 100 % заполнением); модификатор
#: и блокировщики поддержек — только настройки, моделью не считаются.
PART_ROLES = {"normal_part": "model", "negative_part": "negative",
              "modifier_part": "ignored", "support_blocker": "ignored",
              "support_enforcer": "ignored"}
IGNORED_WORDS = {"modifier_part": "модификатор настроек",
                 "support_blocker": "блокировщик поддержек",
                 "support_enforcer": "принудитель поддержек"}

#: Разметку больше этого не читаем: она — несколько килобайт на объект, а файл
#: чужой, и распаковка без предела — ещё один способ занять всю память.
SETTINGS_MAX_BYTES = 64 << 20

SETTINGS = "Metadata/model_settings.config"
ROOT_MODEL = "3D/3dmodel.model"


@dataclass(frozen=True)
class Mesh3mf:
    """Сетка одной части объекта в мировых координатах, мм."""

    vertices: np.ndarray
    faces: np.ndarray


@dataclass(frozen=True)
class Object3mf:
    """Один объект сборки 3MF — то, что слайсер показывает отдельной деталью.

    Части одного объекта остаются вместе (решение владельца: деталь = объект
    файла) и разложены по ролям, как их понимает слайсер: `parts` — модель,
    `negatives` — вычитаемые части (вычитает их `geom.repair.load_objects`:
    модулю ввода булевы ни к чему), модификаторы и блокировщики — только в
    `notes`.
    """

    name: str
    parts: tuple[Mesh3mf, ...]
    negatives: tuple[Mesh3mf, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def vertices(self) -> np.ndarray:
        """Вершины всех частей модели одним массивом (без вычитаемых)."""
        return np.concatenate([p.vertices for p in self.parts])

    @property
    def faces(self) -> np.ndarray:
        offsets = np.cumsum([0] + [len(p.vertices) for p in self.parts[:-1]])
        return np.concatenate([p.faces + k for p, k in zip(self.parts, offsets)])


@dataclass(frozen=True)
class Layout:
    """Разметка слайсера: типы частей, имена объектов, непечатаемые элементы."""

    roles: dict[tuple[int, int], str]        # (объект корня, часть) → subtype
    names: dict[int, str]                    # объект корня → имя
    printable: list[bool] | None             # по элементам <build>, по порядку
    note: str = ""


def _layout(path: str | Path) -> Layout:
    """Прочитать разметку слайсера, если она есть. Нет — пустая: обычный 3MF.

    Номер части в разметке — номер объекта, на который ссылается компонент
    объекта корня. Он уникален в пределах объекта корня, но НЕ во всём файле:
    у Bambu номера в каждой части пакета свои, и копии одной сетки делят номер
    (замерено на настоящих файлах), — поэтому ключ — пара (объект, часть).
    """
    import xml.etree.ElementTree as ET
    import zipfile

    empty = Layout({}, {}, None)
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if SETTINGS not in names:
                return empty
            if z.getinfo(SETTINGS).file_size > SETTINGS_MAX_BYTES:
                return Layout({}, {}, None, "разметка слайсера слишком велика — "
                                            "части взяты как модель")
            config = ET.fromstring(z.read(SETTINGS))
            printable: list[bool] | None = None
            if ROOT_MODEL in names:
                printable = []
                # Потоком: в корневой модели простого 3MF лежат и сами сетки.
                with z.open(ROOT_MODEL) as stream:
                    for _, elem in ET.iterparse(stream, events=("end",)):
                        if elem.tag.endswith("}item") or elem.tag == "item":
                            printable.append(elem.get("printable", "1") != "0")
                        elem.clear()
    except (OSError, zipfile.BadZipFile, ET.ParseError) as e:
        return Layout({}, {}, None, f"разметка слайсера не прочиталась ({type(e).__name__}) — "
                                    f"части взяты как модель")

    roles: dict[tuple[int, int], str] = {}
    titles: dict[int, str] = {}
    for obj in config.iter("object"):
        try:
            oid = int(obj.get("id", ""))
        except ValueError:
            continue
        for meta in obj.findall("metadata"):
            if meta.get("key") == "name" and meta.get("value"):
                titles[oid] = meta.get("value", "").strip()
        for part in obj.findall("part"):
            try:
                roles[(oid, int(part.get("id", "")))] = part.get("subtype", "normal_part")
            except ValueError:
                continue
    return Layout(roles, titles, printable)


def _rels_target(rels_name: str, target: str) -> str:
    """Имя части пакета, на которую указывает связь из `rels_name`.

    Связи файла `dir/_rels/x.rels` относятся к части `dir/x`: относительная
    цель отсчитывается от `dir/`, абсолютная — от корня пакета."""
    import posixpath
    from urllib.parse import unquote

    target = unquote(target.split("#", 1)[0])
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(posixpath.dirname(rels_name))      # снять «_rels/x.rels»
    return posixpath.normpath(posixpath.join(base, target)).lstrip("/")


def _dangling(path: str | Path) -> dict[str, set[str]]:
    """Связи пакета, указывающие на части, которых в нём нет: {файл связей: {Id}}.

    Консольный ElegooSlicer (`--export-3mf` без окна) пишет в `_rels/.rels`
    связи на превью `Metadata/plate_1.png`, а самих картинок не кладёт — им
    нечем рисовать. lib3mf на такой связи отказывает ВЕСЬ файл («Could not
    create OPC Part»), хотя геометрия цела: так не читался ни один файл
    нарезки владельца (Gear Cube, `печать/`) — и проект, который пишет наш
    же `slice`. Имена частей в OPC без учёта регистра."""
    import xml.etree.ElementTree as ET
    import zipfile

    out: dict[str, set[str]] = {}
    try:
        with zipfile.ZipFile(path) as z:
            present = {n.lower() for n in z.namelist()}
            for name in z.namelist():
                if not name.endswith(".rels") or z.getinfo(name).file_size > SETTINGS_MAX_BYTES:
                    continue
                for rel in ET.fromstring(z.read(name)):
                    if rel.get("TargetMode") == "External" or not rel.get("Target"):
                        continue
                    if _rels_target(name, rel.get("Target", "")).lower() not in present:
                        out.setdefault(name, set()).add(rel.get("Id", ""))
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        return {}                         # битый пакет — пусть о нём скажет lib3mf
    return out


def _without_dangling(path: str | Path, dangling: dict[str, set[str]], dest: Path) -> None:
    """Копия пакета без связей на несуществующие части. Остальное — байт в байт."""
    import re
    import shutil
    import zipfile

    with zipfile.ZipFile(path) as src, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            if info.filename in dangling:
                text = src.read(info.filename).decode("utf-8")
                for rid in dangling[info.filename]:
                    # Связь — пустым элементом или парой тегов: обе записи законны.
                    pattern = (r"\s*<Relationship\b[^>]*\bId\s*=\s*[\"']" + re.escape(rid)
                               + r"[\"'][^>]*?(?:/>|>\s*</Relationship\s*>)")
                    text = re.sub(pattern, "", text)
                out.writestr(info, text.encode("utf-8"))
                continue
            with src.open(info) as a, out.open(info, "w") as b:
                shutil.copyfileobj(a, b, 1 << 20)


def read_3mf(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Все части МОДЕЛИ сборки 3MF в мировых координатах, мм: (вершины, треугольники).

    Берётся то, что стоит в сборке (`<build>`), с преобразованиями элементов и
    компонентов. Модификаторы и блокировщики не берутся; вычитаемые части
    здесь НЕ вычтены — это булева операция, её делает
    `geom.repair.load_objects` (и через него `load_mesh`).
    """
    objects, _ = read_3mf_objects(path)
    offsets = np.cumsum([0] + [len(o.vertices) for o in objects[:-1]])
    return (np.concatenate([o.vertices for o in objects]),
            np.concatenate([o.faces + k for o, k in zip(objects, offsets)]))


def read_3mf_objects(path: str | Path) -> tuple[list[Object3mf], list[str]]:
    """Объекты сборки 3MF — как их понимает слайсер — и замечания о файле.

    Объект — элемент `<build>` (объект на столе слайсера); его компоненты с их
    преобразованиями — части объекта. Если файл написан слайсером Bambu/Orca/
    Elegoo, роль части и имя объекта берутся из его разметки (`_layout`), а
    элементы, помеченные непечатаемыми, пропускаются, — как при нарезке.
    Положение части — `transform` компонента: его слайсер и применяет
    (поле `matrix` в разметке на геометрию не влияет — проверено опытом).
    """
    import lib3mf

    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    reader = model.QueryReader("3mf")
    # Строгий режим отказывает на неизвестных метаданных и атрибутах — а их
    # пишет каждый слайсер. Геометрию это не трогает.
    reader.SetStrictModeActive(False)
    dangling = _dangling(path)
    try:
        if dangling:
            import tempfile

            with tempfile.TemporaryDirectory(prefix="datum-3mf-") as tmp:
                clean = Path(tmp) / "clean.3mf"          # ASCII: путь уходит в C как байты
                _without_dangling(path, dangling, clean)
                with _keep_locale():
                    reader.ReadFromFile(str(clean))
        else:
            with _keep_locale():
                reader.ReadFromFile(str(path))
    except Read3mfError:
        raise
    except Exception as e:
        raise Read3mfError(f"{Path(path).name}: 3MF не читается — {e}") from e

    layout = _layout(path)
    file_notes = [layout.note] if layout.note else []
    if dangling:
        n = sum(len(ids) for ids in dangling.values())
        file_notes.append(f"в пакете {n} связ. на отсутствующие части (так пишет превью "
                          f"консольный слайсер) — пропущены, геометрия не затронута")
    scale = UNIT_MM.get(int(model.GetUnit()), 1.0)
    # Части текущего объекта по ролям; заводятся заново на каждый элемент сборки.
    found: dict[str, list[Mesh3mf]] = {}
    unknown: set[str] = set()

    def walk(obj, placed: np.ndarray, depth: int, role: str) -> None:
        if depth > MAX_DEPTH:
            raise Read3mfError(f"{Path(path).name}: компоненты вложены глубже "
                               f"{MAX_DEPTH} — похоже на цикл ссылок")
        if obj.IsMeshObject():
            v, f = _arrays(lib3mf, obj)
            if len(f) == 0:
                return
            world = np.c_[v.astype(np.float64), np.ones(len(v))] @ placed
            faces = f.astype(np.int64)
            # Зеркало (определитель < 0) выворачивает обход треугольников:
            # слайсер пишет так отражённую копию (левая и правая пара), и без
            # обращения она читалась вывернутой — пара сливалась в объём 0, а
            # диагноз видел «запаянные полости» (найдено обзором).
            if np.linalg.det(placed[:3, :3]) < 0:
                faces = faces[:, ::-1]
            found.setdefault(role, []).append(
                Mesh3mf(vertices=world[:, :3] * scale, faces=faces))
        elif obj.IsComponentsObject():
            root_id = int(obj.GetModelResourceID())
            for i in range(obj.GetComponentCount()):
                component = obj.GetComponent(i)
                child = component.GetObjectResource()
                # Роль задаёт разметка у частей ОБЪЕКТА КОРНЯ; глубже — наследуется.
                kind = role
                if depth == 0:
                    kind = layout.roles.get((root_id, int(child.GetModelResourceID())),
                                            "normal_part")
                    if kind not in PART_ROLES:
                        unknown.add(kind)
                        kind = "normal_part"
                local = (_matrix(component.GetTransform()) if component.HasTransform()
                         else np.eye(4))
                # Строка-вектор: сперва своё преобразование, потом родителя.
                walk(child, local @ placed, depth + 1, kind)

    objects: list[Object3mf] = []
    skipped = 0
    items = model.GetBuildItems()
    index = -1
    while items.MoveNext():
        index += 1
        item = items.GetCurrent()
        if layout.printable is not None and index < len(layout.printable) \
                and not layout.printable[index]:
            skipped += 1
            continue
        placed = (_matrix(item.GetObjectTransform()) if item.HasObjectTransform()
                  else np.eye(4))
        resource = item.GetObjectResource()
        found, unknown = {}, set()
        walk(resource, placed, 0, "normal_part")
        parts = found.get("normal_part", [])
        if not parts:
            continue                       # объект без сеток модели — пустой элемент
        notes = []
        for kind, word in IGNORED_WORDS.items():
            if found.get(kind):
                notes.append(f"{word}: частей {len(found[kind])} — не геометрия, пропущено")
        if unknown:
            notes.append(f"неизвестный тип части ({', '.join(sorted(unknown))}) — взят как модель")
        rid = int(resource.GetModelResourceID())
        try:
            own = str(resource.GetName() or "").strip()
        except Exception:                  # pragma: no cover — имя необязательно
            own = ""
        objects.append(Object3mf(name=layout.names.get(rid) or own, parts=tuple(parts),
                                 negatives=tuple(found.get("negative_part", [])),
                                 notes=tuple(notes)))
    if skipped:
        file_notes.append(f"в слайсере помечено непечатаемым — пропущено объектов: {skipped}")

    if not objects:
        raise Read3mfError(f"{Path(path).name}: в сборке 3MF нет ни одной сетки модели")
    return objects, file_notes
